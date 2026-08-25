param(
    [switch]$PurgeData,
    [string]$Language = ""
)

$ErrorActionPreference = "Stop"
$detectedLanguage = if (
    [Globalization.CultureInfo]::CurrentUICulture.Name -match "^(nn|nb|no)(-|$)"
) { "nn" } else { "en" }
if (-not $Language) {
    $Language = if ($env:MESHPI_LANGUAGE) {
        $env:MESHPI_LANGUAGE
    } else {
        $detectedLanguage
    }
}
$Language = $Language.ToLowerInvariant()
$Messages = @{
    invalid_language = @{ nn = 'Språket må vere «nn» eller «en».'; en = 'Language must be “nn” or “en”.' }
    removed = @{ nn = 'Fjerna MeshPi-programmet og autostarten.'; en = 'Removed the MeshPi application and autostart.' }
    purged = @{ nn = 'Sletta konfigurasjon og lokale data.'; en = 'Deleted configuration and local data.' }
    kept_config = @{ nn = 'Bevarte konfigurasjon: {0}'; en = 'Preserved configuration: {0}' }
    kept_data = @{ nn = 'Bevarte database og loggar: {0}'; en = 'Preserved database and logs: {0}' }
    purge_hint = @{ nn = 'Bruk -PurgeData for å slette desse òg.'; en = 'Use -PurgeData to delete these as well.' }
}

function Get-Message {
    param([string]$Key, [object[]]$Values = @())
    $template = [string]$Messages[$Key][$Language]
    if ($Values.Count -eq 0) { return $template }
    return [string]::Format($template, $Values)
}

if ($Language -notin @("nn", "en")) {
    $Language = $detectedLanguage
    throw (Get-Message invalid_language)
}
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

Write-Host (Get-Message removed) -ForegroundColor Green
if ($PurgeData) {
    Remove-Item -LiteralPath $configRoot -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host (Get-Message purged)
} else {
    Write-Host (Get-Message kept_config @($configRoot))
    Write-Host (Get-Message kept_data @((Join-Path $installRoot 'data')))
    Write-Host (Get-Message purge_hint)
}
