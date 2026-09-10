param(
    [string]$BaseUrl = "https://venes.org/meshpi",
    [ValidateSet("Always", "Session")]
    [string]$Mode = "Always",
    [switch]$SkipAutostart,
    [ValidateRange(0, [int]::MaxValue)]
    [int]$UpdaterProcessId = 0,
    [string]$Language = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$detectedLanguage = "en"
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
    windows_folders = @{ nn = 'Klarte ikkje finne Windows-mappene for gjeldande brukar.'; en = 'Could not locate the Windows folders for the current user.' }
    invalid_release = @{ nn = 'Ugyldig versjonsmappe for Windows-launcheren.'; en = 'Invalid release folder for the Windows launcher.' }
    legacy_locked = @{ nn = 'Den gamle MeshPi-launcheren er i bruk. Lukk andre MeshPi-terminalar og køyr installatøren på nytt.'; en = 'The old MeshPi launcher is in use. Close other MeshPi terminals and run the installer again.' }
    check_python = @{ nn = 'Kontrollerer Python 3.11 eller nyare …'; en = 'Checking for Python 3.11 or newer …' }
    python_required = @{ nn = 'MeshPi krev Python 3.11+. Installer frå https://python.org og køyr skriptet igjen.'; en = 'MeshPi requires Python 3.11+. Install it from https://python.org and run the script again.' }
    install_python = @{ nn = 'Installerer Python 3.11 for gjeldande brukar …'; en = 'Installing Python 3.11 for the current user …' }
    winget_failed = @{ nn = 'Klarte ikkje installere Python 3.11 med winget.'; en = 'Could not install Python 3.11 with winget.' }
    reopen_powershell = @{ nn = 'Python blei installert, men er ikkje tilgjengeleg enno. Opne PowerShell på nytt.'; en = 'Python was installed but is not available yet. Reopen PowerShell.' }
    fetch_manifest = @{ nn = 'Hentar og kontrollerer signert versjonsinformasjon …'; en = 'Downloading and checking signed version information …' }
    signature_mismatch = @{ nn = 'Signaturen på versjonsmanifestet stemmer ikkje.'; en = 'The version manifest signature does not match.' }
    invalid_version = @{ nn = 'Ugyldig versjon i version.json.'; en = 'Invalid version in version.json.' }
    invalid_package = @{ nn = 'Ugyldig pakkenamn i version.json.'; en = 'Invalid package name in version.json.' }
    invalid_sha = @{ nn = 'Ugyldig SHA-256 i version.json.'; en = 'Invalid SHA-256 in version.json.' }
    invalid_lock_hash = @{ nn = 'Ugyldig låsefil-hash i version.json.'; en = 'Invalid lock-file hash in version.json.' }
    download_release = @{ nn = 'Lastar ned MeshPi {0} og låste avhengigheiter …'; en = 'Downloading MeshPi {0} and locked dependencies …' }
    check_hashes = @{ nn = 'Kontrollerer SHA-256 for alle nedlasta filer …'; en = 'Checking SHA-256 for all downloaded files …' }
    sha_mismatch = @{ nn = 'SHA-256 stemmer ikkje. Installasjonen er avbroten.'; en = 'SHA-256 does not match. Installation aborted.' }
    lock_mismatch = @{ nn = 'SHA-256 for låsefila stemmer ikkje. Installasjonen er avbroten.'; en = 'The lock-file SHA-256 does not match. Installation aborted.' }
    create_environment = @{ nn = 'Opprettar programmiljø og installerer avhengigheiter. Dette kan ta nokre minutt …'; en = 'Creating the application environment and installing dependencies. This may take a few minutes …' }
    venv_failed = @{ nn = 'Klarte ikkje opprette Python-miljøet.'; en = 'Could not create the Python environment.' }
    deps_failed = @{ nn = 'Klarte ikkje installere låste avhengigheiter.'; en = 'Could not install locked dependencies.' }
    package_failed = @{ nn = 'Klarte ikkje installere MeshPi-pakken.'; en = 'Could not install the MeshPi package.' }
    already_installed = @{ nn = 'MeshPi {0} er alt installert; bruker programfilene på nytt …'; en = 'MeshPi {0} is already installed; reusing the application files …' }
    selftest = @{ nn = 'Kontrollerer installert versjon og køyrer sjølvtest …'; en = 'Checking the installed version and running the self-test …' }
    wrong_version = @{ nn = 'Pakken rapporterer «{0}», venta MeshPi {1}.'; en = 'The package reports “{0}”; expected MeshPi {1}.' }
    selftest_failed = @{ nn = 'MeshPi-sjølvtesten feila.'; en = 'The MeshPi self-test failed.' }
    activate = @{ nn = 'Aktiverer MeshPi og konfigurerer bakgrunnstenesta …'; en = 'Activating MeshPi and configuring the background service …' }
    powershell_missing = @{ nn = 'Fann ikkje Windows PowerShell på den godkjende systemstien.'; en = 'Could not find Windows PowerShell at the approved system path.' }
    service_description = @{ nn = 'MeshPi Meshtastic-bakgrunnsteneste'; en = 'MeshPi Meshtastic background service' }
    rollback = @{ nn = 'Oppdateringa feila. Førre versjon er sett tilbake.'; en = 'The update failed. The previous version has been restored.' }
    no_rollback = @{ nn = 'Oppdateringa feila, og ingen førre versjon finst.'; en = 'The update failed, and no previous version is available.' }
    complete = @{ nn = 'Installasjonen er ferdig.'; en = 'Installation is complete.' }
    installed = @{ nn = 'MeshPi {0} er installert i {1}-modus.'; en = 'MeshPi {0} is installed in {1} mode.' }
    start = @{ nn = 'Opne eit nytt terminalvindauge og start med: meshpi'; en = 'Open a new terminal window and start with: meshpi' }
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
$env:MESHPI_LANGUAGE = $Language
if (($env:PATHEXT -split ";") -notcontains ".EXE") {
    $env:PATHEXT = [Environment]::GetEnvironmentVariable("PATHEXT", "Machine")
    if (($env:PATHEXT -split ";") -notcontains ".EXE") {
        $env:PATHEXT = ".COM;.EXE;.BAT;.CMD"
    }
}
if (-not $env:LOCALAPPDATA) {
    $env:LOCALAPPDATA = [Environment]::GetFolderPath("LocalApplicationData")
}
if (-not $env:APPDATA) {
    $env:APPDATA = [Environment]::GetFolderPath("ApplicationData")
}
if (-not $env:LOCALAPPDATA -or -not $env:APPDATA) {
    throw (Get-Message windows_folders)
}
if ($SkipAutostart) {
    $Mode = "Session"
}
$modeValue = $Mode.ToLowerInvariant()
$ipcPort = if ($env:MESHPI_IPC_PORT) { $env:MESHPI_IPC_PORT } else { "8765" }

function Write-InstallStep {
    param([int]$Number, [string]$Key, [object[]]$Values = @())
    Write-Host ("[{0}/8] {1}" -f $Number, (Get-Message $Key $Values)) `
        -ForegroundColor Cyan
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
    if (
        $env:MESHPI_PYTHON -and
        (Test-Path -LiteralPath $env:MESHPI_PYTHON -PathType Leaf) -and
        (Test-PythonCommand $env:MESHPI_PYTHON @())
    ) {
        return @{ Exe = $env:MESHPI_PYTHON; Prefix = @() }
    }
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
    param([string]$InstallRoot, [int]$PreserveProcessId = 0)
    $preservedProcessIds = New-Object 'Collections.Generic.HashSet[uint32]'
    $ancestorProcessId = [uint32][Math]::Max(0, $PreserveProcessId)
    for ($depth = 0; $ancestorProcessId -gt 0 -and $depth -lt 32; $depth++) {
        [void]$preservedProcessIds.Add($ancestorProcessId)
        $ancestor = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $ancestorProcessId" -ErrorAction SilentlyContinue
        if (-not $ancestor -or $ancestor.ParentProcessId -eq $ancestorProcessId) {
            break
        }
        $ancestorProcessId = [uint32]$ancestor.ParentProcessId
    }
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $PID -and
            -not $preservedProcessIds.Contains([uint32]$_.ProcessId) -and
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
        @(Get-Content -LiteralPath $Path -Encoding UTF8)
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
    $match = Get-Content -LiteralPath $Path -Encoding UTF8 |
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

function Write-MeshPiLaunchers {
    param(
        [string]$BinDir,
        [string]$Release,
        [string]$ConfigFile
    )
    $releaseName = Split-Path -Leaf $Release
    $versionPattern = (
        "^(?:legacy|" +
        "(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)" +
        "(?:(?:a|b|rc)(?:0|[1-9]\d*))?)$"
    )
    if ($releaseName -notmatch $versionPattern) {
        throw (Get-Message invalid_release)
    }
    $releaseScripts = Join-Path $Release "venv\Scripts"
    $releaseEnvPointer = Join-Path $releaseScripts "meshpi.env-path"
    Write-Utf8NoBom $releaseEnvPointer ($ConfigFile + "`n")
    $relativeMeshPi = "..\releases\$releaseName\venv\Scripts\meshpi.exe"
    $meshpiCmd = Join-Path $BinDir "meshpi.cmd"
    $daemonCmd = Join-Path $BinDir "meshpi-daemon.cmd"
    @"
@echo off
"%~dp0$relativeMeshPi" %*
exit /b %errorlevel%
"@ | Set-Content -Encoding ASCII -LiteralPath $meshpiCmd
    @"
@echo off
"%~dp0$relativeMeshPi" daemon
exit /b %errorlevel%
"@ | Set-Content -Encoding ASCII -LiteralPath $daemonCmd
}

function Start-LegacyLauncherCleanup {
    param(
        [string]$LegacyLauncher,
        [string]$BinDir,
        [string]$PowerShellExe,
        [int]$UpdaterProcessId
    )
    if (-not (Test-Path -LiteralPath $LegacyLauncher -PathType Leaf)) {
        return
    }
    try {
        Remove-Item -LiteralPath $LegacyLauncher -Force -ErrorAction Stop
        return
    } catch {
        if ($UpdaterProcessId -le 0) {
            throw (Get-Message legacy_locked)
        }
    }
    $cleanupFile = Join-Path $BinDir "meshpi-launcher-cleanup.ps1"
    @'
param(
    [int]$UpdaterProcessId,
    [string]$LegacyLauncher
)
$ErrorActionPreference = "SilentlyContinue"
Wait-Process -Id $UpdaterProcessId -Timeout 120 -ErrorAction SilentlyContinue
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Remove-Item -LiteralPath $LegacyLauncher -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $LegacyLauncher)) {
        break
    }
    Start-Sleep -Milliseconds 250
}
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
'@ | Set-Content -Encoding UTF8 -LiteralPath $cleanupFile
    $cleanupStart = New-Object Diagnostics.ProcessStartInfo
    $cleanupStart.FileName = $PowerShellExe
    $cleanupStart.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' +
        $cleanupFile + '" -UpdaterProcessId ' + $UpdaterProcessId + ' -LegacyLauncher "' +
        $LegacyLauncher + '"'
    $cleanupStart.WorkingDirectory = $BinDir
    $cleanupStart.UseShellExecute = $false
    $cleanupStart.CreateNoWindow = $true
    $cleanupStart.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    [Diagnostics.Process]::Start($cleanupStart).Dispose()
}

