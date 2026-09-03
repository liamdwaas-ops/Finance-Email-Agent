param(
  [string]$TaskName = 'Daily Podcast Episode Digest'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  throw 'Python 3.12 was not found. Install it, then run this script again.'
}
$script = Join-Path $root 'podcast_digest.py'
$command = '"{0}" "{1}"' -f $python, $script
schtasks.exe /Create /TN $TaskName /TR $command /SC DAILY /ST 07:00 /RU $env:USERNAME /NP /RL LIMITED /F | Out-Host
if ($LASTEXITCODE -ne 0) {
  throw 'Windows did not permit passwordless background registration. Use Task Scheduler to enter the Windows account password locally.'
}
Write-Host "Installed task: $TaskName"
