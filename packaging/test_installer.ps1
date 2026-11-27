param(
    [Parameter(Mandatory = $true)][string]$CurrentInstaller,
    [Parameter(Mandatory = $true)][string]$CurrentVersion,
    [string]$PreviousInstaller,
    [string]$PreviousVersion
)

$ErrorActionPreference = "Stop"
$TestRoot = Join-Path $env:RUNNER_TEMP "second-mind-installer-test"
$ResolvedRunnerTemp = [System.IO.Path]::GetFullPath($env:RUNNER_TEMP)
$ResolvedTestRoot = [System.IO.Path]::GetFullPath($TestRoot)
if (-not $ResolvedTestRoot.StartsWith($ResolvedRunnerTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Installer test directory escaped RUNNER_TEMP."
}
if (Test-Path -LiteralPath $ResolvedTestRoot) {
    Remove-Item -LiteralPath $ResolvedTestRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $ResolvedTestRoot | Out-Null

$InstallDir = Join-Path $ResolvedTestRoot "application"
$CoreProcess = $null
$ControlDataCreated = $false

function Install-SecondMind([string]$Installer, [bool]$RequireUpdater) {
    $process = Start-Process -FilePath $Installer -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CURRENTUSER",
        "/TASKS=",
        "/DIR=$InstallDir"
    ) -PassThru -Wait -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "Installer failed with exit code $($process.ExitCode)."
    }
    $core = Join-Path $InstallDir "second-mind-core\second-mind-core.exe"
    $desktop = Join-Path $InstallDir "second-mind-desktop\second-mind-desktop.exe"
    $updater = Join-Path $InstallDir "second-mind-updater.exe"
    if (-not (Test-Path -LiteralPath $core) -or -not (Test-Path -LiteralPath $desktop)) {
        throw "The installed application is incomplete."
    }
    if ($RequireUpdater -and -not (Test-Path -LiteralPath $updater)) {
        throw "The installed updater is missing."
    }
}

