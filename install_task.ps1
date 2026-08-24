param(
  [string]$TaskName = 'Daily Portfolio News Brief'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  throw 'Python 3.12 was not found. Install it, then run this script again.'
}
$script = Join-Path $root 'portfolio_digest.py'

$command = '"{0}" "{1}"' -f $python, $script
# /NP creates a passwordless S4U task. It runs in the background when the user
# is signed out and does not save the Windows account password in the task.
schtasks.exe /Create /TN $TaskName /TR $command /SC DAILY /ST 06:00 /RU $env:USERNAME /NP /RL LIMITED /F | Out-Host
if ($LASTEXITCODE -ne 0) {
  throw 'Windows did not permit passwordless background registration for this account. Use Task Scheduler to select “Run whether user is logged on or not” and enter the Windows account password locally.'
}
Write-Host "Installed task: $TaskName"
