"""Audio conversion retries must not duplicate the user's visible response."""
from queue import Queue
from types import SimpleNamespace

from core.providers.tts import base as tts_base
from core.providers.tts.dto.dto import SentenceType


class _TTS(tts_base.TTSProviderBase):
    async def text_to_speak(self, text, output_file):
        self.synthesis_calls += 1
        return b"synthesized-mp3"


def _provider():
    provider = object.__new__(_TTS)
    provider._correct_words_pattern = None
    provider.delete_audio_file = True
    provider.audio_file_type = "mp3"
    provider.conn = SimpleNamespace(sample_rate=24000)
    provider.opus_encoder = None
    provider.current_sentence_id = "reply-1"
    provider.tts_audio_queue = Queue()
    provider.synthesis_calls = 0
    return provider


def _events(provider):
    return list(provider.tts_audio_queue.queue)


def test_conversion_retry_publishes_text_once_before_recovered_audio(monkeypatch):
    provider = _provider()
    attempts = 0

    def convert(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 5:
            raise FileNotFoundError("ffprobe")
        kwargs["callback"](b"opus-packet")

    monkeypatch.setattr(tts_base, "audio_bytes_to_data_stream", convert)
    provider.to_tts_stream("It is 8:46 PM.", provider.handle_opus)

    assert provider.synthesis_calls == 5
    assert _events(provider) == [
        (SentenceType.FIRST, None, "It is 8:46 PM.", "reply-1"),
        (SentenceType.MIDDLE, b"opus-packet", None, "reply-1"),
    ]


def test_exhausted_conversion_retries_leave_one_visible_text(monkeypatch):
    provider = _provider()

    def convert(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(tts_base, "audio_bytes_to_data_stream", convert)
    provider.to_tts_stream("Captain Wangwang", provider.handle_opus)

    assert provider.synthesis_calls == 5
    assert _events(provider) == [
        (SentenceType.FIRST, None, "Captain Wangwang", "reply-1"),
    ]


def test_separate_replies_with_identical_text_are_both_delivered(monkeypatch):
    provider = _provider()

    def convert(*args, **kwargs):
        kwargs["callback"](b"opus-packet")

    monkeypatch.setattr(tts_base, "audio_bytes_to_data_stream", convert)
    provider.to_tts_stream("Hello", provider.handle_opus)
    provider.current_sentence_id = "reply-2"
    provider.to_tts_stream("Hello", provider.handle_opus)

    assert _events(provider) == [
        (SentenceType.FIRST, None, "Hello", "reply-1"),
        (SentenceType.MIDDLE, b"opus-packet", None, "reply-1"),
        (SentenceType.FIRST, None, "Hello", "reply-2"),
        (SentenceType.MIDDLE, b"opus-packet", None, "reply-2"),
    ]


def test_text_is_available_while_cloud_synthesis_is_still_pending(monkeypatch):
    provider = _provider()
    pending_events = []

    async def synthesize(text, output_file):
        # The audio thread can deliver the caption before the network call finishes.
        pending_events.append(_events(provider))
        return b"synthesized-mp3"

    monkeypatch.setattr(provider, "text_to_speak", synthesize)
    monkeypatch.setattr(tts_base, "audio_bytes_to_data_stream", lambda *args, **kwargs: None)
    provider.to_tts_stream("Hello", provider.handle_opus)
    assert pending_events == [[(SentenceType.FIRST, None, "Hello", "reply-1")]]
