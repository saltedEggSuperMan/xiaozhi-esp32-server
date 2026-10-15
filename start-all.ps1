param(
    [switch]$Check,
    [switch]$OpenBrowser,
    [ValidateRange(10, 1800)][int]$TimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot 'main\xiaozhi-server\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error 'Project Python environment is missing: main/xiaozhi-server/.venv'
    exit 1
}

$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$arguments = @('-u', (Join-Path $PSScriptRoot 'scripts\start_local.py'), '--timeout', "$TimeoutSeconds")
if ($Check) { $arguments += '--check' }
if ($OpenBrowser) { $arguments += '--open-browser' }
& $python @arguments
exit $LASTEXITCODE
