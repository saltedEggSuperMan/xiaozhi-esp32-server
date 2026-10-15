param([switch]$Check)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create the Python 3.10 .venv and install requirements-windows.txt first.'
}

$previousPath = $env:PATH
$resultCode = 1
Push-Location $PSScriptRoot
try {
    # Keep FFmpeg and Opus inside this environment instead of changing Windows PATH.
    $prepareAudio = @'
import importlib.util
import pathlib
import shutil
import sys

if sys.version_info[:2] != (3, 10):
    raise SystemExit('This project environment must use Python 3.10.')
import imageio_ffmpeg
scripts = pathlib.Path(sys.executable).parent
opus_spec = importlib.util.find_spec('pyogg')
if opus_spec is None:
    raise SystemExit('Install requirements-windows.txt before starting.')
sources = {
    'ffmpeg.exe': pathlib.Path(imageio_ffmpeg.get_ffmpeg_exe()),
    'ffprobe.exe': pathlib.Path('../../.runtime/audio/bin/ffprobe.exe').resolve(),
    'opus.dll': pathlib.Path(opus_spec.origin).parent / 'opus.dll',
}
for name, source in sources.items():
    target = scripts / name
    if not target.exists():
        if not source.is_file():
            raise SystemExit(f'Missing {name}: install the complete FFmpeg tools in .runtime/audio/bin or .venv/Scripts.')
        shutil.copy2(source, target)
print('Python:', sys.version.split()[0], '-', sys.executable)
'@
    & $python -c $prepareAudio
    if ($LASTEXITCODE -ne 0) { throw 'Windows audio environment setup failed.' }
    $env:PATH = "$(Split-Path -Parent $python);$previousPath"

    if ($Check) {
        $checkEnvironment = @'
import subprocess
from io import BytesIO
import torch
import torchaudio
import numpy
import opuslib_next
import funasr
import aiohttp
import websockets
import edge_tts
import mcp
import yaml
from pydub import AudioSegment
from pathlib import Path

print('torch:', torch.__version__, '| torchaudio:', torchaudio.__version__, '| numpy:', numpy.__version__)
encoder = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_AUDIO)
decoder = opuslib_next.Decoder(16000, 1)
packet = encoder.encode(bytes(640), 320)
assert len(decoder.decode(packet, 320)) == 640
del encoder, decoder
print('Opus encode/decode: OK')
result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, check=True)
print(result.stdout.splitlines()[0])
result = subprocess.run(['ffprobe', '-version'], capture_output=True, text=True, check=True)
print(result.stdout.splitlines()[0])
mp3 = subprocess.check_output([
    'ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
    '-i', 'anullsrc=r=24000:cl=mono', '-t', '0.1', '-f', 'mp3', 'pipe:1',
])
assert len(AudioSegment.from_file(BytesIO(mp3), format='mp3')) > 0
print('MP3 audio decoding: OK')
config = yaml.safe_load(Path('data/.config.yaml').read_text(encoding='utf-8'))
model = Path('models/SenseVoiceSmall/model.pt')
print('SenseVoiceSmall weights:', 'present' if model.is_file() else 'MISSING')
secret = config.get('manager-api', {}).get('secret', '')
print('manager-api.secret:', 'configured' if secret and '\u4f60' not in secret else 'NOT SET - fill data/.config.yaml after starting manager-api')
print('Python environment checks passed.')
'@
        & $python -c $checkEnvironment
    } else {
        $checkSecret = @'
from pathlib import Path
import yaml
path = Path('data/.config.yaml')
if not path.is_file():
    raise SystemExit('Missing data/.config.yaml; copy config_from_api.yaml first.')
config = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
if config.get('manager-api', {}).get('url'):
    secret = config['manager-api'].get('secret', '')
    if not secret or '\u4f60' in secret:
        raise SystemExit('Fill manager-api.secret in data/.config.yaml with server.secret from the management console, then start manager-api before this server.')
'@
        & $python -c $checkSecret
        if ($LASTEXITCODE -ne 0) { throw 'Local configuration is not ready; see the message above.' }
        & $python app.py
    }
    $resultCode = $LASTEXITCODE
} finally {
    $env:PATH = $previousPath
    Pop-Location
}
exit $resultCode
