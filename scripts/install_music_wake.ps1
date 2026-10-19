param()
$ErrorActionPreference = 'Stop'
$musicWakeProject = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$musicWakeRoot = Join-Path $musicWakeProject '.runtime/music-wake'
$musicWakePython = Join-Path $musicWakeProject 'main/xiaozhi-server/.venv/Scripts/python.exe'
& $musicWakePython -m pip install -r (Join-Path $musicWakeProject 'main/xiaozhi-server/requirements-music-wake.txt') --cache-dir (Join-Path $musicWakeProject '.runtime/pip-cache')
if ($LASTEXITCODE -ne 0) { throw 'Music wake dependency installation failed.' }
New-Item -ItemType Directory -Force -Path $musicWakeRoot | Out-Null
$musicWakeArchive = Join-Path $musicWakeRoot 'model.tar.bz2'
if (-not (Test-Path -LiteralPath $musicWakeArchive)) {
    Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2' -OutFile $musicWakeArchive
}
if ((Get-FileHash -LiteralPath $musicWakeArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'b2f7c89690dc8ce4c6ed6afeab7cd800c36ad1421fb6b6302b4a4b194cf7f35f') { throw 'Music wake model checksum mismatch.' }
@'
from pathlib import Path
import sys, tarfile
root=Path(sys.argv[1]).resolve()
with tarfile.open(root/'model.tar.bz2','r:bz2') as archive:
    members=[]
    for item in archive.getmembers():
        if not (root/item.name).resolve().is_relative_to(root) or not (item.isfile() or item.isdir()):
            raise ValueError('Unexpected model archive entry')
        members.append(item)
    archive.extractall(root,members=members)
print('Local music wake model installed.')
'@ | & $musicWakePython -B - $musicWakeRoot
if ($LASTEXITCODE -ne 0) { throw 'Music wake model extraction failed.' }
