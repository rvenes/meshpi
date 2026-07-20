param(
    [string]$BaseUrl = "https://venes.org/meshpi",
    [ValidateSet("Always", "Session")]
    [string]$Mode = "Always",
    [switch]$SkipAutostart
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if ($SkipAutostart) {
    $Mode = "Session"
}
$modeValue = $Mode.ToLowerInvariant()
$ipcPort = if ($env:MESHPI_IPC_PORT) { $env:MESHPI_IPC_PORT } else { "8765" }

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
        foreach ($version in @("-3.14", "-3.13", "-3.12", "-3.11")) {
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

function Invoke-NativeChecked {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$ErrorMessage
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $ErrorMessage
    }
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    [IO.File]::WriteAllText($Path, $Content, (New-Object Text.UTF8Encoding($false)))
}

function Set-EnvValue {
    param([string]$Path, [string]$Name, [string]$Value)
    $lines = if (Test-Path -LiteralPath $Path) {
        @(Get-Content -LiteralPath $Path)
    } else {
        @()
    }
    $found = $false
    $changed = $false
    $updated = foreach ($line in $lines) {
        if ($line -match ("^" + [regex]::Escape($Name) + "=")) {
            $found = $true
            $replacement = "$Name=$Value"
            $changed = $changed -or $line -ne $replacement
            $replacement
        } else {
            $line
        }
    }
    if (-not $found) {
        $updated = @($updated) + "$Name=$Value"
        $changed = $true
    }
    if (-not $changed) {
        return
    }
    Write-Utf8NoBom $Path (($updated -join "`n") + "`n")
}

function Set-CurrentRelease {
    param([string]$CurrentFile, [string]$Release)
    $temporary = "$CurrentFile.new"
    Write-Utf8NoBom $temporary ($Release + "`n")
    Move-Item -LiteralPath $temporary -Destination $CurrentFile -Force
}

$python = Find-MeshPiPython
if (-not $python) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "MeshPi krev Python 3.11+. Installer frå https://python.org og køyr skriptet igjen."
    }
    Write-Host "Installerer Python 3.11 for gjeldande brukar …" -ForegroundColor Cyan
    Invoke-NativeChecked $winget.Source @(
        "install", "--id", "Python.Python.3.11", "--exact", "--scope", "user",
        "--accept-package-agreements", "--accept-source-agreements"
    ) "Klarte ikkje installere Python 3.11 med winget."
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
$releasesDir = Join-Path $installRoot "releases"
$currentFile = Join-Path $installRoot "current.txt"
$previousFile = Join-Path $installRoot "previous.txt"
$configFile = Join-Path $configRoot "meshpi.env"
$tempDir = Join-Path ([IO.Path]::GetTempPath()) ("meshpi-" + [guid]::NewGuid())
$manifestFile = Join-Path $tempDir "version.json"
$lockFile = Join-Path $tempDir "requirements-windows.txt"
$taskName = if ($env:MESHPI_TASK_NAME) {
    $env:MESHPI_TASK_NAME
} else {
    "MeshPi Daemon"
}

New-Item -ItemType Directory -Force -Path @(
    $tempDir, $installRoot, $configRoot, $binDir, $dataDir, $releasesDir
) | Out-Null