# Write-InstallStep 1 "Kontrollerer Python 3.11 eller nyare …"
Write-InstallStep 1 check_python
$python = Find-MeshPiPython
if (-not $python) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw (Get-Message python_required)
    }
    Write-Host (Get-Message install_python) -ForegroundColor Cyan
    Invoke-NativeChecked $winget.Source @(
        "install", "--id", "Python.Python.3.11", "--exact", "--scope", "user",
        "--accept-package-agreements", "--accept-source-agreements"
    ) (Get-Message winget_failed)
    $python = Find-MeshPiPython
    if (-not $python) {
        throw (Get-Message reopen_powershell)
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
$languageFile = if ($env:MESHPI_LANGUAGE_FILE) {
    $env:MESHPI_LANGUAGE_FILE
} else {
    Join-Path $installRoot "language.json"
}

function ConvertTo-PowerShellLiteral {
    param([string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}
$freshInstall = -not (Test-Path -LiteralPath $installRoot) -and `
    -not (Test-Path -LiteralPath $configRoot)
$tempDir = Join-Path ([IO.Path]::GetTempPath()) ("meshpi-" + [guid]::NewGuid())
$manifestFile = Join-Path $tempDir "version.json"
$verifierFile = Join-Path $tempDir "verify-manifest.py"
$lockFile = Join-Path $tempDir "requirements-windows.txt"
$taskName = if ($env:MESHPI_TASK_NAME) {
    $env:MESHPI_TASK_NAME
} else {
    "MeshPi Daemon"
}
if ($UpdaterProcessId -le 0) {
    $installerProcess = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $PID" -ErrorAction SilentlyContinue
    if ($installerProcess -and $installerProcess.ParentProcessId -gt 0) {
        $UpdaterProcessId = [int]$installerProcess.ParentProcessId
    }
}

New-Item -ItemType Directory -Force -Path @(
    $tempDir, $installRoot, $configRoot, $binDir, $dataDir, $releasesDir
) | Out-Null

try {
    Write-InstallStep 2 fetch_manifest
    if ($env:MESHPI_MANIFEST_FILE) {
        Copy-Item -LiteralPath $env:MESHPI_MANIFEST_FILE -Destination $manifestFile
    } else {
        Invoke-WebRequest "$BaseUrl/version.json" -OutFile $manifestFile
    }
    $verifier = @'
import base64, hashlib, hmac, json, sys
language = sys.argv[2]
messages = {
    "nn": {
        "missing_signature": "Versjonsmanifestet manglar ein gyldig signatur",
        "revoked_key": "Tilbakekalla signeringsnøkkel",
        "unknown_key": "Ukjend signeringsnøkkel",
        "invalid_signature": "Ugyldig manifestsignatur",
        "signature_mismatch": "Signaturen på versjonsmanifestet stemmer ikkje",
    },
    "en": {
        "missing_signature": "The version manifest is missing a valid signature",
        "revoked_key": "Revoked signing key",
        "unknown_key": "Unknown signing key",
        "invalid_signature": "Invalid manifest signature",
        "signature_mismatch": "The version manifest signature does not match",
    },
}[language]
current_modulus = int("c1370fa9e2eb0d22e354c58594e369f9db44156f834522bf69a8da523a30ac0d4539e08a30d76e854b40ae693da388af11ca62ee24c1e6f43ec128be550e8b7655d86955ae858b9f30237ba02e2773e9ad2fcfe1644484e909a8805a6c8a289dda69cedbc973d7427278442d8acb1d00a0c5cd242c34404843ea684ece7ad40a59d902633624ae36ae3f4e8c9e401bb887ef650f1fe001f9fd7661841b98a95f67aea496c05054a4c41c287c09d1dd1e94e9c01cc997162a50e02df6d28645d268cceb35daf7ad1e4202b2b1714a71e2b18d0564f12a468c2bb4d7e678a1c4c493de0c945f0f2665efb658238dd4dd617b73acd8e20e4c5f440d2d4ee13617f2c2857c0457e0a3a73aac43d0e23f5c0f56f9042a6d1e6221383481a9bcc952576904895e013a5f12b6c0aa08b9ba911df7be42a4d0a3c31ca98111b4344d8079fdb55a43379fde9968edf9ce7b3554333d5819ad196935e928012d1b20b4aed5ee48d8851dd69458b15998712530b4d91228b06ae109741c0cf4ab723f092e49", 16)
trusted_keys = {"meshpi-release-2026-01": (65537, current_modulus)}
revoked_key_ids = set()
with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
signature = manifest.pop("signature", None)
if not isinstance(signature, dict) or signature.get("algorithm") != "rsa-pkcs1v15-sha256":
    raise SystemExit(messages["missing_signature"])
key_id = str(signature.get("key_id", ""))
if key_id in revoked_key_ids:
    raise SystemExit(messages["revoked_key"])
key = trusted_keys.get(key_id)
if key is None:
    raise SystemExit(messages["unknown_key"])
exponent, modulus = key
try:
    raw = base64.b64decode(signature["value"], validate=True)
except (KeyError, ValueError) as exc:
    raise SystemExit(messages["invalid_signature"]) from exc
canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
size = (modulus.bit_length() + 7) // 8
actual = pow(int.from_bytes(raw, "big"), exponent, modulus).to_bytes(size, "big")
digest_info = bytes.fromhex("3031300d060960864801650304020105000420")
digest = hashlib.sha256(canonical).digest()
pad = size - len(digest_info) - len(digest) - 3
expected = b"\x00\x01" + b"\xff" * pad + b"\x00" + digest_info + digest
if len(raw) != size or pad < 8 or not hmac.compare_digest(actual, expected):
    raise SystemExit(messages["signature_mismatch"])
'@
    Write-Utf8NoBom $verifierFile $verifier
    Invoke-NativeChecked $python.Exe `
        (@($python.Prefix) + @($verifierFile, $manifestFile, $Language)) `
        (Get-Message signature_mismatch)
    $manifest = Get-Content -Raw -Encoding UTF8 $manifestFile | ConvertFrom-Json
    $version = [string]$manifest.latest_version
    if (
        $version -notmatch (
            "^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)" +
            "(?:(?:a|b|rc)(0|[1-9]\d*))?$"
        )
    ) {
        throw (Get-Message invalid_version)
    }
    $packageUrl = [string]$manifest.package.url
    $packageFilename = [string]$manifest.package.filename
    if ($packageFilename -cne "meshpi-$version-py3-none-any.whl") {
        throw (Get-Message invalid_package)
    }
    $expectedHash = ([string]$manifest.package.sha256).ToLowerInvariant()
    $lockUrl = [string]$manifest.locks.windows.url
    $expectedLockHash = ([string]$manifest.locks.windows.sha256).ToLowerInvariant()
    if ($expectedHash -notmatch "^[0-9a-f]{64}$") {
        throw (Get-Message invalid_sha)
    }
    if ($expectedLockHash -notmatch "^[0-9a-f]{64}$") {
        throw (Get-Message invalid_lock_hash)
    }
    $wheelFile = Join-Path $tempDir $packageFilename
    Write-InstallStep 3 download_release @($version)
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
    Write-InstallStep 4 check_hashes
    $actualHash = (Get-FileHash -Algorithm SHA256 $wheelFile).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw (Get-Message sha_mismatch)
    }
    $actualLockHash = (Get-FileHash -Algorithm SHA256 $lockFile).Hash.ToLowerInvariant()
    if ($actualLockHash -ne $expectedLockHash) {
        throw (Get-Message lock_mismatch)
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
IPC_TRANSPORT=tcp
IPC_SOCKET_PATH=
IPC_SOCKET_GID=
IPC_TOKEN=$ipcToken
LOG_LEVEL=INFO
LOG_FILE=$dataDir\meshpi.log
LOG_MAX_BYTES=5242880
LOG_BACKUP_COUNT=3
UPDATE_URL=$BaseUrl/version.json
UPDATE_TIMEOUT=3
BACKGROUND_MODE=$modeValue
"@
        Write-Utf8NoBom $configFile $configText
    } else {
        Set-EnvValue $configFile "BACKGROUND_MODE" $modeValue
        if (-not (Get-EnvValue $configFile "LOG_FILE")) {
            Set-EnvValue $configFile "LOG_FILE" (Join-Path $dataDir "meshpi.log")
        }
        Set-EnvValue $configFile "IPC_TRANSPORT" "tcp"
        Set-EnvValue $configFile "IPC_SOCKET_PATH" ""
        Set-EnvValue $configFile "IPC_SOCKET_GID" ""
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
        ([IO.File]::ReadAllText(
            $currentFile,
            [Text.Encoding]::UTF8
        )).Trim()
    } else {
        ""
    }
    if ($release -ne $oldRelease) {
        Write-InstallStep 5 create_environment
        if (Test-Path -LiteralPath $release) {
            Remove-Item -LiteralPath $release -Recurse -Force
        }
        $venvArguments = @($python.Prefix) + @("-m", "venv", (Join-Path $release "venv"))
        Invoke-NativeChecked $python.Exe $venvArguments (Get-Message venv_failed)
        $venvPython = Join-Path $release "venv\Scripts\python.exe"
        Invoke-NativeChecked $venvPython @(
            "-I", "-c",
            "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('meshpi.bootstrap',run_name='__main__')",
            $wheelFile, $lockFile
        ) (Get-Message deps_failed)
        Invoke-NativeChecked $venvPython @(
            "-m", "pip", "install", "-q", "--no-deps", $wheelFile
        ) (Get-Message package_failed)
    } else {
        Write-InstallStep 5 already_installed @($version)
    }
    Write-InstallStep 6 selftest
    $releaseMeshPi = Join-Path $release "venv\Scripts\meshpi.exe"
    $installedVersion = (& $releaseMeshPi --version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $installedVersion -ne "MeshPi $version") {
        throw (Get-Message wrong_version @($installedVersion, $version))
    }
    Invoke-NativeChecked $releaseMeshPi @(
        "--env-file", $configFile, "doctor", "--offline"
    ) (Get-Message selftest_failed)

    Write-InstallStep 7 activate
    Stop-MeshPiProcesses $installRoot $UpdaterProcessId
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

    $nativeLauncher = Join-Path $binDir "meshpi.exe"
    $meshpiCmd = Join-Path $binDir "meshpi.cmd"
    $daemonCmd = Join-Path $binDir "meshpi-daemon.cmd"
    $launcherFile = Join-Path $binDir "meshpi-launcher.ps1"
    $envPointerFile = Join-Path $binDir "meshpi.env-path"
    Remove-Item -LiteralPath $launcherFile -Force -ErrorAction SilentlyContinue
    Write-Utf8NoBom $envPointerFile ($configFile + "`n")
    Write-MeshPiLaunchers $binDir $release $configFile
    $supervisorFile = Join-Path $binDir "meshpi-supervisor.ps1"
    $managerFile = Join-Path $binDir "meshpi-service.ps1"
    $powerShellExe = Join-Path ([Environment]::SystemDirectory) `
        "WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path -LiteralPath $powerShellExe -PathType Leaf)) {
        throw (Get-Message powershell_missing)
    }
    $startupDir = if ($env:MESHPI_STARTUP_DIR) {
        $env:MESHPI_STARTUP_DIR
    } else {
        [Environment]::GetFolderPath("Startup")
    }
    New-Item -ItemType Directory -Force -Path $startupDir | Out-Null
    $currentLiteral = ConvertTo-PowerShellLiteral $currentFile
    $configLiteral = ConvertTo-PowerShellLiteral $configFile
    $startupLiteral = ConvertTo-PowerShellLiteral $startupDir
    $pythonwLiteral = ConvertTo-PowerShellLiteral (Join-Path $release "venv\Scripts\pythonw.exe")
    $powershellLiteral = ConvertTo-PowerShellLiteral $powerShellExe
    $dataLiteral = ConvertTo-PowerShellLiteral $dataDir
    $supervisorLiteral = ConvertTo-PowerShellLiteral $supervisorFile
    $descriptionLiteral = ConvertTo-PowerShellLiteral (Get-Message service_description)
    @"
`$ErrorActionPreference = "Stop"
`$log = Join-Path $dataLiteral "meshpi-supervisor.log"
function Write-SupervisorEvent([string]`$message) {
    try {
        if ((Test-Path -LiteralPath `$log) -and (Get-Item -LiteralPath `$log).Length -gt 524288) {
            Move-Item -LiteralPath `$log -Destination (`$log + '.1') -Force
        }
        Add-Content -LiteralPath `$log -Encoding UTF8 -Value ((Get-Date -Format o) + ' ' + `$message)
    } catch { }
}
Write-SupervisorEvent "Supervisor started"
while (`$true) {
    try {
        `$current = ([IO.File]::ReadAllText($currentLiteral, [Text.Encoding]::UTF8)).Trim()
        `$daemonStart = New-Object Diagnostics.ProcessStartInfo
        `$daemonStart.FileName = Join-Path `$current "venv\Scripts\pythonw.exe"
        `$daemonStart.Arguments = '-I -c "import os,sys; sys.stdin=open(os.devnull); sys.stdout=sys.stderr=open(os.devnull,''w''); from meshpi.cli import main; main()" --env-file "' + $configLiteral + '" daemon'
        `$daemonStart.UseShellExecute = `$false
        `$daemonStart.CreateNoWindow = `$true
        `$daemon = [Diagnostics.Process]::Start(`$daemonStart)
        try {
            `$daemon.WaitForExit()
            `$exitCode = `$daemon.ExitCode
        } finally { `$daemon.Dispose() }
        Write-SupervisorEvent ("Daemon exited: " + `$exitCode)
        if (`$exitCode -eq 0) { break }
    } catch {
        Write-SupervisorEvent "Daemon launch failed; retrying"
    }
    Start-Sleep -Seconds 5
}
"@ | Set-Content -Encoding UTF8 -LiteralPath $supervisorFile
    @"
