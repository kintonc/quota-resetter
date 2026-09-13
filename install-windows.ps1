param(
  [Parameter(Mandatory=$true)][string]$ScriptPath,
  [string]$Python = "pythonw.exe"
)
$resolvedPython = (Get-Command $Python -ErrorAction SilentlyContinue).Source
if (-not $resolvedPython) {
  $resolvedPython = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}
if (-not $resolvedPython) {
  $resolvedPython = $Python
}
$workDir = Join-Path $HOME ".quota-kicker"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
$action = New-ScheduledTaskAction -Execute $resolvedPython -Argument ('"{0}"' -f $ScriptPath) -WorkingDirectory $workDir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Quota Kicker" -Action $action -Trigger $trigger -Settings $settings -Description "Starts Codex/Claude/Antigravity after observed 5-hour resets" -Force | Out-Null
Write-Host "Installed. Check it with: Get-ScheduledTask -TaskName 'Quota Kicker'"
