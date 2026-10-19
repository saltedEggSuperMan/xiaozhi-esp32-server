param()
$ErrorActionPreference = 'Stop'
$projectMusicRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$musicRuntime = Join-Path $projectMusicRoot '.runtime'
$musicApiRuntime = Join-Path $musicRuntime 'music-api'
$musicPlayerRuntime = Join-Path $musicRuntime 'mpv'
if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22+ first.' }
$nodeMajor = [int]((& node.exe --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 22) { throw 'Node.js 22+ is required.' }
New-Item -ItemType Directory -Force -Path $musicApiRuntime,$musicPlayerRuntime | Out-Null
Copy-Item -LiteralPath (Join-Path $projectMusicRoot 'services/music-bridge/package.json') -Destination $musicApiRuntime
Copy-Item -LiteralPath (Join-Path $projectMusicRoot 'services/music-bridge/package-lock.json') -Destination $musicApiRuntime
& npm.cmd ci --prefix $musicApiRuntime --ignore-scripts --no-fund --no-audit --cache (Join-Path $musicRuntime 'npm-cache')
if ($LASTEXITCODE -ne 0) { throw 'NetEase API dependency installation failed.' }
$musicArchive = Join-Path $musicPlayerRuntime 'player.7z'
$musicArchiveHash = 'd7157567a2fd88e46f739d2e8b8e6c02d2ad812db04547b45c59a2800821991f'
$musicArchiveUrl = 'https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261018/mpv-x86_64-20261018-git-01e2533f31.7z'
if (-not (Test-Path -LiteralPath $musicArchive)) { Invoke-WebRequest -UseBasicParsing -Uri $musicArchiveUrl -OutFile $musicArchive }
if ((Get-FileHash -LiteralPath $musicArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $musicArchiveHash) { throw 'mpv checksum mismatch.' }
$musicExtractor = Join-Path $musicPlayerRuntime '7zr.exe'
if (-not (Test-Path -LiteralPath $musicExtractor)) { Invoke-WebRequest -UseBasicParsing -Uri 'https://www.7-zip.org/a/7zr.exe' -OutFile $musicExtractor }
if ((Get-FileHash -LiteralPath $musicExtractor -Algorithm SHA256).Hash.ToLowerInvariant() -ne '256feca8e274e5da655e2a284fabafd9f554365eb164862089dacd4e8276d282') { throw '7-Zip checksum mismatch.' }
& $musicExtractor x $musicArchive "-o$musicPlayerRuntime" -y
if ($LASTEXITCODE -ne 0) { throw 'mpv extraction failed.' }
& (Join-Path $musicPlayerRuntime 'mpv.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'mpv execution failed.' }
$musicSettings = Join-Path $musicRuntime 'music/settings.json'
if (-not (Test-Path -LiteralPath $musicSettings)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $musicSettings) | Out-Null
    [IO.File]::WriteAllText($musicSettings, '{"source":"local","volume":35}', (New-Object Text.UTF8Encoding($false)))
}
Write-Output 'Music components installed. Run music.cmd --source netease --open to sign in.'
& (Join-Path $PSScriptRoot 'install_music_wake.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Local music wake setup failed.' }
