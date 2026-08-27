# Registers the 13F pipeline as a weekly Windows Task Scheduler job.
# Run once, from anywhere: paths are derived from this script's location.
# Remove with:  Unregister-ScheduledTask -TaskName "13F-Portfolio-Pipeline"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Wrapper = Join-Path $RepoRoot "scheduler\run_scheduled.py"

$Action = New-ScheduledTaskAction `
    -Execute $VenvPython `
    -Argument "`"$Wrapper`"" `
    -WorkingDirectory $RepoRoot

$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 07:00

# StartWhenAvailable runs a missed job at next boot, so a powered-off
# PC on Monday morning does not mean a skipped week.
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName "13F-Portfolio-Pipeline" `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Fetches and processes new SEC 13F filings weekly."

Write-Host "Registered task '13F-Portfolio-Pipeline' (Mondays 07:00)."
Write-Host "Repo root: $RepoRoot"