param([ValidateSet("start", "enable", "disable")][string]`$Action)
`$startup = $startupLiteral
`$supervisor = $supervisorLiteral
`$shortcutFile = Join-Path `$startup "MeshPi Daemon.lnk"
if (`$Action -eq "enable") {
    `$shell = New-Object -ComObject WScript.Shell
    `$shortcut = `$shell.CreateShortcut(`$shortcutFile)
    `$shortcut.TargetPath = $pythonwLiteral
    `$shortcut.Arguments = '-I -m meshpi.windows_background "' + `$supervisor + '"'
    `$shortcut.WorkingDirectory = $dataLiteral
    `$shortcut.Description = $descriptionLiteral
    `$shortcut.Save()
} elseif (`$Action -eq "disable") {
    Remove-Item -LiteralPath `$shortcutFile -Force -ErrorAction SilentlyContinue
} elseif (`$Action -eq "start") {
    `$startInfo = New-Object Diagnostics.ProcessStartInfo
    `$startInfo.FileName = $pythonwLiteral
    `$startInfo.Arguments = '-I -m meshpi.windows_background "' + `$supervisor + '"'
    `$startInfo.WorkingDirectory = $dataLiteral
    `$startInfo.UseShellExecute = `$false
    `$startInfo.CreateNoWindow = `$true
    `$startInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    [Diagnostics.Process]::Start(`$startInfo).Dispose()
}
"@ | Set-Content -Encoding UTF8 -LiteralPath $managerFile

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
        $oldStartup = Join-Path $startupDir "MeshPi-daemon.vbs"
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
            Stop-MeshPiProcesses $installRoot $UpdaterProcessId
            if ($oldRelease -and (Test-Path -LiteralPath $oldRelease)) {
                Set-CurrentRelease $currentFile $oldRelease
                Write-MeshPiLaunchers $binDir $oldRelease $configFile
                Remove-Item -LiteralPath $previousFile -Force `
                    -ErrorAction SilentlyContinue
                & $powerShellExe -NoProfile -ExecutionPolicy Bypass `
                    -File $managerFile start
                throw (Get-Message rollback)
            }
            throw (Get-Message no_rollback)
        }
    }

    Start-LegacyLauncherCleanup `
        $nativeLauncher $binDir $powerShellExe $UpdaterProcessId
    if ($freshInstall -and -not (Test-Path -LiteralPath $languageFile)) {
        $languageTemporary = "$languageFile.new"
        Write-Utf8NoBom $languageTemporary `
            ('{"language":"' + $Language + '"}' + "`n")
        Move-Item -LiteralPath $languageTemporary `
            -Destination $languageFile -Force
    }
    Write-InstallStep 8 complete
    Write-Host (Get-Message installed @($version, $modeValue)) `
        -ForegroundColor Green
    Write-Host (Get-Message start)
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
