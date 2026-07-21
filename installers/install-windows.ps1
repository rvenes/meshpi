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

function Write-InstallStep {
    param([int]$Number, [string]$Message)
    Write-Host ("[{0}/8] {1}" -f $Number, $Message) -ForegroundColor Cyan
}

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

function Get-EnvValue {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $match = Get-Content -LiteralPath $Path |
        Where-Object { $_ -match ("^" + [regex]::Escape($Name) + "=(.*)$") } |
        Select-Object -First 1
    if ($match -and $match -match "^[^=]+=(.*)$") { return $Matches[1] }
    return $null
}

function Set-CurrentRelease {
    param([string]$CurrentFile, [string]$Release)
    $temporary = "$CurrentFile.new"
    Write-Utf8NoBom $temporary ($Release + "`n")
    Move-Item -LiteralPath $temporary -Destination $CurrentFile -Force
}

Write-InstallStep 1 "Kontrollerer Python 3.11 eller nyare …"
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
$verifierFile = Join-Path $tempDir "verify-manifest.py"
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
    Write-InstallStep 2 "Hentar og kontrollerer signert versjonsinformasjon …"
    if ($env:MESHPI_MANIFEST_FILE) {
        Copy-Item -LiteralPath $env:MESHPI_MANIFEST_FILE -Destination $manifestFile
    } else {
        Invoke-WebRequest "$BaseUrl/version.json" -OutFile $manifestFile
    }
    $verifier = @'
import base64, hashlib, hmac, json, sys
modulus = int("c1370fa9e2eb0d22e354c58594e369f9db44156f834522bf69a8da523a30ac0d4539e08a30d76e854b40ae693da388af11ca62ee24c1e6f43ec128be550e8b7655d86955ae858b9f30237ba02e2773e9ad2fcfe1644484e909a8805a6c8a289dda69cedbc973d7427278442d8acb1d00a0c5cd242c34404843ea684ece7ad40a59d902633624ae36ae3f4e8c9e401bb887ef650f1fe001f9fd7661841b98a95f67aea496c05054a4c41c287c09d1dd1e94e9c01cc997162a50e02df6d28645d268cceb35daf7ad1e4202b2b1714a71e2b18d0564f12a468c2bb4d7e678a1c4c493de0c945f0f2665efb658238dd4dd617b73acd8e20e4c5f440d2d4ee13617f2c2857c0457e0a3a73aac43d0e23f5c0f56f9042a6d1e6221383481a9bcc952576904895e013a5f12b6c0aa08b9ba911df7be42a4d0a3c31ca98111b4344d8079fdb55a43379fde9968edf9ce7b3554333d5819ad196935e928012d1b20b4aed5ee48d8851dd69458b15998712530b4d91228b06ae109741c0cf4ab723f092e49", 16)
with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
signature = manifest.pop("signature", None)
if not isinstance(signature, dict) or signature.get("algorithm") != "rsa-pkcs1v15-sha256" or signature.get("key_id") != "meshpi-release-2026-01":
    raise SystemExit("Versjonsmanifestet manglar ein gyldig signatur")
try:
    raw = base64.b64decode(signature["value"], validate=True)
except (KeyError, ValueError) as exc:
    raise SystemExit("Ugyldig manifestsignatur") from exc
canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
size = (modulus.bit_length() + 7) // 8
actual = pow(int.from_bytes(raw, "big"), 65537, modulus).to_bytes(size, "big")
digest_info = bytes.fromhex("3031300d060960864801650304020105000420")
digest = hashlib.sha256(canonical).digest()
pad = size - len(digest_info) - len(digest) - 3
expected = b"\x00\x01" + b"\xff" * pad + b"\x00" + digest_info + digest
if len(raw) != size or pad < 8 or not hmac.compare_digest(actual, expected):
    raise SystemExit("Signaturen på versjonsmanifestet stemmer ikkje")
