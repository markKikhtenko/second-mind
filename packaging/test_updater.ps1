param(
    [Parameter(Mandatory = $true)][string]$CurrentInstaller,
    [Parameter(Mandatory = $true)][string]$CurrentVersion,
    [Parameter(Mandatory = $true)][string]$ReleaseMetadataDir
)

$ErrorActionPreference = "Stop"
$TestRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $env:RUNNER_TEMP "second-mind-updater-test")
)
$ResolvedRunnerTemp = [System.IO.Path]::GetFullPath($env:RUNNER_TEMP)
if (-not $TestRoot.StartsWith($ResolvedRunnerTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Updater test directory escaped RUNNER_TEMP."
}

function Stop-TestProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and
        [System.IO.Path]::GetFullPath($_.ExecutablePath).StartsWith(
            $TestRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Stop-TestProcesses
if (Test-Path -LiteralPath $TestRoot) {
    Remove-Item -LiteralPath $TestRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $TestRoot | Out-Null

$InstallDir = Join-Path $TestRoot "application"
$DataDir = Join-Path $TestRoot "user-data"
$StatePath = Join-Path $DataDir "updates\last-update.json"
$env:SECOND_MIND_DATA_DIR = $DataDir

function Install-Application {
    $process = Start-Process -FilePath $CurrentInstaller -ArgumentList @(
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
}

function Start-Core {
    $core = Join-Path $InstallDir "second-mind-core\second-mind-core.exe"
    $env:SECOND_MIND_TEST_COMMAND = @($core) | ConvertTo-Json -Compress
    & python -c @'
import json
import os
import subprocess

subprocess.Popen(
    json.loads(os.environ["SECOND_MIND_TEST_COMMAND"]),
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
    Remove-Item Env:SECOND_MIND_TEST_COMMAND -ErrorAction SilentlyContinue
    $deadline = [DateTime]::UtcNow.AddSeconds(180)
    do {
        try {
            $ping = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/ping" -TimeoutSec 3
            if ($ping.version -eq $CurrentVersion) { return }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Core did not start during updater integration test."
}

function Stop-Core {
    try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/api/system/shutdown" `
            -TimeoutSec 5 | Out-Null
    } catch {
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/ping" -TimeoutSec 2 | Out-Null
            Start-Sleep -Milliseconds 300
        } catch {
            return
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Core did not stop during updater integration test."
}

function Uninstall-Application {
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
    Install-Application
    Start-Core
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/api/tasks" `
        -ContentType "application/json" -Body '{"title":"updater-e2e-marker"}' | Out-Null
    Stop-Core

    $manifestPath = Join-Path $ReleaseMetadataDir "second-mind-release.json"
    $signaturePath = Join-Path $ReleaseMetadataDir "second-mind-release.json.sig"
    $manifestBytes = [System.IO.File]::ReadAllBytes($manifestPath)
    $signatureBytes = [System.IO.File]::ReadAllBytes($signaturePath)
    $manifest = [System.Text.Encoding]::UTF8.GetString($manifestBytes) | ConvertFrom-Json
    $metadataPath = Join-Path $DataDir "updates\installer.exe.json"
    New-Item -ItemType Directory -Path (Split-Path $metadataPath -Parent) -Force | Out-Null
    @{
        schema_version = 1
        version = $manifest.version
        sha256 = $manifest.installer.sha256
        manifest = [Convert]::ToBase64String($manifestBytes)
        signature = [Convert]::ToBase64String($signatureBytes)
    } | ConvertTo-Json | Set-Content -LiteralPath $metadataPath -Encoding UTF8

    $updater = Join-Path $DataDir "updates\second-mind-updater.exe"
    Copy-Item -LiteralPath (Join-Path $InstallDir "second-mind-updater.exe") -Destination $updater
    $command = @(
        $updater,
        "--installer", $CurrentInstaller,
        "--metadata", $metadataPath,
        "--install-dir", $InstallDir,
        "--data-dir", $DataDir,
        "--api-url", "http://127.0.0.1:8765",
        "--desktop-pid", "0",
        "--current-version", $CurrentVersion,
        "--no-restart"
    )
    $env:SECOND_MIND_TEST_COMMAND = $command | ConvertTo-Json -Compress
    & python -c @'
import json
import os
import subprocess

subprocess.Popen(
    json.loads(os.environ["SECOND_MIND_TEST_COMMAND"]),
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
)
'@
    if ($LASTEXITCODE -ne 0) {
        throw "Updater launcher failed with exit code $LASTEXITCODE."
    }
    Remove-Item Env:SECOND_MIND_TEST_COMMAND -ErrorAction SilentlyContinue

    $deadline = [DateTime]::UtcNow.AddMinutes(15)
    $state = $null
    do {
        if (Test-Path -LiteralPath $StatePath) {
            try { $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json } catch {}
            if ($state.status -in @("succeeded", "failed", "rolled_back", "rollback_failed")) {
                break
            }
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $state -or $state.status -ne "succeeded") {
        $details = if ($state) { $state | ConvertTo-Json -Compress } else { "no state" }
        throw "Detached updater did not succeed: $details"
    }
    if (-not (Test-Path -LiteralPath $state.backup_dir)) {
        throw "Updater did not create an application backup."
    }
    if (-not (Test-Path -LiteralPath $state.database_backup)) {
        throw "Updater did not create a database backup."
    }

    Start-Core
    $tasks = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/tasks" -TimeoutSec 10
    if (-not ($tasks.title -contains "updater-e2e-marker")) {
        throw "Updater did not preserve user data."
    }
    Stop-Core
    Uninstall-Application
    if (-not (Test-Path -LiteralPath (Join-Path $DataDir "second-mind.db"))) {
        throw "Uninstall after update removed user data."
    }
} finally {
    try { Stop-Core } catch {}
    Stop-TestProcesses
    Remove-Item Env:SECOND_MIND_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:SECOND_MIND_TEST_COMMAND -ErrorAction SilentlyContinue
}
