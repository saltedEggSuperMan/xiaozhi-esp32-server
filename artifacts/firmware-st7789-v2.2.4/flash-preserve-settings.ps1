param(
    [Parameter(Mandatory=$true)][string]$Port,
    [int]$Baud=460800
)
$ErrorActionPreference='Stop'
$projectRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'flash-manifest.json') -Raw | ConvertFrom-Json
$python=Join-Path $projectRoot '.runtime/fw244/tools/python_env/idf5.5_py3.10_env/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python=$manifest.build_python }
if (-not (Test-Path -LiteralPath $python)) { throw 'Project ESP-IDF Python environment not found.' }
foreach ($line in Get-Content -LiteralPath (Join-Path $PSScriptRoot 'SHA256SUMS.txt')) {
    $expected,$file=$line -split '  ',2
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PSScriptRoot $file)).Hash.ToLowerInvariant() -ne $expected) {
        throw "Checksum mismatch: $file"
    }
}
$flashArguments=@('-m','esptool','--chip','esp32s3','--port',$Port,'--baud',"$Baud",
    '--before','default_reset','--after','hard_reset','write_flash',
    '--flash_mode',$manifest.flash_mode,'--flash_freq',$manifest.flash_freq,'--flash_size',$manifest.flash_size)
foreach ($entry in ($manifest.flash_files.PSObject.Properties | Sort-Object { [Convert]::ToInt32($_.Name,16) })) {
    $flashArguments+=@($entry.Name,(Join-Path $PSScriptRoot $entry.Value))
}
& $python @flashArguments
if ($LASTEXITCODE -ne 0) { throw "Flashing failed: $LASTEXITCODE" }