'@
    Write-Utf8NoBom $verifierFile $verifier
    Invoke-NativeChecked $python.Exe (@($python.Prefix) + @($verifierFile, $manifestFile)) `
        "Signaturen på versjonsmanifestet stemmer ikkje."
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
    Write-InstallStep 3 "Lastar ned MeshPi $version og låste avhengigheiter …"
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
    Write-InstallStep 4 "Kontrollerer SHA-256 for alle nedlasta filer …"
    $actualHash = (Get-FileHash -Algorithm SHA256 $wheelFile).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "SHA-256 stemmer ikkje. Installasjonen er avbroten."
    }
    $actualLockHash = (Get-FileHash -Algorithm SHA256 $lockFile).Hash.ToLowerInvariant()
    if ($actualLockHash -ne $expectedLockHash) {
        throw "SHA-256 for låsefila stemmer ikkje. Installasjonen er avbroten."
    }

    if (-not (Test-Path -LiteralPath $configFile)) {
        $tokenBytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($tokenBytes) } finally { $rng.Dispose() }
        $ipcToken = ($tokenBytes | ForEach-Object { $_.ToString("x2") }) -join ""
        $configText = @"
MESHTASTIC_HOST=
MESHTASTIC_PORT=4403
DATABASE_PATH=$dataDir\meshtastic.db
CONNECTIONS_PATH=$dataDir\connections.json
DISCOVERY_SUBNET=
IPC_HOST=127.0.0.1
IPC_PORT=$ipcPort
IPC_TOKEN=$ipcToken
LOG_LEVEL=INFO
UPDATE_URL=$BaseUrl/version.json
UPDATE_TIMEOUT=3
BACKGROUND_MODE=$modeValue
"@
        Write-Utf8NoBom $configFile $configText
    } else {
        Set-EnvValue $configFile "BACKGROUND_MODE" $modeValue
        $ipcToken = Get-EnvValue $configFile "IPC_TOKEN"
        if ($ipcToken -notmatch "^[0-9a-fA-F]{64}$") {
            $tokenBytes = New-Object byte[] 32
            $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $rng.GetBytes($tokenBytes) } finally { $rng.Dispose() }
            $ipcToken = ($tokenBytes | ForEach-Object { $_.ToString("x2") }) -join ""
            Set-EnvValue $configFile "IPC_TOKEN" $ipcToken
        }
    }

    $release = Join-Path $releasesDir $version
    $oldRelease = if (Test-Path -LiteralPath $currentFile) {
        (Get-Content -Raw $currentFile).Trim()
    } else {
        ""
    }
    if ($release -ne $oldRelease) {
        Write-InstallStep 5 (
            "Opprettar programmiljø og installerer avhengigheiter. " +
            "Dette kan ta nokre minutt …"
        )
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
    } else {
        Write-InstallStep 5 "MeshPi $version er alt installert; bruker programfilene på nytt …"
    }
    Write-InstallStep 6 "Kontrollerer installert versjon og køyrer sjølvtest …"
    $releaseMeshPi = Join-Path $release "venv\Scripts\meshpi.exe"
    $installedVersion = (& $releaseMeshPi --version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $installedVersion -ne "MeshPi $version") {
        throw "Pakken rapporterer «$installedVersion», venta MeshPi $version."
    }
    Invoke-NativeChecked $releaseMeshPi @(
        "--env-file", $configFile, "doctor", "--offline"
    ) "MeshPi-sjølvtesten feila."

    Write-InstallStep 7 "Aktiverer MeshPi og konfigurerer bakgrunnstenesta …"
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
        $savedErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "SilentlyContinue"
            & schtasks.exe /Delete /TN $taskName /F *> $null
        } finally {
            $ErrorActionPreference = $savedErrorActionPreference
        }
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

    Write-InstallStep 8 "Installasjonen er ferdig."
    Write-Host "MeshPi $version er installert i $modeValue-modus." -ForegroundColor Green
    Write-Host "Opne eit nytt terminalvindauge og start med: meshpi"
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
