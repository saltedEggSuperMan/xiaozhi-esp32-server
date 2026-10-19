"""Windows installed offline voices; emits WAV into the existing Opus pipeline."""

import asyncio
import time
from pathlib import Path
from core.providers.tts.base import TTSProviderBase
from core.utils.windowsSpeech import synthesize_wav
from config.logger import setup_logging

logger = setup_logging()


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.voice = config.get("voice", "Microsoft Huihui Desktop")
        self.rate = int(config.get("rate", 0))
        self.volume = int(config.get("volume", 90))
        self.audio_file_type = "wav"

    async def text_to_speak(self, text, output_file):
        started = time.perf_counter()
        audio = await asyncio.to_thread(synthesize_wav, text, self.voice, self.rate,
                                        self.volume, self.tts_timeout)
        logger.bind(tag=__name__).info(
            f"响应计时 本地TTS合成完成: {(time.perf_counter() - started) * 1000:.0f}ms, 字符数={len(text)}"
        )
        if output_file:
            path = Path(output_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(audio)
            return None
        return audio
