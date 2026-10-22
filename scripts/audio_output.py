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
from core.utils.windowsAudio import list_outputs, require_output, mpv_device


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


def speech_test_pcm(settings=None):
    """Generate an intelligible offline sample, without opening a microphone."""
    import audioop
    import wave
    from core.utils.windowsSpeech import synthesize_wav
    settings = settings or {}
    wav = synthesize_wav('这是小智的中文语音测试，声音正常。',
                         voice=settings.get('windows_voice', 'Microsoft Huihui Desktop'), timeout=20)
    with wave.open(io.BytesIO(wav), 'rb') as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise RuntimeError('本地测试语音不是预期的单声道 16 位 PCM')
        pcm = source.readframes(source.getnframes())
        rate = source.getframerate()
    if rate != 24000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 24000, None)
    return pcm


async def test_speech(device, volume, settings=None):
    import opuslib_next
    # Synthesis precedes device opening: synthesis errors must not report a beep
    # as a successful speech test. Use the same Opus/PCM route as actual replies.
    pcm = await asyncio.to_thread(speech_test_pcm, settings)
    encoder = opuslib_next.Encoder(24000, 1, opuslib_next.APPLICATION_AUDIO)
    player = PCSpeaker(24000, device, min(volume, 0.6))
    try:
        for offset in range(0, len(pcm), 2880):
            frame = pcm[offset:offset + 2880].ljust(2880, b'\0')
            await player.write(encoder.encode(frame, 1440))
        await player.finish()
    finally:
        player.abort()
        await player.wait_closed()


def describe(config):
    mode, device, volume = audio_output_settings(config)
    pc = config.get('audio_output', {}).get('pc', {})
    selected = pc.get('device_id')
    devices = list_outputs(selected)
    current = next((d for d in devices if d['selected']), None)
    return {'mode': mode, 'device_id': selected, 'volume': volume,
            'devices': devices, 'available': bool(current and current['available']),
            'mpv_device': mpv_device(selected) if selected else None}


def api_request(request):
    """Narrow JSON interface for the authenticated local configuration page."""
    path = VOICE / 'data/.config.yaml'
    yaml, config = load_local(path)
    action = request.get('action', 'describe')
    if action not in ('describe', 'save', 'test', 'volume'):
        raise ValueError('无效音频配置操作')
    if action == 'volume':
        # A volume-only update never changes the mode/endpoint or needs the
        # device to be connected. Validate before touching the persisted file.
        config.setdefault('audio_output', {}).setdefault('pc', {})['volume'] = request.get('volume')
        audio_output_settings(config)
        save_local(path, yaml, config)
    if action in ('save', 'test'):
        mode = request.get('mode')
        if mode not in ('device', 'server_pc', 'PC_DEVICE'):
            raise ValueError('无效输出模式')
        settings = config.setdefault('audio_output', {})
        settings['mode'] = mode
        pc = settings.setdefault('pc', {})
        if 'device_id' in request:
            if request['device_id'] is not None:
                mpv_device(request['device_id'])
            pc['device_id'] = request['device_id']
        if 'volume' in request:
            pc['volume'] = request['volume']
        _, device, volume = audio_output_settings(config)
        if mode == 'PC_DEVICE':
            require_output(device)
            from core.utils.specifiedAudioOutput import mpv_path
            mpv_path()
        if action == 'test':
            if mode != 'PC_DEVICE':
                raise ValueError('请先选择指定 PC 音频设备进行测试')
            asyncio.run(test_speech(device, volume, config.get('speech_response')))
        else:
            save_local(path, yaml, config)
    return describe(config)


def main(argv=None):
    parser = argparse.ArgumentParser(description="配置小智外放：开发板原音频模块 / 服务端 PC")
    parser.add_argument("--mode", choices=("device", "server_pc", "PC_DEVICE"))
    parser.add_argument('--device-id', help='Windows 输出终端 ID（也可在本机管理页选择）')
    parser.add_argument('--select-name', help='仅首次选择时按唯一名称匹配，保存的是设备 ID')
    parser.add_argument('--request-json', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument("--device", help="PC 输出设备编号或名称，default 表示系统默认")
    parser.add_argument("--volume", type=float, help="PC 软件音量，0~1")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--test", action="store_true", help="指定 PC 设备播放中文语音测试；原 PC 模式播放变调测试音")
    parser.add_argument("--tts-mode", choices=("configured", "windows_local"), help="沿用原 TTS 或 Windows 本地离线合成")
    parser.add_argument("--vad-silence-ms", type=int, help="本机静音结束等待，200~2000 毫秒")
    args = parser.parse_args(argv)

    if args.request_json:
        try:
            print(json.dumps({'ok': True, **api_request(json.load(sys.stdin))}, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({'ok': False, 'message': str(exc)}, ensure_ascii=False))
            return 1
        return 0

    if args.list_devices:
        if os.name == 'nt':
            for item in list_outputs():
                print(f'{item["state"]}: {item["name"]}\n  {item["id"]}')
        else:
            import sounddevice as sd
            hosts = sd.query_hostapis()
            for index, item in enumerate(sd.query_devices()):
                if item['max_output_channels']:
                    print(f'{index}: {item["name"]} [{hosts[item["hostapi"]]["name"]}]')
        if not any((args.mode, args.device, args.volume is not None, args.test, args.tts_mode,
                    args.vad_silence_ms is not None, args.device_id, args.select_name)):
            return

    path = VOICE / "data" / ".config.yaml"
    yaml, config = load_local(path)
    settings = config.setdefault("audio_output", {})
    pc = settings.setdefault("pc", {})
    if args.mode:
        settings["mode"] = args.mode
    if args.select_name:
        matches = [d for d in list_outputs() if d['available'] and args.select_name.casefold() in d['name'].casefold()]
        if len(matches) != 1:
            raise ValueError('设备名称未唯一匹配可用输出终端，请刷新设备并在界面中选择')
        pc['device_id'] = matches[0]['id']
    if args.device_id is not None:
        pc['device_id'] = args.device_id
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
    if mode == 'PC_DEVICE':
        require_output(device)
    if args.test:
        if mode == 'PC_DEVICE':
            asyncio.run(test_speech(device, volume, config.get('speech_response')))
            print('中文语音测试播放完成，请确认能够听懂测试句子；播放器返回成功不等于听感已通过。')
        else:
            asyncio.run(test_speaker(device, volume))
            print("变调测试音播放完成，请确认实际听感。")
    # Test first: a failed test must not persist a new selection.
    if args.mode or args.device is not None or args.device_id or args.select_name or args.volume is not None or args.tts_mode or args.vad_silence_ms is not None:
        save_local(path, yaml, config)
        print("已保存。新版语音服务自动读取音频出口；首次更新代码需重启服务，开发板无需刷固件。")
    print(json.dumps({"audio_output": settings,
                      "speech_response": response},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        print(f"音频输出配置失败: {exc}", file=sys.stderr)
        sys.exit(1)
