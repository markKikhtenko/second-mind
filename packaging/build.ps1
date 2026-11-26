$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Create .venv and install second-mind[desktop,speech,build] first."
}

Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onedir --name second-mind-core `
        --hidden-import keyring.backends.Windows --hidden-import faster_whisper `
        --collect-binaries ctranslate2 --collect-binaries av packaging/core_main.py
    if ($LASTEXITCODE -ne 0) { throw "Core build failed with exit code $LASTEXITCODE" }
    & $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name second-mind-desktop `
        --hidden-import keyring.backends.Windows packaging/desktop_main.py
    if ($LASTEXITCODE -ne 0) { throw "Desktop build failed with exit code $LASTEXITCODE" }
    & $Python -m PyInstaller --noconfirm --clean --onedir --name second-mind-mcp `
        --hidden-import keyring.backends.Windows packaging/mcp_main.py
    if ($LASTEXITCODE -ne 0) { throw "MCP build failed with exit code $LASTEXITCODE" }

    $Iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($Iscc) {
        & $Iscc.Source (Join-Path $PSScriptRoot "second-mind.iss")
    } else {
        Write-Warning "Inno Setup not found; portable executables were built in dist/."
    }
} finally {
    Pop-Location
}
