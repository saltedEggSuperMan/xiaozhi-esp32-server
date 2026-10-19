param(
    [Parameter(Mandatory = $true)][string]$Ports,
    [switch]$Check,
    [switch]$OnlyVoice,
    [switch]$OnlyMusic
)

$ErrorActionPreference = 'Stop'
if ($OnlyVoice -and $OnlyMusic) { throw 'Choose only one process filter.' }
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$projectPorts = @($Ports.Split(',') | ForEach-Object {
    $number = [int]$_
    if ($number -lt 1 -or $number -gt 65535) { throw 'Invalid application port.' }
    $number
} | Sort-Object -Unique)

function Has-EntryPoint($Process, [string]$RelativePath) {
    $entry = Join-Path $projectRoot $RelativePath
    $command = ([string]$Process.CommandLine).Replace('/', '\')
    return $command.IndexOf($entry, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-ProjectLabel($Process) {
    if ($Process.Name -eq 'node.exe' -and (Has-EntryPoint $Process 'services\music-bridge\server.js')) {
        return 'NetEase music bridge'
    }
    if ($Process.Name -eq 'mpv.exe' -and (Has-EntryPoint $Process '.runtime\mpv\mpv.exe') -and
        $Process.CommandLine -match 'xiaozhi-music-') { return 'Project music player' }
    if ($Process.Name -in @('powershell.exe', 'pwsh.exe')) {
        if (Has-EntryPoint $Process 'main\xiaozhi-server\start-local.ps1') { return 'Python voice server' }
        if (Has-EntryPoint $Process 'main\manager-api\start-local.ps1') { return 'Java backend' }
    }
    if ($Process.Name -eq 'node.exe' -and (Has-EntryPoint $Process 'main\manager-web\node_modules\@vue\cli-service\bin\vue-cli-service.js')) {
        return 'Management frontend'
    }
    if ($Process.Name -in @('python.exe', 'pythonw.exe')) {
        if ((Has-EntryPoint $Process 'main\digital-human\serve-local.py') -or (Has-EntryPoint $Process '.runtime\serve-digital-test.py')) {
            return 'Browser test page'
        }
        if (Has-EntryPoint $Process 'main\xiaozhi-server\app.py') { return 'Python voice server' }
    }
    if ($Process.Name -eq 'java.exe' -and $Process.CommandLine -match 'xiaozhi\.AdminApplication' -and (Has-EntryPoint $Process 'main\manager-api\')) {
        return 'Java backend'
    }
    return $null
}

try {
    # Determine ownership from current commands and parent-child relationships,
    # not from PID files (which may refer to a different process after reboot).
    $snapshot = @(Get-CimInstance Win32_Process)
    $byId = @{}
    $candidates = @{}
    foreach ($process in $snapshot) {
        $byId[[int]$process.ProcessId] = $process
        $label = Get-ProjectLabel $process
        if ($label -and (-not $OnlyVoice -or $label -eq 'Python voice server') -and
            (-not $OnlyMusic -or $label -in @('NetEase music bridge','Project music player'))) {
            $candidates[[int]$process.ProcessId] = $label
        }
    }
    $roots = @{}
    foreach ($processId in $candidates.Keys) {
        $current = $byId[$processId]
        $parentIsOwned = $false
        $seen = @{}
        while ($current -and -not $seen.ContainsKey([int]$current.ProcessId)) {
            $seen[[int]$current.ProcessId] = $true
            $parentId = [int]$current.ParentProcessId
            $parent = $byId[$parentId]
            if (-not $parent -or $parent.CreationDate -gt $current.CreationDate) { break }
            if ($candidates.ContainsKey($parentId)) { $parentIsOwned = $true; break }
            $current = $parent
        }
        if (-not $parentIsOwned) { $roots[$processId] = $candidates[$processId] }
    }
    $owned = @{}
    foreach ($processId in $roots.Keys) { $owned[$processId] = $true }
    do {
        $expanded = $false
        foreach ($process in $snapshot) {
            $processId = [int]$process.ProcessId
            $parentId = [int]$process.ParentProcessId
            if (-not $owned.ContainsKey($processId) -and $owned.ContainsKey($parentId) -and
                $process.CreationDate -ge $byId[$parentId].CreationDate) {
                $owned[$processId] = $true
                $expanded = $true
            }
        }
    } while ($expanded)

    $listeners = @(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in $projectPorts })
    foreach ($listener in $listeners) {
        if (-not $owned.ContainsKey([int]$listener.OwningProcess)) {
            throw "Port $($listener.LocalPort) belongs to unverified PID $($listener.OwningProcess). Close its terminal or identify that process before restarting."
        }
    }

    if ($roots.Count -eq 0) { Write-Output 'No running project applications found; startup will proceed.' }
    $order = @('Python voice server', 'NetEase music bridge', 'Project music player', 'Browser test page', 'Management frontend', 'Java backend')
    foreach ($label in $order) {
        foreach ($processId in @($roots.Keys | Where-Object { $roots[$_] -eq $label })) {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
            if ($null -eq $process) { continue }
            $original = $byId[$processId]
            if ($process.CreationDate -ne $original.CreationDate -or $process.CommandLine -ne $original.CommandLine) {
                throw "PID $processId changed during inspection. Restart cancelled."
            }
            if ($Check) {
                Write-Output "[PLAN] $label, PID $processId and its children"
            } else {
                Write-Output "[STOP] $label, PID $processId and its children"
                & taskkill.exe /PID $processId /T /F
                if ($LASTEXITCODE -ne 0) { throw "Unable to stop $label (PID $processId)." }
            }
        }
    }
    if (-not $Check) {
        $deadline = (Get-Date).AddSeconds(30)
        do {
            $remaining = @(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in $projectPorts })
            if ($remaining.Count -eq 0) { break }
            Start-Sleep -Milliseconds 300
        } while ((Get-Date) -lt $deadline)
        if ($remaining.Count -gt 0) { throw 'Application ports are still occupied; startup cancelled.' }
        Write-Output 'Project application ports released. MySQL and Redis were kept running.'
    }
    exit 0
} catch {
    Write-Error $_
    exit 1
}
