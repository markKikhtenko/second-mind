param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Create .venv and install second-mind[desktop,speech,build] first."
}

Push-Location $ProjectRoot
try {
    $Version = & $Python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
    if ($LASTEXITCODE -ne 0 -or -not $Version) {
        throw "Cannot read the project version."
    }

    & $Python packaging/generate_icons.py
    if ($LASTEXITCODE -ne 0) { throw "Icon generation failed with exit code $LASTEXITCODE" }

    & $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name second-mind-core `
        --hidden-import keyring.backends.Windows --hidden-import faster_whisper `
        --collect-binaries ctranslate2 --collect-binaries av packaging/core_main.py
    if ($LASTEXITCODE -ne 0) { throw "Core build failed with exit code $LASTEXITCODE" }

    & $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name second-mind-desktop `
        --icon packaging/app-icon.ico `
        --add-data "src/second_mind/assets;second_mind/assets" `
        --hidden-import keyring.backends.Windows packaging/desktop_main.py
    if ($LASTEXITCODE -ne 0) { throw "Desktop build failed with exit code $LASTEXITCODE" }

    & $Python -m PyInstaller --noconfirm --clean --onedir --name second-mind-mcp `
        --hidden-import keyring.backends.Windows `
        --exclude-module faster_whisper --exclude-module ctranslate2 --exclude-module av `
        --exclude-module onnxruntime --exclude-module tokenizers --exclude-module huggingface_hub `
        packaging/mcp_main.py
    if ($LASTEXITCODE -ne 0) { throw "MCP build failed with exit code $LASTEXITCODE" }

    & $Python -m PyInstaller --noconfirm --clean --windowed --onefile --name second-mind-updater `
        packaging/updater_main.py
    if ($LASTEXITCODE -ne 0) { throw "Updater build failed with exit code $LASTEXITCODE" }

    if (-not $SkipInstaller) {
        $Iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
        if (-not $Iscc) {
            $CandidatePaths = @(
                (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
                (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
            )
            $DefaultIscc = $CandidatePaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
            if ($DefaultIscc) {
                $Iscc = Get-Item -LiteralPath $DefaultIscc
            }
        }
        if (-not $Iscc) {
            throw "Inno Setup 6 was not found. Pass -SkipInstaller only for a portable developer build."
        }
        $IsccPath = if ($Iscc.Source) { $Iscc.Source } else { $Iscc.FullName }
        & $IsccPath "/DMyAppVersion=$Version" (Join-Path $PSScriptRoot "second-mind.iss")
        if ($LASTEXITCODE -ne 0) { throw "Installer build failed with exit code $LASTEXITCODE" }
    }
} finally {
    Pop-Location
}