try {
    if ($env:MESHPI_MANIFEST_FILE) {
        Copy-Item -LiteralPath $env:MESHPI_MANIFEST_FILE -Destination $manifestFile
    } else {
        Invoke-WebRequest "$BaseUrl/version.json" -OutFile $manifestFile
    }
    $manifest = Get-Content -Raw $manifestFile | ConvertFrom-Json
    $version = [string]$manifest.latest_version
    if ($version -notmatch "^\d+\.\d+\.\d+$") {
        throw "Ugyldig versjon i version.json."
    }
    $packageUrl = [string]$manifest.package.url
    $expectedHash = ([string]$manifest.package.sha256).ToLowerInvariant()
    $lockUrl = [string]$manifest.locks.windows.url
    $expectedLockHash = ([string]$manifest.locks.windows.sha256).ToLowerInvariant()
    if ($expectedHash -notmatch "^[0-9a-f]{64}$") {
        throw "Ugyldig SHA-256 i version.json."
    }
    if ($expectedLockHash -notmatch "^[0-9a-f]{64}$") {
        throw "Ugyldig låsefil-hash i version.json."
    }
    $wheelFile = Join-Path $tempDir "meshpi-$version-py3-none-any.whl"
    if ($env:MESHPI_PACKAGE_FILE) {
        Copy-Item -LiteralPath $env:MESHPI_PACKAGE_FILE -Destination $wheelFile
    } else {
        Invoke-WebRequest $packageUrl -OutFile $wheelFile
    }
    if ($env:MESHPI_LOCK_FILE) {
        Copy-Item -LiteralPath $env:MESHPI_LOCK_FILE -Destination $lockFile
    } else {
        Invoke-WebRequest $lockUrl -OutFile $lockFile
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 $wheelFile).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "SHA-256 stemmer ikkje. Installasjonen er avbroten."
    }
    $actualLockHash = (Get-FileHash -Algorithm SHA256 $lockFile).Hash.ToLowerInvariant()
    if ($actualLockHash -ne $expectedLockHash) {
        throw "SHA-256 for låsefila stemmer ikkje. Installasjonen er avbroten."
    }

    if (-not (Test-Path -LiteralPath $configFile)) {
        $configText = @"
MESHTASTIC_HOST=10.0.0.152
MESHTASTIC_PORT=4403
DATABASE_PATH=$dataDir\meshtastic.db
CONNECTIONS_PATH=$dataDir\connections.json
DISCOVERY_SUBNET=10.0.0.0/24
IPC_HOST=127.0.0.1
IPC_PORT=$ipcPort
LOG_LEVEL=INFO
UPDATE_URL=$BaseUrl/version.json
UPDATE_TIMEOUT=3
BACKGROUND_MODE=$modeValue
"@
        Write-Utf8NoBom $configFile $configText
    } else {
        Set-EnvValue $configFile "BACKGROUND_MODE" $modeValue
    }

    $release = Join-Path $releasesDir $version
    $oldRelease = if (Test-Path -LiteralPath $currentFile) {
        (Get-Content -Raw $currentFile).Trim()
    } else {
        ""
    }
    if ($release -ne $oldRelease) {
        if (Test-Path -LiteralPath $release) {
            Remove-Item -LiteralPath $release -Recurse -Force
        }
        $venvArguments = @($python.Prefix) + @("-m", "venv", (Join-Path $release "venv"))
        Invoke-NativeChecked $python.Exe $venvArguments "Klarte ikkje opprette Python-miljøet."
        $venvPython = Join-Path $release "venv\Scripts\python.exe"
        Invoke-NativeChecked $venvPython @(
            "-m", "pip", "install", "-q", "--require-hashes", "-r", $lockFile
        ) "Klarte ikkje installere låste avhengigheiter."
        Invoke-NativeChecked $venvPython @(
            "-m", "pip", "install", "-q", "--no-deps", $wheelFile
        ) "Klarte ikkje installere MeshPi-pakken."
    }
    $releaseMeshPi = Join-Path $release "venv\Scripts\meshpi.exe"
    $installedVersion = (& $releaseMeshPi --version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $installedVersion -ne "MeshPi $version") {
        throw "Pakken rapporterer «$installedVersion», venta MeshPi $version."
    }
    Invoke-NativeChecked $releaseMeshPi @(
        "--env-file", $configFile, "doctor", "--offline"
    ) "MeshPi-sjølvtesten feila."

    Stop-MeshPiProcesses $installRoot
    $legacyVenv = Join-Path $installRoot "venv"
    if (-not $oldRelease -and (Test-Path -LiteralPath $legacyVenv)) {
        $legacyPython = Join-Path $legacyVenv "Scripts\python.exe"
        $legacyLine = (& $legacyPython -m pip show meshpi |
            Where-Object { $_ -like "Version:*" } |
            Select-Object -First 1)
        $legacyVersion = if ($legacyLine) {
            $legacyLine.Split(":", 2)[1].Trim()
        } else {
            $legacyVersion = "legacy"
        }
        $oldRelease = Join-Path $releasesDir $legacyVersion
        if (-not (Test-Path -LiteralPath $oldRelease)) {
            New-Item -ItemType Directory -Path $oldRelease | Out-Null
            Move-Item -LiteralPath $legacyVenv -Destination (Join-Path $oldRelease "venv")
        }
    }
    if ($oldRelease -and $oldRelease -ne $release) {
        Write-Utf8NoBom $previousFile ($oldRelease + "`n")
    }
    Set-CurrentRelease $currentFile $release

    $meshpiCmd = Join-Path $binDir "meshpi.cmd"
    $daemonCmd = Join-Path $binDir "meshpi-daemon.cmd"
    @"
