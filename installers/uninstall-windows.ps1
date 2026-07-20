param([switch]$PurgeData)

$ErrorActionPreference = "Stop"
$installRoot = if ($env:MESHPI_INSTALL_ROOT) {
    $env:MESHPI_INSTALL_ROOT
} else {
    Join-Path $env:LOCALAPPDATA "MeshPi"
}
$configRoot = if ($env:MESHPI_CONFIG_ROOT) {
    $env:MESHPI_CONFIG_ROOT
} else {
    Join-Path $env:APPDATA "MeshPi"
}
$binDir = Join-Path $installRoot "bin"
$taskName = if ($env:MESHPI_TASK_NAME) {
    $env:MESHPI_TASK_NAME
} else {
    "MeshPi Daemon"
}

if ($env:MESHPI_SKIP_TASK -ne "1") {
    & schtasks.exe /Delete /TN $taskName /F *> $null
    $startup = Join-Path ([Environment]::GetFolderPath("Startup")) "MeshPi-daemon.vbs"
    Remove-Item -LiteralPath $startup -Force -ErrorAction SilentlyContinue
    $shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "MeshPi Daemon.lnk"
    Remove-Item -LiteralPath $shortcut -Force -ErrorAction SilentlyContinue
}

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        $_.CommandLine.IndexOf(
            $installRoot,
            [StringComparison]::OrdinalIgnoreCase
        ) -ge 0
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

$pathMarker = Join-Path $installRoot "path-added-by-meshpi"
if (Test-Path -LiteralPath $pathMarker) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @(
        $userPath -split ";" |
            Where-Object { $_ -and $_ -ne $binDir }
    )
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
}

if (Test-Path -LiteralPath $installRoot) {
    if ($PurgeData) {
        Remove-Item -LiteralPath $installRoot -Recurse -Force
    } else {
        foreach ($name in @(
            "releases", "current.txt", "previous.txt", "venv", "bin",
            "path-added-by-meshpi"
        )) {
            $path = Join-Path $installRoot $name
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "Fjerna MeshPi-programmet og autostarten." -ForegroundColor Green
if ($PurgeData) {
    Remove-Item -LiteralPath $configRoot -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Sletta konfigurasjon og lokale data."
} else {
    Write-Host "Bevarte konfigurasjon: $configRoot"
    Write-Host "Bevarte database og loggar: $(Join-Path $installRoot 'data')"
    Write-Host "Bruk -PurgeData for å slette desse òg."
}
