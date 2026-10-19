"""Optional, bounded PC speaker output. Hardware I/O stays off the asyncio loop."""

import asyncio
import math
import queue
import threading
import time


_speaker_lock = threading.Lock()
TAG = __name__


def audio_output_settings(config):
    settings = config.get("audio_output", {})
    if not isinstance(settings, dict):
        raise ValueError("audio_output 必须是配置对象")
    mode = settings.get("mode", "device")
    if mode not in ("device", "server_pc"):
        raise ValueError("audio_output.mode 只能是 device 或 server_pc")
    pc = settings.get("pc", {})
    if not isinstance(pc, dict):
        raise ValueError("audio_output.pc 必须是配置对象")
    device = pc.get("device")
    if device is not None and (isinstance(device, bool) or not isinstance(device, (str, int))):
        raise ValueError("audio_output.pc.device 必须是 null、设备编号或名称")
    volume = pc.get("volume", 0.6)
    if isinstance(volume, bool) or not isinstance(volume, (float, int)) or not math.isfinite(volume) or not 0 <= volume <= 1:
        raise ValueError("audio_output.pc.volume 必须在 0 到 1 之间")
    return mode, device, volume


def uses_pc_speaker(conn):
    return audio_output_settings(conn.config)[0] == "server_pc"


def _open_output(device):
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("PC 外放需要安装 requirements-pc-audio.txt 中的 sounddevice") from exc
    info = sd.query_devices(device, "output")
    channels = min(2, int(info["max_output_channels"]))
    if channels < 1:
        raise RuntimeError("选择的 PC 设备没有音频输出通道")
    rate = int(info["default_samplerate"])
    stream = sd.RawOutputStream(
        device=device, samplerate=rate, channels=channels, dtype="int16",
        blocksize=max(1, rate // 50), latency="low",
    )
    return stream, rate, channels


def _opus_decoder(rate):
    import opuslib_next
    return opuslib_next.Decoder(rate, 1)


class PCSoundDevice:
    """Cache a stopped stream, but lease the speaker only during an utterance."""

    def __init__(self, device):
        self.device = device
        self.stream = None
        self.rate = self.channels = None
        self.leased = False

    def open(self):
        if not _speaker_lock.acquire(blocking=False):
            raise RuntimeError("PC 扬声器正被另一个设备会话使用")
        try:
            if self.stream is None:
                self.stream, self.rate, self.channels = _open_output(self.device)
            self.leased = True
        except Exception:
            _speaker_lock.release()
            raise
        return self.stream, self.rate, self.channels

    def release(self):
        if self.leased:
            self.leased = False
            _speaker_lock.release()

    def close(self):
        if self.stream is not None:
            stream, self.stream = self.stream, None
            try:
                stream.close()
            finally:
                self.release()


class PCSpeaker:
    """One utterance, one worker, at most 8 queued Opus packets (~480 ms).

    Only the worker touches PortAudio. Abort is checked between 20 ms writes;
    normal completion drains the output before the device receives tts/stop.
    A connection-owned output can retain the stream between utterances. Only an
    active utterance holds the process-wide lease, so an idle old connection does
    not block a reconnect or another idle device's next response.
    """

    def __init__(self, sample_rate, device=None, volume=0.6, output=None):
        self.sample_rate = sample_rate
        self.device = device
        self.volume = volume
        self.output = output
        self.queue = queue.Queue(maxsize=8)
        self.aborted = threading.Event()
        self.finishing = threading.Event()
        self.done = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, name="pc-speaker", daemon=True)
        self.thread.start()

    async def write(self, packet):
        deadline = time.monotonic() + 10
        while not self.aborted.is_set() and not self.finishing.is_set():
            self._check_error()
            if self.done.is_set():
                raise RuntimeError("PC 音频播放线程已经停止")
            try:
                self.queue.put_nowait(bytes(packet))
                return
            except queue.Full:
                if time.monotonic() >= deadline:
                    self.abort()
                    raise RuntimeError("PC 音频输出超时，请检查声卡")
                await asyncio.sleep(0.01)

    def abort(self):
        self.aborted.set()

    async def finish(self):
        self.finishing.set()
        await self.wait_closed()
        self._check_error()

    async def wait_closed(self):
        # Bounded queue and 20 ms writes keep normal drain time well below this.
        if not await asyncio.to_thread(self.done.wait, 5):
            self.abort()
            raise RuntimeError("PC 音频设备未及时停止")

    def _check_error(self):
        if self.error is not None:
            raise RuntimeError(f"PC 音频播放失败: {self.error}") from self.error

    def _run(self):
        stream = None
        acquired = False
        cached_acquired = False
        try:
            # audioop is part of the project's supported Python 3.10 runtime.
            import audioop

            decoder = _opus_decoder(self.sample_rate)
            if self.output is not None:
                stream, output_rate, channels = self.output.open()
                cached_acquired = True
            else:
                acquired = _speaker_lock.acquire(blocking=False)
                if not acquired:
                    raise RuntimeError("PC 扬声器正被另一个设备会话使用，请稍后重试")
                stream, output_rate, channels = _open_output(self.device)
            state = None
            stream.start()
            while not self.aborted.is_set():
                try:
                    packet = self.queue.get(timeout=0.02)
                except queue.Empty:
                    if self.finishing.is_set():
                        break
                    continue
                # Opus packets may contain up to 120 ms, independent of send pacing.
                pcm = decoder.decode(packet, self.sample_rate * 120 // 1000)
                if output_rate != self.sample_rate:
                    pcm, state = audioop.ratecv(pcm, 2, 1, self.sample_rate, output_rate, state)
                if self.volume != 1:
                    pcm = audioop.mul(pcm, 2, self.volume)
                if channels == 2:
                    pcm = audioop.tostereo(pcm, 2, 1, 1)
                chunk_size = max(1, output_rate // 50) * channels * 2
                for offset in range(0, len(pcm), chunk_size):
                    if self.aborted.is_set():
                        break
                    stream.write(pcm[offset:offset + chunk_size])
            if self.aborted.is_set():
                stream.abort()
            else:
                stream.stop()  # Wait for the final samples, do not clip the last word.
        except Exception as exc:
            self.error = exc
        finally:
            try:
                if self.output is not None:
                    if cached_acquired:
                        if self.error is not None:
                            self.output.close()
                        else:
                            self.output.release()
                elif stream is not None:
                    stream.close()  # close discards pending data on errors
            except Exception as exc:
                if self.error is None:
                    self.error = exc
            finally:
                if acquired:
                    _speaker_lock.release()
                self.done.set()


def abort_pc_audio(conn):
    player = getattr(conn, "pc_audio_player", None)
    if player is not None:
        player.abort()


async def close_pc_audio(conn, release_device=False):
    player = getattr(conn, "pc_audio_player", None)
    if player is None:
        if release_device:
            output = getattr(conn, "pc_sound_device", None)
            if output is not None:
                try:
                    await asyncio.to_thread(output.close)
                except Exception as exc:
                    conn.logger.bind(tag=TAG).error(f"关闭 PC 声卡失败: {type(exc).__name__}")
        return
    player.abort()
    try:
        await player.wait_closed()
    except Exception as exc:
        conn.logger.bind(tag=TAG).error(str(exc))
    finally:
        if getattr(conn, "pc_audio_player", None) is player:
            conn.pc_audio_player = None
        if release_device and player.done.is_set():
            output = getattr(conn, "pc_sound_device", None)
            if output is not None:
                try:
                    await asyncio.to_thread(output.close)
                except Exception as exc:
                    conn.logger.bind(tag=TAG).error(f"关闭 PC 声卡失败: {type(exc).__name__}")


def _report_failure(conn, sentence_id, exc):
    # Fail closed: never unexpectedly play private replies on the device instead.
    conn.pc_audio_failed_sentence = sentence_id
    conn.logger.bind(tag=TAG).error(f"PC 外放失败，本轮停止播放，不回退开发板: {exc}")


async def send_pc_audio(conn, audios):
    sentence_id = conn.sentence_id
    if conn.client_abort or getattr(conn, "pc_audio_failed_sentence", object()) == sentence_id:
        return
    player = getattr(conn, "pc_audio_player", None)
    try:
        if player is None or getattr(conn, "pc_audio_sentence", None) != sentence_id:
            await close_pc_audio(conn)
            if conn.client_abort or sentence_id != conn.sentence_id:
                return
            _, device, volume = audio_output_settings(conn.config)
            output = getattr(conn, "pc_sound_device", None)
            if output is None:
                output = PCSoundDevice(device)
                conn.pc_sound_device = output
            player = PCSpeaker(conn.sample_rate, device, volume, output=output)
            conn.pc_audio_player = player
            conn.pc_audio_sentence = sentence_id
            conn.logger.bind(tag=TAG).info("音频输出: server_pc（服务端 PC 扬声器）")
        packets = [audios] if isinstance(audios, bytes) else audios
        for packet in packets:
            if conn.client_abort or sentence_id != conn.sentence_id or player.aborted.is_set():
                return
            conn.last_activity_time = time.time() * 1000
            await player.write(packet)
    except Exception as exc:
        if player is not None:
            player.abort()
        _report_failure(conn, sentence_id, exc)


async def finish_pc_audio(conn):
    player = getattr(conn, "pc_audio_player", None)
    if player is None:
        return
    sentence_id = getattr(conn, "pc_audio_sentence", None)
    try:
        await player.finish()
    except Exception as exc:
        player.abort()
        _report_failure(conn, sentence_id, exc)
    finally:
        if getattr(conn, "pc_audio_player", None) is player:
            conn.pc_audio_player = None
