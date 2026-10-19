"""Select the voice server's speaker without exposing other local configuration."""

import argparse
import asyncio
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from array import array


ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "main" / "xiaozhi-server"
sys.path.insert(0, str(VOICE))
if os.name == "nt":
    # opuslib_next locates opus.dll in the venv Scripts directory.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]

from core.utils.pcAudioOutput import PCSpeaker, audio_output_settings
from core.utils.speechResponse import apply_speech_response


def load_local(path):
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    config = yaml.load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return yaml, config or {}


def save_local(path, yaml, config):
    buffer = io.StringIO()
    yaml.dump(config, buffer)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".audio-output-", delete=False) as file:
            temporary = Path(file.name)
            file.write(buffer.getvalue())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


async def test_speaker(device, volume):
    import opuslib_next

    rate, frames = 24000, 1440
    encoder = opuslib_next.Encoder(rate, 1, opuslib_next.APPLICATION_AUDIO)
    player = PCSpeaker(rate, device, min(volume, 0.6))
    try:
        # Two seconds at the configured volume, with fades to avoid clicks.
        blocks = 33
        for block in range(blocks):
            frequency = 440 if block < 16 else 660
            pcm = array("h", (int(16000 * math.sin(2 * math.pi * frequency * (block * frames + i) / rate)
                                  * min(1, (block * frames + i) / 480,
                                        (blocks * frames - block * frames - i) / 480))
                              for i in range(frames)))
            if sys.byteorder != "little":
                pcm.byteswap()
            await player.write(encoder.encode(pcm.tobytes(), frames))
        await player.finish()
    finally:
        player.abort()
        await player.wait_closed()


def main(argv=None):
    parser = argparse.ArgumentParser(description="配置小智外放：开发板原音频模块 / 服务端 PC")
    parser.add_argument("--mode", choices=("device", "server_pc"))
    parser.add_argument("--device", help="PC 输出设备编号或名称，default 表示系统默认")
    parser.add_argument("--volume", type=float, help="PC 软件音量，0~1")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--test", action="store_true", help="播放约 2 秒的测试音")
    parser.add_argument("--tts-mode", choices=("configured", "windows_local"), help="沿用原 TTS 或 Windows 本地离线合成")
    parser.add_argument("--vad-silence-ms", type=int, help="本机静音结束等待，200~2000 毫秒")
    args = parser.parse_args(argv)

    if args.list_devices:
        import sounddevice as sd
        devices = sd.query_devices()
        hosts = sd.query_hostapis()
        for index, item in enumerate(devices):
            if item["max_output_channels"]:
                print(f'{index}: {item["name"]} [{hosts[item["hostapi"]]["name"]}]')
        if not any((args.mode, args.device, args.volume is not None, args.test, args.tts_mode,
                    args.vad_silence_ms is not None)):
            return

    path = VOICE / "data" / ".config.yaml"
    yaml, config = load_local(path)
    settings = config.setdefault("audio_output", {})
    pc = settings.setdefault("pc", {})
    if args.mode:
        settings["mode"] = args.mode
    if args.device is not None:
        pc["device"] = None if args.device == "default" else (
            int(args.device) if args.device.isdecimal() else args.device)
    if args.volume is not None:
        pc["volume"] = args.volume
    response = config.setdefault("speech_response", {})
    if args.tts_mode is not None:
        response["tts_mode"] = args.tts_mode
    if args.vad_silence_ms is not None:
        response["vad_silence_ms"] = args.vad_silence_ms
    # Validate local overrides without modifying the saved model definitions.
    apply_speech_response({}, response)
    mode, device, volume = audio_output_settings(config)
    if args.test:
        asyncio.run(test_speaker(device, volume))
        print("测试音播放完成，请确认实际听感。")
    # Test first: a failed test must not persist a new selection.
    if args.mode or args.device is not None or args.volume is not None or args.tts_mode or args.vad_silence_ms is not None:
        save_local(path, yaml, config)
        print("已保存。重启 Python 语音服务后生效；开发板无需刷固件。")
    print(json.dumps({"audio_output": {"mode": mode, "pc": {"device": device, "volume": volume}},
                      "speech_response": response},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"音频输出配置失败: {exc}", file=sys.stderr)
        sys.exit(1)