function Start-Core([string]$DataDir, [string]$ExpectedVersion) {
    if ($DataDir) {
        $env:SECOND_MIND_DATA_DIR = $DataDir
    } else {
        Remove-Item Env:SECOND_MIND_DATA_DIR -ErrorAction SilentlyContinue
    }
    $core = Join-Path $InstallDir "second-mind-core\second-mind-core.exe"
    # Match the production desktop launcher: the Python process exits after
    # creating a detached frozen Core process with no inherited stdio handles.
    $env:SECOND_MIND_TEST_CORE = $core
    & python -c @'
import os
import subprocess

subprocess.Popen(
    [os.environ["SECOND_MIND_TEST_CORE"]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
)
'@
    if ($LASTEXITCODE -ne 0) {
        throw "Core launcher failed with exit code $LASTEXITCODE."
    }
    Remove-Item Env:SECOND_MIND_TEST_CORE -ErrorAction SilentlyContinue
    $resolvedCore = [System.IO.Path]::GetFullPath($core)
    $processDeadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
        $coreProcessInfo = Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and
            [System.IO.Path]::GetFullPath($_.ExecutablePath).Equals(
                $resolvedCore,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        } | Select-Object -First 1
        if ($coreProcessInfo) {
            $script:CoreProcess = Get-Process -Id $coreProcessInfo.ProcessId
            break
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $processDeadline)
    if (-not $script:CoreProcess) {
        throw "Core process did not start."
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(180)
    do {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/ping" -TimeoutSec 3
            if ($health.version -eq $ExpectedVersion) {
                return
            }
        } catch {
            try {
                # v0.1.0 predates /api/ping; OpenAPI metadata is lightweight
                # and does not call external LLM health checks.
                $legacyMetadata = Invoke-RestMethod -Uri "http://127.0.0.1:8765/openapi.json" -TimeoutSec 3
                if ($legacyMetadata.info.version -eq $ExpectedVersion) {
                    return
                }
            } catch {
                Start-Sleep -Milliseconds 500
            }
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Core $ExpectedVersion did not become healthy."
}

function Stop-Core {
    try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/api/system/shutdown" -TimeoutSec 5 | Out-Null
    } catch {
    }
    if ($script:CoreProcess) {
        $script:CoreProcess.WaitForExit(30000) | Out-Null
        if (-not $script:CoreProcess.HasExited) {
            $script:CoreProcess.Kill($true)
            throw "Core required forced termination."
        }
        $script:CoreProcess = $null
    }
}

function Test-DesktopStartup {
    $desktop = Join-Path $InstallDir "second-mind-desktop\second-mind-desktop.exe"
    $desktopProcess = Start-Process -FilePath $desktop -ArgumentList @(
        "--theme", "dark"
    ) -PassThru -WindowStyle Hidden
    if ($desktopProcess.WaitForExit(12000)) {
        throw "Desktop crashed during startup with exit code $($desktopProcess.ExitCode)."
    }
    Stop-Process -Id $desktopProcess.Id
    $desktopProcess.WaitForExit(10000) | Out-Null
}

function Uninstall-SecondMind {
    $uninstaller = Join-Path $InstallDir "unins000.exe"
    if (Test-Path -LiteralPath $uninstaller) {
        $process = Start-Process -FilePath $uninstaller -ArgumentList @(
            "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
        ) -PassThru -Wait -WindowStyle Hidden
        if ($process.ExitCode -ne 0) {
            throw "Uninstaller failed with exit code $($process.ExitCode)."
        }
    }
}

try {
    $CleanData = Join-Path $ResolvedTestRoot "clean-data"
    Install-SecondMind $CurrentInstaller $true
    Start-Core $CleanData $CurrentVersion
    $created = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/api/tasks" `
        -ContentType "application/json" -Body '{"title":"clean-install-marker"}'
    if (-not $created.id) { throw "Clean install API smoke test failed." }
    Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:8765/api/settings" `
        -ContentType "application/json" `
        -Body '{"setup_completed":true,"automatic_update_checks":true}' | Out-Null
    Test-DesktopStartup
    Stop-Core
    Uninstall-SecondMind
    if (-not (Test-Path -LiteralPath (Join-Path $CleanData "second-mind.db"))) {
        throw "Uninstall removed user data."
    }

    if ($PreviousInstaller) {
        $UpgradeData = Join-Path $ResolvedTestRoot "upgrade-data"
        $ControlData = Join-Path $env:LOCALAPPDATA "Second Mind\Second Mind"
        $ResolvedControlData = [System.IO.Path]::GetFullPath($ControlData)
        $ResolvedLocalAppData = [System.IO.Path]::GetFullPath($env:LOCALAPPDATA)
        if (-not $ResolvedControlData.StartsWith($ResolvedLocalAppData, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Previous-version control directory escaped LOCALAPPDATA."
        }
        if (Test-Path -LiteralPath $ResolvedControlData) {
            throw "Refusing to overwrite existing user data at $ResolvedControlData."
        }
        New-Item -ItemType Directory -Path $ResolvedControlData | Out-Null
        $ControlDataCreated = $true
        @{ data_dir = $UpgradeData } | ConvertTo-Json |
            Set-Content -LiteralPath (Join-Path $ResolvedControlData "config.json") -Encoding UTF8
        Install-SecondMind $PreviousInstaller $false
        Start-Core "" $PreviousVersion
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/api/tasks" `
            -ContentType "application/json" -Body '{"title":"upgrade-preservation-marker"}' | Out-Null
        Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:8765/api/settings" `
            -ContentType "application/json" -Body '{"setup_completed":true}' | Out-Null
        $UpgradeDatabase = Join-Path $UpgradeData "second-mind.db"
        if (-not (Test-Path -LiteralPath $UpgradeDatabase)) {
            throw "Previous version did not create its database in the isolated profile."
        }
        Stop-Core

        Install-SecondMind $CurrentInstaller $true
        Start-Core "" $CurrentVersion
        $tasks = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/tasks"
        $settings = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/settings"
        if (-not ($tasks.title -contains "upgrade-preservation-marker")) {
            throw "Upgrade did not preserve the database."
        }
        if (-not $settings.setup_completed) {
            throw "Upgrade did not preserve settings."
        }
        Stop-Core
        Uninstall-SecondMind
        if (-not (Test-Path -LiteralPath $UpgradeDatabase)) {
            throw "Upgrade uninstall removed user data."
        }
        Remove-Item -LiteralPath $ResolvedControlData -Recurse -Force
        $ControlDataCreated = $false
    }
} finally {
    if ($script:CoreProcess) {
        try { Stop-Core } catch { $script:CoreProcess.Kill($true) }
    }
    Remove-Item Env:SECOND_MIND_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:SECOND_MIND_TEST_CORE -ErrorAction SilentlyContinue
    if ($ControlDataCreated -and $ControlData -and (Test-Path -LiteralPath $ControlData)) {
        Remove-Item -LiteralPath $ControlData -Recurse -Force
    }
}
