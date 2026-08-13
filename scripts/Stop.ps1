[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$pidPath = Join-Path (Join-Path $env:LOCALAPPDATA 'PortfolioAssistant') 'server.pid'
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host 'Portfolio Assistant is not recorded as running.'
    exit 0
}
try {
    $record = Get-Content -Raw -LiteralPath $pidPath | ConvertFrom-Json
    $serverPid = [int]$record.pid
    $process = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
    $sameExecutable = $process -and $process.Path -eq ([System.IO.Path]::GetFullPath([string]$record.executable))
    $recordStart = [datetimeoffset]::Parse([string]$record.start_time_utc).UtcDateTime
    $sameStart = $process -and ([Math]::Abs(($process.StartTime.ToUniversalTime() - $recordStart).TotalSeconds) -lt 2)
    if ($process -and $sameExecutable -and $sameStart) {
        Stop-Process -Id $serverPid
    }
    elseif ($process) {
        Write-Warning "PID $serverPid belongs to a different process; it was not stopped."
    }
}
catch {
    Write-Warning 'The PID record was unreadable; no process was stopped.'
}
Remove-Item -LiteralPath $pidPath -Force
Write-Host 'Portfolio Assistant stopped.'
