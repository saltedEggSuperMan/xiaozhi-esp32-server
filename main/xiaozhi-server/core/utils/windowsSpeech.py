"""Offline Windows SAPI synthesis; COM objects stay on one dedicated thread."""

import atexit
import io
import queue
import sys
import threading
import wave
from concurrent.futures import Future


class WindowsSpeechWorker:
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("windows_local TTS 需要 Windows 和本机离线语音库")
        self.jobs = queue.Queue(maxsize=8)
        self.thread = threading.Thread(target=self._run, name="windows-local-tts", daemon=True)
        self.thread.start()

    def synthesize(self, text, voice, rate=0, volume=90, timeout=15):
        future = Future()
        self.jobs.put((future, str(text), voice, int(rate), int(volume)), timeout=timeout)
        return future.result(timeout=timeout)

    def close(self):
        try:
            self.jobs.put_nowait(None)
        except queue.Full:
            return
        self.thread.join(timeout=2)

    def _run(self):
        synth = stream = fmt = tokens = token = None
        initialized = False
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            initialized = True
            synth = win32com.client.Dispatch("SAPI.SpVoice")
            while True:
                job = self.jobs.get()
                if job is None:
                    break
                future, text, name, rate, volume = job
                try:
                    tokens = synth.GetVoices()
                    chosen = None
                    for index in range(tokens.Count):
                        token = tokens.Item(index)
                        if name.casefold() in token.GetDescription().casefold():
                            chosen = token
                            break
                    if chosen is None:
                        raise RuntimeError("未找到指定的 Windows 离线音色，请检查已安装的语音库")
                    synth.Voice = chosen
                    chosen = None
                    synth.Rate = max(-10, min(10, rate))
                    synth.Volume = max(0, min(100, volume))
                    stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
                    stream.Format.Type = 22  # SAPI 22.05 kHz, signed 16-bit mono.
                    synth.AudioOutputStream = stream
                    synth.Speak(text, 16)  # SVSFIsNotXML: user text is always plain text.
                    fmt = stream.Format.GetWaveFormatEx()
                    pcm = bytes(stream.GetData())
                    buffer = io.BytesIO()
                    with wave.open(buffer, "wb") as wav:
                        wav.setnchannels(fmt.Channels)
                        wav.setsampwidth(fmt.BitsPerSample // 8)
                        wav.setframerate(fmt.SamplesPerSec)
                        wav.writeframes(pcm)
                    future.set_result(buffer.getvalue())
                except Exception as exc:
                    # Avoid including the user's text in errors.
                    future.set_exception(RuntimeError(f"Windows 本地语音合成失败 ({type(exc).__name__})"))
                finally:
                    fmt = stream = tokens = token = None
        except Exception as exc:
            # Fail queued requests explicitly, rather than hanging on missing COM dependencies.
            while True:
                job = self.jobs.get()
                if job is None:
                    break
                job[0].set_exception(RuntimeError(f"Windows 离线语音引擎不可用 ({type(exc).__name__})"))
        finally:
            synth = stream = fmt = tokens = token = None
            if initialized:
                pythoncom.CoUninitialize()


_worker = None
_lock = threading.Lock()


def synthesize_wav(text, voice="Microsoft Huihui Desktop", rate=0, volume=90, timeout=15):
    global _worker
    with _lock:
        if _worker is None:
            _worker = WindowsSpeechWorker()
            atexit.register(_worker.close)
        worker = _worker
    return worker.synthesize(text, voice, rate, volume, timeout)
