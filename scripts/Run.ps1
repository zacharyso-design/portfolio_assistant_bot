[CmdletBinding()]
param(
    [string]$ConfigPath = '',
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath = Join-Path $repoRoot 'config.toml' }
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts\Install.ps1 first.' }
if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "Configuration file not found: $ConfigPath" }

if ($Foreground) {
    & $python -m portfolio_assistant --config $ConfigPath serve
    exit $LASTEXITCODE
}

$runtimeDir = Join-Path $env:LOCALAPPDATA 'PortfolioAssistant'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$pidPath = Join-Path $runtimeDir 'server.pid'
if (Test-Path -LiteralPath $pidPath) {
    try {
        $record = Get-Content -Raw -LiteralPath $pidPath | ConvertFrom-Json
        $existing = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
        $sameExecutable = $existing -and $existing.Path -eq ([System.IO.Path]::GetFullPath([string]$record.executable))
        $recordStart = [datetimeoffset]::Parse([string]$record.start_time_utc).UtcDateTime
        $sameStart = $existing -and ([Math]::Abs(($existing.StartTime.ToUniversalTime() - $recordStart).TotalSeconds) -lt 2)
        if ($sameExecutable -and $sameStart) {
            Write-Host "Portfolio Assistant is already running (PID $($record.pid))."
            exit 0
        }
    }
    catch {
        Write-Warning 'Ignoring an unreadable stale Portfolio Assistant PID record.'
    }
    Remove-Item -LiteralPath $pidPath -Force
}
$configuration = & $python -m portfolio_assistant --config $ConfigPath config-test | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Configuration test failed with exit code $LASTEXITCODE." }
$process = Start-Process -FilePath $python -ArgumentList @('-m', 'portfolio_assistant', '--config', $ConfigPath, 'serve') -WindowStyle Hidden -PassThru
$record = @{
    pid = $process.Id
    executable = [System.IO.Path]::GetFullPath($python)
    start_time_utc = $process.StartTime.ToUniversalTime().ToString('o')
}
$record | ConvertTo-Json -Compress | Set-Content -LiteralPath $pidPath -Encoding UTF8
Write-Host "Portfolio Assistant started at http://$($configuration.bind) (PID $($process.Id))."
