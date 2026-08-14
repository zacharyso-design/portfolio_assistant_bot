[CmdletBinding()]
param(
    [string]$ConfigPath = '',
    [string]$PythonExecutable = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath = Join-Path $repoRoot 'config.toml' }
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    if ($PythonExecutable) {
        & $PythonExecutable -m venv (Join-Path $repoRoot '.venv')
    }
    else {
        py -m venv (Join-Path $repoRoot '.venv')
    }
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed with exit code $LASTEXITCODE." }
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed with exit code $LASTEXITCODE." }
& $venvPython -m pip install -r (Join-Path $repoRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed with exit code $LASTEXITCODE." }
& $venvPython -m pip install -e $repoRoot
if ($LASTEXITCODE -ne 0) { throw "Application installation failed with exit code $LASTEXITCODE." }

$frontendIndex = Join-Path $repoRoot 'frontend\dist\index.html'
if (-not (Test-Path -LiteralPath $frontendIndex)) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw 'Compiled frontend assets are missing and npm is unavailable. Use the Windows distribution artifact, which includes compiled assets.'
    }
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
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Copy-Item -LiteralPath (Join-Path $repoRoot 'config.example.toml') -Destination $ConfigPath
    Write-Warning "Created $ConfigPath. Set one_drive_root and the approved internal LLM values before starting."
    exit 0
}

& $venvPython -m portfolio_assistant --config $ConfigPath migrate
if ($LASTEXITCODE -ne 0) { throw "Database migration failed with exit code $LASTEXITCODE." }
Write-Host 'Portfolio Assistant installation completed.'
