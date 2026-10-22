param([string]$Port='COM3', [switch]$Restore)
$ErrorActionPreference='Stop'
$projectRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$python=Join-Path $projectRoot '.runtime/fw244/tools/python_env/idf5.5_py3.10_env/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project ESP-IDF Python not found.' }
$manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw | ConvertFrom-Json
$file=if($Restore){'restore-default-assets.bin'}else{'otto-assets.bin'}
$expected=if($Restore){$manifest.restore_sha256}else{$manifest.sha256}
$image=Join-Path $PSScriptRoot $file
if ((Get-FileHash -LiteralPath $image -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Resource checksum mismatch.' }
if ((Get-Item -LiteralPath $image).Length -gt 0x800000) { throw 'Resource partition overflow.' }
& $python -m esptool --chip esp32s3 --port $Port --baud 460800 --before default_reset --after hard_reset write_flash --flash_mode dio --flash_freq 80m --flash_size 16MB 0x800000 $image
if ($LASTEXITCODE -ne 0) { throw "Resource flashing failed: $LASTEXITCODE" }