@echo off
set /p MESHPI_CURRENT=<"$currentFile"
"%MESHPI_CURRENT%\venv\Scripts\meshpi.exe" --env-file "$configFile" %*
"@ | Set-Content -Encoding ASCII $meshpiCmd
    @"
@echo off
set /p MESHPI_CURRENT=<"$currentFile"
"%MESHPI_CURRENT%\venv\Scripts\meshpi.exe" --env-file "$configFile" daemon
"@ | Set-Content -Encoding ASCII $daemonCmd
    $supervisorFile = Join-Path $binDir "meshpi-supervisor.ps1"
    $managerFile = Join-Path $binDir "meshpi-service.ps1"
    $powerShellExe = (Get-Command powershell.exe).Source
    @"
`$ErrorActionPreference = "Continue"
while (`$true) {
    `$current = (Get-Content -Raw "$currentFile").Trim()
    & (Join-Path `$current "venv\Scripts\meshpi.exe") --env-file "$configFile" daemon
    if (`$LASTEXITCODE -eq 0) {
        break
    }
    Start-Sleep -Seconds 5
}
"@ | Set-Content -Encoding UTF8 $supervisorFile
    @"
param([ValidateSet("start", "enable", "disable")][string]`$Action)
`$startup = [Environment]::GetFolderPath("Startup")
`$shortcutFile = Join-Path `$startup "MeshPi Daemon.lnk"
if (`$Action -eq "enable") {
    `$shell = New-Object -ComObject WScript.Shell
    `$shortcut = `$shell.CreateShortcut(`$shortcutFile)
    `$shortcut.TargetPath = "$powerShellExe"
    `$shortcut.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$supervisorFile"'
    `$shortcut.WorkingDirectory = "$dataDir"
    `$shortcut.Description = "MeshPi Meshtastic-bakgrunnsteneste"
    `$shortcut.Save()
} elseif (`$Action -eq "disable") {
    Remove-Item -LiteralPath `$shortcutFile -Force -ErrorAction SilentlyContinue
} elseif (`$Action -eq "start") {
    Start-Process -FilePath "$powerShellExe" -ArgumentList @(
        "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
        "-File", '"$supervisorFile"'
    ) -WorkingDirectory "$dataDir" -WindowStyle Hidden
}
"@ | Set-Content -Encoding UTF8 $managerFile

    if ($env:MESHPI_SKIP_PATH -ne "1") {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $pathParts = @($userPath -split ";" | Where-Object { $_ })
        if ($binDir -notin $pathParts) {
            [Environment]::SetEnvironmentVariable(
                "Path",
                (($pathParts + $binDir) -join ";"),
                "User"
            )
            New-Item -ItemType File -Force `
                -Path (Join-Path $installRoot "path-added-by-meshpi") | Out-Null
        }
    }
    $env:Path = "$binDir;$env:Path"

    if ($env:MESHPI_SKIP_TASK -ne "1") {
        $oldStartup = Join-Path ([Environment]::GetFolderPath("Startup")) "MeshPi-daemon.vbs"
        Remove-Item -LiteralPath $oldStartup -Force -ErrorAction SilentlyContinue
        & $powerShellExe -NoProfile -ExecutionPolicy Bypass `
            -File $managerFile disable
        & schtasks.exe /Delete /TN $taskName /F *> $null
    }

    if (
        $Mode -eq "Always" -and
        -not $SkipAutostart -and
        $env:MESHPI_SKIP_TASK -ne "1"
    ) {
        & $powerShellExe -NoProfile -ExecutionPolicy Bypass `
            -File $managerFile enable
        & $powerShellExe -NoProfile -ExecutionPolicy Bypass `
            -File $managerFile start

        $ready = $false
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            Start-Sleep -Milliseconds 250
            & $meshpiCmd status *> $null
            if (
                $env:MESHPI_FORCE_HEALTH_FAILURE -ne "1" -and
                $LASTEXITCODE -eq 0
            ) {
                $ready = $true
                break
            }
        }
        if (-not $ready) {
            Stop-MeshPiProcesses $installRoot
            if ($oldRelease -and (Test-Path -LiteralPath $oldRelease)) {
                Set-CurrentRelease $currentFile $oldRelease
                & $powerShellExe -NoProfile -ExecutionPolicy Bypass `
                    -File $managerFile start
                throw "Oppdateringa feila. Førre versjon er sett tilbake."
            }
            throw "Oppdateringa feila, og ingen førre versjon finst."
        }
    }

    Write-Host "MeshPi $version er installert i $modeValue-modus." -ForegroundColor Green
    Write-Host "Opne eit nytt terminalvindauge og start med: meshpi"
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
