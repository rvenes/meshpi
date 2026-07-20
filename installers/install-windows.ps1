param(
    [string]$BaseUrl = "https://venes.org/meshpi",
    [switch]$SkipAutostart
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Test-PythonCommand {
    param([string]$Executable, [string[]]$Prefix)
    try {
        & $Executable @Prefix -c "import sys; raise SystemExit(sys.version_info < (3, 11))" *>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Find-MeshPiPython {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @("-3.13", "-3.12", "-3.11")) {
            if (Test-PythonCommand $launcher.Source @($version)) {
                return @{ Exe = $launcher.Source; Prefix = @($version) }
            }
        }
    }
    foreach ($name in @("python3.exe", "python.exe")) {
        $python = Get-Command $name -ErrorAction SilentlyContinue
        if ($python -and (Test-PythonCommand $python.Source @())) {
            return @{ Exe = $python.Source; Prefix = @() }
        }
    }
    return $null
}

function Stop-MeshPiProcesses {
    param([string]$InstallRoot)
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $PID -and
            $_.CommandLine -and
            $_.CommandLine.IndexOf(
                $InstallRoot,
                [StringComparison]::OrdinalIgnoreCase
            ) -ge 0
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

$python = Find-MeshPiPython
if (-not $python) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "MeshPi krev Python 3.11+. Installer frå https://python.org og køyr skriptet igjen."
    }
    Write-Host "Installerer Python 3.11 for gjeldande brukar …" -ForegroundColor Cyan
    & $winget.Source install --id Python.Python.3.11 --exact --scope user `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Klarte ikkje installere Python 3.11 med winget."
    }
    $python = Find-MeshPiPython
    if (-not $python) {
        throw "Python blei installert, men er ikkje tilgjengeleg enno. Opne PowerShell på nytt."
    }
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
$dataDir = Join-Path $installRoot "data"
$venvDir = Join-Path $installRoot "venv"
$configFile = Join-Path $configRoot "meshpi.env"
$tempDir = Join-Path ([IO.Path]::GetTempPath()) ("meshpi-" + [guid]::NewGuid())
$manifestFile = Join-Path $tempDir "version.json"

New-Item -ItemType Directory -Force -Path $tempDir, $installRoot, $configRoot, $binDir, $dataDir |
    Out-Null
try {
    if ($env:MESHPI_MANIFEST_FILE) {
        Copy-Item -LiteralPath $env:MESHPI_MANIFEST_FILE -Destination $manifestFile
    } else {
        Invoke-WebRequest "$BaseUrl/version.json" -OutFile $manifestFile
    }
    $manifest = Get-Content -Raw $manifestFile | ConvertFrom-Json
    $version = [string]$manifest.latest_version
    $packageUrl = [string]$manifest.package.url
    $expectedHash = ([string]$manifest.package.sha256).ToLowerInvariant()
    $wheelFile = Join-Path $tempDir "meshpi-$version-py3-none-any.whl"
    if ($expectedHash -notmatch "^[0-9a-f]{64}$") {
        throw "Ugyldig SHA-256 i version.json."
    }

    Invoke-WebRequest $packageUrl -OutFile $wheelFile
    $actualHash = (Get-FileHash -Algorithm SHA256 $wheelFile).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "SHA-256 stemmer ikkje. Installasjonen er avbroten."
    }

    Stop-MeshPiProcesses $installRoot
    $venvArguments = @($python.Prefix) + @("-m", "venv", $venvDir)
    & $python.Exe $venvArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Klarte ikkje opprette Python-miljøet."
    }
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    & $venvPython -m pip install -q --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Klarte ikkje oppdatere pip."
    }
    & $venvPython -m pip install -q --upgrade --force-reinstall $wheelFile
    if ($LASTEXITCODE -ne 0) {
        throw "Klarte ikkje installere MeshPi-pakken."
    }

    if (-not (Test-Path -LiteralPath $configFile)) {
        $utf8NoBom = New-Object Text.UTF8Encoding($false)
        $configText = @"
MESHTASTIC_HOST=10.0.0.152
MESHTASTIC_PORT=4403
DATABASE_PATH=$dataDir\meshtastic.db
CONNECTIONS_PATH=$dataDir\connections.json
DISCOVERY_SUBNET=10.0.0.0/24
IPC_HOST=127.0.0.1
IPC_PORT=8765
LOG_LEVEL=INFO
UPDATE_URL=$BaseUrl/version.json
UPDATE_TIMEOUT=3
"@
        [IO.File]::WriteAllText($configFile, $configText, $utf8NoBom)
    }

    $meshpiExe = Join-Path $venvDir "Scripts\meshpi.exe"
    @"
@echo off
"$meshpiExe" --env-file "$configFile" %*
"@ | Set-Content -Encoding ASCII (Join-Path $binDir "meshpi.cmd")

    if ($env:MESHPI_SKIP_PATH -ne "1") {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $pathParts = @($userPath -split ";" | Where-Object { $_ })
        if ($binDir -notin $pathParts) {
            [Environment]::SetEnvironmentVariable(
                "Path",
                (($pathParts + $binDir) -join ";"),
                "User"
            )
        }
    }
    $env:Path = "$binDir;$env:Path"

    if (-not $SkipAutostart) {
        $startup = [Environment]::GetFolderPath("Startup")
        $startupScript = Join-Path $startup "MeshPi-daemon.vbs"
        $command = '"""' + $meshpiExe + '"" --env-file ""' + $configFile + '"" daemon"'
        @"
Set shell = CreateObject("WScript.Shell")
shell.Run $command, 0, False
"@ | Set-Content -Encoding Unicode $startupScript
        Start-Process -FilePath $meshpiExe `
            -ArgumentList @("--env-file", "`"$configFile`"", "daemon") `
            -WorkingDirectory $dataDir -WindowStyle Hidden
    }

    Write-Host "MeshPi $version er installert." -ForegroundColor Green
    Write-Host "Opne eit nytt terminalvindauge og start med: meshpi"
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
