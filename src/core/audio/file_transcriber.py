"""
文件转录模块
负责音频/视频文件的转录处理
"""
import os
import json
import time
import threading
import subprocess
import tempfile
from typing import Any, Optional

from src.core.signals import TranscriptionSignals

class FileTranscriber:
    """文件转录器类"""

    def __init__(self, signals: TranscriptionSignals):
        """
        初始化文件转录器

        Args:
            signals: 信号实例
        """
        self.signals = signals
        self.is_transcribing = False
        self.transcription_thread = None
        self.temp_files = []  # 临时文件列表，用于清理
        self.ffmpeg_process = None

    def start_transcription(self, file_path: str, recognizer: Any) -> bool:
        """
        开始文件转录

        Args:
            file_path: 文件路径
            recognizer: 识别器实例

        Returns:
            bool: 开始转录是否成功
        """
        try:
            # 导入日志工具
            try:
                from src.utils.sherpa_logger import sherpa_logger
            except ImportError:
                # 如果导入失败，使用简单的日志记录
                class DummyLogger:
                    def debug(self, msg): print(f"DEBUG: {msg}")
                    def info(self, msg): print(f"INFO: {msg}")
                    def warning(self, msg): print(f"WARNING: {msg}")
                    def error(self, msg): print(f"ERROR: {msg}")
                sherpa_logger = DummyLogger()

            sherpa_logger.info(f"开始文件转录: {file_path}")

            # 检查是否已经在转录
            if self.is_transcribing:
                sherpa_logger.warning("已经在转录中，无法启动新的转录")
                return False

            # 检查文件是否存在
            if not os.path.exists(file_path):
                error_msg = f"文件不存在: {file_path}"
                sherpa_logger.error(error_msg)
                self.signals.error_occurred.emit(error_msg)
                return False

            # 设置转录标志
            self.is_transcribing = True

            # 发送转录开始信号
            if hasattr(self.signals, 'transcription_started'):
                sherpa_logger.debug("发送转录开始信号")
                self.signals.transcription_started.emit()

            # 获取文件信息
            try:
                # 获取文件大小
                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                sherpa_logger.info(f"文件大小: {file_size_mb:.2f}MB")

                # 获取文件时长
                probe = subprocess.run([
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_format',
                    file_path
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                probe_data = json.loads(probe.stdout)
                duration = float(probe_data['format']['duration'])
                sherpa_logger.info(f"文件时长: {duration:.2f}秒")

                # 更新状态
                status_msg = f"文件信息: {os.path.basename(file_path)} ({file_size_mb:.2f}MB, {duration:.2f}秒)"
                sherpa_logger.info(status_msg)
                self.signals.status_updated.emit(status_msg)

                # 创建转录线程
                sherpa_logger.debug("创建转录线程")
                self.transcription_thread = threading.Thread(
                    target=self._transcribe_file_thread,
                    args=(file_path, recognizer, duration),
                    daemon=True
                )

                # 启动线程
                sherpa_logger.debug("启动转录线程")
                self.transcription_thread.start()

                return True

            except Exception as e:
                error_msg = f"获取文件信息错误: {e}"
                sherpa_logger.error(error_msg)
                import traceback
                sherpa_logger.error(traceback.format_exc())
                self.signals.error_occurred.emit(error_msg)
                self.is_transcribing = False

                # 发送转录完成信号（因为出错）
                if hasattr(self.signals, 'transcription_finished'):
                    sherpa_logger.debug("发送转录完成信号（因为出错）")
                    self.signals.transcription_finished.emit()

                return False

        except Exception as e:
            print(f"启动文件转录错误: {e}")
            import traceback
            traceback.print_exc()

            # 尝试发送错误信号
            try:
                self.signals.error_occurred.emit(f"启动文件转录错误: {e}")
            except:
                pass

            # 重置转录标志
            self.is_transcribing = False
            return False

    def stop_transcription(self) -> bool:
        """
        停止文件转录

        Returns:
            bool: 停止转录是否成功
        """
        try:
            # 导入日志工具
            try:
                from src.utils.sherpa_logger import sherpa_logger
            except ImportError:
                # 如果导入失败，使用简单的日志记录
                class DummyLogger:
                    def debug(self, msg): print(f"DEBUG: {msg}")
                    def info(self, msg): print(f"INFO: {msg}")
                    def warning(self, msg): print(f"WARNING: {msg}")
                    def error(self, msg): print(f"ERROR: {msg}")
                sherpa_logger = DummyLogger()

            sherpa_logger.info("停止文件转录")

            # 检查是否正在转录
            if not self.is_transcribing:
                sherpa_logger.warning("没有正在进行的转录，无法停止")
                return False

            # 清除转录标志
            self.is_transcribing = False
            sherpa_logger.debug("转录标志已清除")

            # 终止ffmpeg进程
            if self.ffmpeg_process:
                sherpa_logger.debug("终止ffmpeg进程")
                try:
                    self.ffmpeg_process.terminate()
                    self.ffmpeg_process.wait(timeout=1.0)
                    sherpa_logger.debug("ffmpeg进程已正常终止")
                except:
                    try:
                        sherpa_logger.warning("ffmpeg进程终止超时，强制结束")
                        self.ffmpeg_process.kill()
                    except Exception as e:
                        sherpa_logger.error(f"强制结束ffmpeg进程失败: {e}")
                self.ffmpeg_process = None

            # 等待线程结束
            if self.transcription_thread and self.transcription_thread.is_alive():
                sherpa_logger.debug("等待转录线程结束")
                self.transcription_thread.join(timeout=1.0)
                if self.transcription_thread.is_alive():
                    sherpa_logger.warning("转录线程未在超时时间内结束")
                else:
                    sherpa_logger.debug("转录线程已结束")

            self.transcription_thread = None

            # 清理临时文件
            sherpa_logger.debug("清理临时文件")
            self._cleanup_temp_files()

            # 发送转录完成信号
            if hasattr(self.signals, 'transcription_finished'):
                sherpa_logger.debug("发送转录完成信号")
                self.signals.transcription_finished.emit()

            sherpa_logger.info("文件转录已停止")
            return True

        except Exception as e:
            print(f"停止文件转录错误: {e}")
            import traceback
            traceback.print_exc()

            # 尝试发送错误信号
            try:
                self.signals.error_occurred.emit(f"停止文件转录错误: {e}")
            except:
                pass

            # 确保转录标志被清除
            self.is_transcribing = False
            return False

    def _transcribe_file_thread(self, file_path: str, recognizer: Any, duration: float) -> None:
        """
        文件转录线程

        Args:
            file_path: 文件路径
            recognizer: 识别器实例
            duration: 文件时长(秒)
        """
        try:
            # 导入日志工具
            try:
                from src.utils.sherpa_logger import sherpa_logger
            except ImportError:
                # 如果导入失败，使用简单的日志记录
                class DummyLogger:
                    def debug(self, msg): print(f"DEBUG: {msg}")
                    def info(self, msg): print(f"INFO: {msg}")
                    def warning(self, msg): print(f"WARNING: {msg}")
                    def error(self, msg): print(f"ERROR: {msg}")
                sherpa_logger = DummyLogger()

            sherpa_logger.info(f"文件转录线程开始: {file_path}")

            # 检查是否是 ASRModelManager 实例
            if hasattr(recognizer, 'transcribe_file'):
                sherpa_logger.info("使用 ASRModelManager 的 transcribe_file 方法")
                # 使用 ASRModelManager 的 transcribe_file 方法
                self._transcribe_file_with_manager(file_path, recognizer, duration)
            else:
                sherpa_logger.info("使用传统的 Vosk 方法")
                # 使用传统的 Vosk 方法
                self._transcribe_file_with_vosk(file_path, recognizer, duration)

        except Exception as e:
            error_msg = f"转录过程错误: {e}"
            print(error_msg)
            import traceback
            traceback.print_exc()

            # 尝试发送错误信号
            try:
                self.signals.error_occurred.emit(error_msg)
            except:
                pass

        finally:
            try:
                # 清理临时文件
                self._cleanup_temp_files()

                # 清除转录标志
                self.is_transcribing = False

                # 发送完成信号
                if hasattr(self.signals, 'transcription_finished'):
                    self.signals.transcription_finished.emit()

                print("文件转录线程结束")
            except Exception as e:
                print(f"转录线程清理错误: {e}")
                import traceback
                traceback.print_exc()

    def _transcribe_file_with_manager(self, file_path: str, model_manager: Any, duration: float) -> None:
        """
        使用 ASRModelManager 转录文件

        Args:
            file_path: 文件路径
            model_manager: ASRModelManager 实例
            duration: 文件时长(秒)
        """
        # 导入 Sherpa-ONNX 日志工具
        try:
            from src.utils.sherpa_logger import sherpa_logger
        except ImportError:
            # 如果导入失败，创建一个简单的日志记录器
            class DummyLogger:
                def debug(self, msg): print(f"DEBUG: {msg}")
                def info(self, msg): print(f"INFO: {msg}")
                def warning(self, msg): print(f"WARNING: {msg}")
                def error(self, msg): print(f"ERROR: {msg}")
            sherpa_logger = DummyLogger()

        # 检查 model_manager 的详细信息
        sherpa_logger.info(f"开始使用 ASRModelManager 转录文件: {file_path}")
        sherpa_logger.info(f"文件时长: {duration} 秒")
        sherpa_logger.info(f"model_manager 类型: {type(model_manager)}")

        # 获取模型和引擎信息
        model_type = getattr(model_manager, 'model_type', 'unknown')
        engine_type = model_manager.get_current_engine_type() if hasattr(model_manager, 'get_current_engine_type') else 'unknown'
        engine_info = type(model_manager.current_engine).__name__ if hasattr(model_manager, 'current_engine') and model_manager.current_engine else "None"

        sherpa_logger.info(f"模型类型: {model_type}")
        sherpa_logger.info(f"引擎类型: {engine_type}")
        sherpa_logger.info(f"引擎实例: {engine_info}")

        # 检查 model_manager 是否有 transcribe_file 方法
        if not hasattr(model_manager, 'transcribe_file'):
            error_msg = "model_manager 没有 transcribe_file 方法"
            sherpa_logger.error(error_msg)
            self.signals.error_occurred.emit(error_msg)
            return

        # 更新状态栏和字幕窗口，显示当前使用的模型和引擎
        status_msg = f"使用 {model_type} 模型 (引擎: {engine_type}) 转录文件..."
        self.signals.status_updated.emit(status_msg)

        # 第一阶段：转换为WAV格式（0-20%）
        sherpa_logger.info(f"第一阶段：转换音频格式... (模型: {model_type}, 引擎: {engine_type})")
        self.signals.status_updated.emit(f"第一阶段：转换音频格式... (模型: {model_type})")
        self.signals.progress_updated.emit(10, "转换格式: 10%")

        # 第二阶段：转录文件（20-90%）
        sherpa_logger.info(f"第二阶段：转录文件... (模型: {model_type}, 引擎: {engine_type})")
        self.signals.status_updated.emit(f"第二阶段：转录文件... (模型: {model_type})")
        self.signals.progress_updated.emit(20, "转录中: 20%")

        # 使用 model_manager 的 transcribe_file 方法
        sherpa_logger.info(f"调用 model_manager.transcribe_file({file_path})")
        sherpa_logger.info(f"使用引擎: {engine_info}")
        sherpa_logger.info(f"引擎类型: {engine_type}")
        result = model_manager.transcribe_file(file_path)
        sherpa_logger.info(f"转录结果: {result[:100]}..." if result and len(result) > 100 else f"转录结果: {result}")

        # 第三阶段：处理结果（90-100%）
        sherpa_logger.info(f"第三阶段：处理结果... (模型: {model_type}, 引擎: {engine_type})")
        self.signals.status_updated.emit(f"第三阶段：处理结果... (模型: {model_type})")
        self.signals.progress_updated.emit(90, "处理结果: 90%")

        # 如果有结果，发送到字幕窗口
        if result:
            text = self._format_text(result)
            sherpa_logger.info(f"格式化后的文本: {text[:100]}..." if len(text) > 100 else f"格式化后的文本: {text}")

            # 添加模型和引擎信息到字幕
            header = f"[使用 {model_type} 模型 (引擎: {engine_type}) 转录结果]"
            full_text = f"{header}\n\n{text}"

            self.signals.new_text.emit(full_text)
        else:
            sherpa_logger.warning(f"没有转录结果 (模型: {model_type}, 引擎: {engine_type})")
            # 发送一个提示信息到字幕窗口
            error_text = f"[使用 {model_type} 模型 (引擎: {engine_type}) 转录失败]\n\n没有获取到转录结果。"
            self.signals.new_text.emit(error_text)

        # 转录完成，设置进度为 100%
        self.signals.progress_updated.emit(100, "转录完成 (100%)")
        self.signals.status_updated.emit(f"文件转录完成 (模型: {model_type})")
        sherpa_logger.info(f"文件转录完成 (模型: {model_type}, 引擎: {engine_type})")

    def _transcribe_file_with_vosk(self, file_path: str, recognizer: Any, duration: float) -> None:
        """
        使用 Vosk 转录文件

        Args:
            file_path: 文件路径
            recognizer: Vosk 识别器实例
            duration: 文件时长(秒)
        """
        # 导入 Sherpa-ONNX 日志工具
        try:
            from src.utils.sherpa_logger import sherpa_logger
        except ImportError:
            # 如果导入失败，创建一个简单的日志记录器
            class DummyLogger:
                def debug(self, msg): print(f"DEBUG: {msg}")
                def info(self, msg): print(f"INFO: {msg}")
                def warning(self, msg): print(f"WARNING: {msg}")
                def error(self, msg): print(f"ERROR: {msg}")
            sherpa_logger = DummyLogger()

        # 获取识别器信息
        recognizer_type = type(recognizer).__name__
        engine_type = getattr(recognizer, 'engine_type', 'vosk')

        sherpa_logger.info(f"开始使用 Vosk 转录文件: {file_path}")
        sherpa_logger.info(f"文件时长: {duration} 秒")
        sherpa_logger.info(f"识别器类型: {recognizer_type}")
        sherpa_logger.info(f"引擎类型: {engine_type}")

        # 更新状态栏，显示当前使用的模型和引擎
        status_msg = f"使用 Vosk 模型 (引擎: {engine_type}) 转录文件..."
        self.signals.status_updated.emit(status_msg)

        # 第一阶段：转换为WAV格式（0-20%）
        sherpa_logger.info(f"第一阶段：转换音频格式... (引擎: {engine_type})")
        self.signals.status_updated.emit(f"第一阶段：转换音频格式... (引擎: {engine_type})")
        wav_file = self._convert_to_wav(file_path)
        if not wav_file or not self.is_transcribing:
            sherpa_logger.warning(f"转换WAV格式失败或转录已停止 (引擎: {engine_type})")
            self.signals.transcription_finished.emit()
            return

        # 第二阶段：读取音频数据（20-50%）
        sherpa_logger.info(f"第二阶段：读取音频数据... (引擎: {engine_type})")
        self.signals.status_updated.emit(f"第二阶段：读取音频数据... (引擎: {engine_type})")
        all_chunks = []
        total_bytes = 0
        last_update_time = time.time()

        # 使用 ffmpeg 提取音频
        sherpa_logger.info(f"使用 ffmpeg 提取音频... (引擎: {engine_type})")
        self.ffmpeg_process = subprocess.Popen([
            'ffmpeg',
            '-i', wav_file,
            '-ar', '16000',
            '-ac', '1',
            '-f', 's16le',
            '-'
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # 读取所有音频数据
        sherpa_logger.info(f"开始读取音频数据... (引擎: {engine_type})")
        while self.is_transcribing:
            chunk = self.ffmpeg_process.stdout.read(4000)
            if not chunk:
                break

            all_chunks.append(chunk)
            total_bytes += len(chunk)

            # 更新读取进度（20-50%）
            current_time = time.time()
            if current_time - last_update_time >= 0.2:  # 每0.2秒更新一次
                current_position = total_bytes / (16000 * 2)  # 16kHz, 16-bit
                progress = 20 + min(30, int((current_position / duration) * 30))

                time_str = f"{int(current_position//60):02d}:{int(current_position%60):02d}"
                total_str = f"{int(duration//60):02d}:{int(duration%60):02d}"
                format_text = f"读取中: {time_str} / {total_str} ({progress}%)"

                self.signals.progress_updated.emit(progress, format_text)
                last_update_time = current_time

        # 确保 ffmpeg 进程终止
        sherpa_logger.info(f"音频数据读取完成，终止 ffmpeg 进程... (引擎: {engine_type})")
        if self.ffmpeg_process:
            self.ffmpeg_process.terminate()
            try:
                self.ffmpeg_process.wait(timeout=5)
            except:
                self.ffmpeg_process.kill()
            self.ffmpeg_process = None

        if not self.is_transcribing:
            sherpa_logger.warning(f"转录已停止 (引擎: {engine_type})")
            self.signals.transcription_finished.emit()
            return

        # 第三阶段：处理音频数据（50-99%）
        sherpa_logger.info(f"第三阶段：处理 {len(all_chunks)} 个音频块... (引擎: {engine_type})")
        self.signals.status_updated.emit(f"第三阶段：处理 {len(all_chunks)} 个音频块... (引擎: {engine_type})")
        total_chunks = len(all_chunks)

        # 收集所有部分结果
        all_results = []

        for i, chunk in enumerate(all_chunks):
            if not self.is_transcribing:
                sherpa_logger.warning(f"转录已停止 (引擎: {engine_type})")
                break

            # 处理音频数据
            if recognizer.AcceptWaveform(chunk):
                result = json.loads(recognizer.Result())
                if result.get('text', '').strip():
                    text = result['text'].strip()
                    sherpa_logger.info(f"部分结果: {text[:100]}..." if len(text) > 100 else f"部分结果: {text}")
                    all_results.append(text)

                    # 收集部分结果，但不立即显示，避免频繁更新界面
                    if len(all_results) % 5 == 0:  # 每5个结果更新一次
                        combined_text = " ".join(all_results)
                        formatted_text = self._format_text(combined_text)

                        # 添加模型和引擎信息到字幕
                        header = f"[使用 Vosk 模型 (引擎: {engine_type}) 转录中...]"
                        full_text = f"{header}\n\n{formatted_text}"

                        self.signals.new_text.emit(full_text)

            # 更新处理进度（50-99%）
            current_time = time.time()
            if current_time - last_update_time >= 0.2:
                progress = 50 + min(49, int((i / total_chunks) * 49))
                format_text = f"处理中: {progress}%"
                self.signals.progress_updated.emit(progress, format_text)
                last_update_time = current_time

        # 处理最终结果
        sherpa_logger.info(f"处理最终结果... (引擎: {engine_type})")
        final_result = json.loads(recognizer.FinalResult())
        final_text = final_result.get('text', '').strip()

        if final_text:
            # 将最终结果添加到所有结果中
            all_results.append(final_text)
            sherpa_logger.info(f"最终结果: {final_text[:100]}..." if len(final_text) > 100 else f"最终结果: {final_text}")

        # 合并所有结果
        if all_results:
            # 合并所有部分结果和最终结果
            combined_text = " ".join(all_results)
            formatted_text = self._format_text(combined_text)

            sherpa_logger.info(f"合并结果: {formatted_text[:100]}..." if len(formatted_text) > 100 else f"合并结果: {formatted_text}")

            # 添加模型和引擎信息到字幕
            header = f"[使用 Vosk 模型 (引擎: {engine_type}) 转录结果]"
            full_text = f"{header}\n\n{formatted_text}"

            self.signals.new_text.emit(full_text)

            # 转录完成，设置进度为 100%
            self.signals.progress_updated.emit(100, "转录完成 (100%)")
            self.signals.status_updated.emit(f"文件转录完成 (引擎: {engine_type})")
            sherpa_logger.info(f"文件转录完成 (引擎: {engine_type})")
        else:
            sherpa_logger.warning(f"没有任何转录结果 (引擎: {engine_type})")
            # 发送一个提示信息到字幕窗口
            error_text = f"[使用 Vosk 模型 (引擎: {engine_type}) 转录失败]\n\n没有获取到转录结果。"
            self.signals.new_text.emit(error_text)

            # 转录完成，设置进度为 100%
            self.signals.progress_updated.emit(100, "转录完成 (100%)")
            self.signals.status_updated.emit(f"文件转录完成，但没有结果 (引擎: {engine_type})")
            sherpa_logger.info(f"文件转录完成，但没有结果 (引擎: {engine_type})")

    def _convert_to_wav(self, file_path: str) -> Optional[str]:
        """
        将文件转换为WAV格式

        Args:
            file_path: 原始文件路径

        Returns:
            str: WAV文件路径，如果转换失败则返回None
        """
        try:
            # 创建临时文件
            fd, temp_wav = tempfile.mkstemp(suffix='.wav')
            os.close(fd)
            self.temp_files.append(temp_wav)

            # 使用ffmpeg转换为WAV格式
            self.signals.status_updated.emit(f"正在转换文件格式...")
            self.signals.progress_updated.emit(5, "转换格式: 5%")

            # 使用ffmpeg转换
            self.ffmpeg_process = subprocess.Popen([
                'ffmpeg',
                '-i', file_path,
                '-ar', '16000',  # 采样率16kHz
                '-ac', '1',      # 单声道
                '-y',            # 覆盖已有文件
                temp_wav
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # 等待转换完成
            while self.ffmpeg_process.poll() is None:
                if not self.is_transcribing:
                    self.ffmpeg_process.terminate()
                    try:
                        self.ffmpeg_process.wait(timeout=1.0)
                    except:
                        self.ffmpeg_process.kill()
                    return None
                time.sleep(0.1)

            # 检查转换结果
            if self.ffmpeg_process.returncode != 0:
                stderr = self.ffmpeg_process.stderr.read().decode('utf-8', errors='ignore')
                self.signals.error_occurred.emit(f"转换格式失败: {stderr}")
                return None

            self.signals.progress_updated.emit(20, "转换格式完成: 20%")
            return temp_wav

        except Exception as e:
            self.signals.error_occurred.emit(f"转换格式错误: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _cleanup_temp_files(self) -> None:
        """清理临时文件"""
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception as e:
                print(f"清理临时文件错误: {e}")
        self.temp_files = []

    def _format_text(self, text: str) -> str:
        """
        格式化文本：添加标点、首字母大写等

        Args:
            text: 原始文本

        Returns:
            str: 格式化后的文本
        """
        if not text:
            return text

        # 首字母大写
        text = text[0].upper() + text[1:]

        # 如果文本末尾没有标点符号，添加句号
        if text[-1] not in ['.', '?', '!', ',', ';', ':', '-']:
            text += '.'

        # 处理常见的问句开头
        question_starters = ['what', 'who', 'where', 'when', 'why', 'how', 'is', 'are', 'do', 'does', 'did', 'can', 'could', 'will', 'would']
        words = text.split()
        if words and words[0].lower() in question_starters:
            # 将句尾的句号替换为问号
            if text[-1] == '.':
                text = text[:-1] + '?'

        return text
