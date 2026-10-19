"""Explicit machine-local response settings, also applied in management mode."""


def apply_speech_response(config, settings=None):
    settings = config.get("speech_response", {}) if settings is None else settings
    if not isinstance(settings, dict):
        raise ValueError("speech_response 必须是配置对象")
    silence = settings.get("vad_silence_ms")
    if silence is not None:
        if isinstance(silence, bool) or not isinstance(silence, int) or not 200 <= silence <= 2000:
            raise ValueError("speech_response.vad_silence_ms 必须为 200~2000 或 null")
        for provider in config.get("VAD", {}).values():
            if isinstance(provider, dict) and provider.get("type", "silero") == "silero":
                provider["min_silence_duration_ms"] = silence
    mode = settings.get("tts_mode", "configured")
    if mode not in ("configured", "windows_local"):
        raise ValueError("speech_response.tts_mode 只能是 configured 或 windows_local")
    if mode == "windows_local":
        voice = settings.get("windows_voice", "Microsoft Huihui Desktop")
        rate = settings.get("windows_rate", 0)
        volume = settings.get("windows_volume", 90)
        if not isinstance(voice, str) or not voice.strip():
            raise ValueError("windows_voice 必须是非空音色名称")
        if isinstance(rate, bool) or not isinstance(rate, int) or not -10 <= rate <= 10:
            raise ValueError("windows_rate 必须为 -10~10 的整数")
        if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
            raise ValueError("windows_volume 必须为 0~100 的整数")
        config.setdefault("TTS", {})["TTS_WindowsLocal"] = {
            "type": "windows_local", "voice": voice,
            "rate": rate, "volume": volume,
            "output_dir": "tmp/", "tts_timeout": config.get("tts_timeout", 15),
        }
        config.setdefault("selected_module", {})["TTS"] = "TTS_WindowsLocal"
    return config
