"""Local keyword-only listening while PC music bypasses firmware TTS state.

No ASR, conversation reporting, recording, or external calls are made here.
The small shared model has one independent streaming state per connection.
"""
import asyncio
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parents[4]
MODEL = ROOT / '.runtime/music-wake/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01'
_lock = threading.Lock()
_spotter = None


def get_spotter():
    global _spotter
    with _lock:
        if _spotter is None:
            import sherpa_onnx
            # Canonical board wake phrase. Tone-mark pinyin matches model tokens.
            keywords = MODEL / 'music-keywords.txt'
            keywords.write_text('n ǐ h ǎo x iǎo zh ì @你好小智\n', encoding='utf8')
            _spotter = sherpa_onnx.KeywordSpotter(
                tokens=str(MODEL/'tokens.txt'),
                encoder=str(MODEL/'encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx'),
                decoder=str(MODEL/'decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx'),
                joiner=str(MODEL/'joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx'),
                keywords_file=str(keywords), num_threads=1, sample_rate=16000,
                max_active_paths=4, keywords_score=1.5, keywords_threshold=0.25,
                num_trailing_blanks=1, provider='cpu',
            )
        return _spotter


class MusicWakeword:
    def __init__(self):
        self.spotter = get_spotter()
        self.stream = self.spotter.create_stream()

    def feed(self, pcm):
        import numpy as np
        if not pcm:
            return False
        # Ingress Opus decoder always produces 16 kHz, mono, signed PCM16.
        samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
        with _lock:
            self.stream.accept_waveform(16000, samples)
            while self.spotter.is_ready(self.stream):
                self.spotter.decode_stream(self.stream)
                if self.spotter.get_result(self.stream):
                    self.spotter.reset_stream(self.stream)
                    return True
        return False


async def detect_music_wakeword(conn, pcm):
    """Feed only the keyword model. Failure retains the BOOT fallback."""
    if getattr(conn, '_music_wake_failed', False):
        return False
    try:
        detector = getattr(conn, '_music_wake_detector', None)
        if detector is None:
            detector = await asyncio.to_thread(MusicWakeword)
            conn._music_wake_detector = detector
            conn.logger.bind(tag=__name__).info('音乐播放期间本地唤醒检测已启用')
        return await asyncio.to_thread(detector.feed, pcm)
    except Exception as exc:
        conn._music_wake_failed = True
        conn.logger.bind(tag=__name__).warning('音乐本地唤醒不可用: ' + type(exc).__name__ + '；仍可使用 BOOT')
        return False


def reset_music_wakeword(conn):
    # Drop the stream rather than retaining partial words across playback sessions.
    if hasattr(conn, '_music_wake_detector'):
        conn._music_wake_detector = None
