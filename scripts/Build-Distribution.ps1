[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$artifactDirectory = Join-Path $repoRoot 'dist'
$artifactPath = Join-Path $artifactDirectory 'CHIO-Portfolio-Assistant-Windows.zip'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts\Install.ps1 first.' }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is required to verify the Node-free frontend bundle.'
}

& git -C $repoRoot ls-files --error-unmatch 'frontend/dist/index.html' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'frontend/dist/index.html must be tracked for Node-free source installation.' }

Push-Location (Join-Path $repoRoot 'frontend')
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE." }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
}

Push-Location $repoRoot
try {
    New-Item -ItemType Directory -Force -Path $artifactDirectory | Out-Null
    $workPath = Join-Path $repoRoot "build\pyinstaller-$PID"
    $distPath = Join-Path $repoRoot "build\distribution-$PID"
    & $python -m PyInstaller --noconfirm --clean --onedir --name PortfolioAssistant `
        --workpath $workPath `
        --distpath $distPath `
        --add-data 'frontend\dist;frontend\dist' `
        --add-data 'portfolio_assistant\migrations;portfolio_assistant\migrations' `
        --collect-all extract_msg --collect-all compressed_rtf --collect-all RTFDE `
        'portfolio_assistant_launcher.py'
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
    $bundle = Join-Path $distPath 'PortfolioAssistant'
    $bundleScripts = Join-Path $bundle 'scripts'
    New-Item -ItemType Directory -Force -Path $bundleScripts | Out-Null
    Copy-Item -LiteralPath 'config.example.toml' -Destination $bundle -Force
    Copy-Item -LiteralPath 'README.md' -Destination $bundle -Force
    Copy-Item -LiteralPath 'scripts\Start-Packaged.cmd' -Destination (Join-Path $bundle 'Start CHIO Portfolio Assistant.cmd') -Force
    Copy-Item -LiteralPath 'scripts\Install-MorningTask.ps1' -Destination $bundleScripts -Force
    Copy-Item -LiteralPath 'scripts\Remove-MorningTask.ps1' -Destination $bundleScripts -Force
    Compress-Archive -Path (Join-Path $bundle '*') -DestinationPath $artifactPath -Force
}
finally {
    Pop-Location
}
Write-Host 'Windows distribution created at dist\CHIO-Portfolio-Assistant-Windows.zip.'
