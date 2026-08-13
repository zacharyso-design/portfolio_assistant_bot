[CmdletBinding()]
param(
    [string]$ConfigPath = '',
    [string]$Executable = ''
)

$ErrorActionPreference = 'Stop'
$taskName = 'CHIO Portfolio Assistant Morning Update'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) { $ConfigPath = Join-Path $repoRoot 'config.toml' }
if (-not $Executable) {
    $packagedExecutable = Join-Path $repoRoot 'PortfolioAssistant.exe'
    $Executable = if (Test-Path -LiteralPath $packagedExecutable) {
        $packagedExecutable
    } else {
        Join-Path $repoRoot '.venv\Scripts\python.exe'
    }
}
$config = (Resolve-Path -LiteralPath $ConfigPath).Path
$executablePath = (Resolve-Path -LiteralPath $Executable).Path
$isPython = [System.IO.Path]::GetFileName($executablePath) -match '^python(.exe)?$'
$statusJson = if ($isPython) {
    & $executablePath -m portfolio_assistant --config $config config-test
} else {
    & $executablePath --config $config config-test
}
$status = ($statusJson -join "`n") | ConvertFrom-Json
$runTime = [string]$status.scheduler.run_time
if ($LASTEXITCODE -ne 0 -or $runTime -notmatch '^([01]\d|2[0-3]):[0-5]\d$') { throw 'daily_run_time must use 24-hour HH:MM format.' }
$runtimeDir = Join-Path $env:LOCALAPPDATA 'PortfolioAssistant'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$wrapperPath = Join-Path $runtimeDir 'morning-update.cmd'
$dailyCommand = if ($isPython) {
    '"{0}" -m portfolio_assistant --config "{1}" daily' -f $executablePath, $config
} else {
    '"{0}" --config "{1}" daily' -f $executablePath, $config
}
Set-Content -LiteralPath $wrapperPath -Value @('@echo off', $dailyCommand) -Encoding ASCII
schtasks /Create /TN $taskName /TR $wrapperPath /SC DAILY /ST $runTime /F | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'Task Scheduler creation was blocked. The application remains usable with Run update now.' }
Write-Host "Morning task installed for $runTime local time."
