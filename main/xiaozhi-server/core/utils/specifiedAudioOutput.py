"""Existing mpv as a bounded PCM sink pinned to one Windows render endpoint.

The exact endpoint and WASAPI driver are mandatory. No automatic driver/device
selection, exclusive access, endpoint volume or capture streams are used.
"""
from pathlib import Path
import subprocess
import threading
import time

from core.utils.windowsAudio import mpv_device, require_output

ROOT = Path(__file__).resolve().parents[4]


def mpv_path():
    path = ROOT / '.runtime/mpv/mpv.exe'
    if not path.is_file():
        raise RuntimeError('指定 PC 设备需要已有 mpv 播放组件，请参阅 docs/pc-device-audio.md')
    return path


def output_options(ident):
    return ['--ao=wasapi', '--audio-device=' + mpv_device(ident),
            '--audio-exclusive=no', '--audio-fallback-to-null=no',
            '--audio-client-name=xiaozhi', '--audio-set-media-role=no', '--volume=100']


class SpecifiedPCMStream:
    """RawOutputStream adapter. A new process/session is opened per utterance."""
    def __init__(self, ident):
        self.ident = ident
        self.process = None
        self.finished = threading.Event()
        self.error = None
        self.seconds = 0

    def start(self):
        require_output(self.ident)
        self.finished.clear()
        self.error = None
        self.seconds = 0
        args = [str(mpv_path()), '--no-config', '--load-scripts=no', '--no-video',
                '--terminal=no', '--input-terminal=no', '--really-quiet',
                '--cache=no', '--demuxer-max-bytes=64KiB', '--audio-buffer=0.1',
                '--demuxer=rawaudio', '--demuxer-rawaudio-format=s16le',
                '--demuxer-rawaudio-rate=24000', '--demuxer-rawaudio-channels=mono',
                *output_options(self.ident), '-']
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, bufsize=0,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.started_at = time.monotonic()
        self.monitor = threading.Thread(target=self._monitor, name='audio-endpoint-watch', daemon=True)
        self.monitor.start()

    def _monitor(self):
        # mpv also registers IMMNotificationClient. This watchdog surfaces device
        # loss to Python and kills a blocked pipe write; it never selects a device.
        while not self.finished.wait(0.25):
            try:
                require_output(self.ident)
                if self.process.poll() is not None:
                    raise RuntimeError('指定设备播放器已退出')
            except Exception as exc:
                self.error = exc
                self.abort()
                return

    def write(self, pcm):
        if self.error:
            raise self.error
        if not self.process or self.process.poll() is not None:
            raise RuntimeError('指定设备播放器已停止，不回退默认输出')
        try:
            view = memoryview(pcm)
            while view:
                written = self.process.stdin.write(view)
                if not written:
                    raise RuntimeError('指定设备音频管道已关闭')
                view = view[written:]
        except OSError as exc:
            raise RuntimeError('指定设备播放失败，不回退默认输出') from exc
        self.seconds += len(pcm) / 48000
        # Limit mpv/pipe read-ahead to 100 ms, retaining prompt interruption.
        remaining = self.started_at + self.seconds - time.monotonic() - 0.1
        if remaining > 0:
            self.finished.wait(remaining)

    def stop(self):
        try:
            self.process.stdin.close()
            result = self.process.wait(timeout=4)
            if self.error:
                raise self.error
            if result:
                raise RuntimeError('指定音频设备无法完成播放，不回退默认输出')
        finally:
            self.close()

    def abort(self):
        self.finished.set()
        if self.process and self.process.poll() is None:
            self.process.kill()

    def close(self):
        self.abort()
        if self.process:
            self.process.wait(timeout=2)
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
        monitor = getattr(self, 'monitor', None)
        if monitor and monitor is not threading.current_thread():
            monitor.join(timeout=2)
