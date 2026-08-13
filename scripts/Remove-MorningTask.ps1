[CmdletBinding()]
param()

$taskName = 'CHIO Portfolio Assistant Morning Update'
schtasks /Delete /TN $taskName /F | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'The morning task was not installed or could not be removed.' }
$wrapperPath = Join-Path (Join-Path $env:LOCALAPPDATA 'PortfolioAssistant') 'morning-update.cmd'
if (Test-Path -LiteralPath $wrapperPath) { Remove-Item -LiteralPath $wrapperPath -Force }
