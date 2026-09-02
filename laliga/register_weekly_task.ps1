<#
.SYNOPSIS
    Registers ONE recurring Windows Scheduled Task that runs the weekly La Liga
    update (laliga/weekly_update.py): refresh fixtures/results, scrape whatever
    matchday just finished, rebuild the dashboard, and push to GitHub.

    This is separate from register_tasks.ps1, which fires one task per match at
    kick-off + 3h. This one just runs once a week as a safety net (catches
    matches missed by the per-match tasks, e.g. because the PC was off).

.PARAMETER DayOfWeek  Day to run on (default Tuesday - after a full round, which
                       normally runs Fri-Mon).
.PARAMETER Time       Local time to run, HH:mm (default 07:00).
.PARAMETER NoPush     Pass --no-push to weekly_update.py (rebuild only, no git push).
.PARAMETER WhatIf     Show what would be registered without creating the task.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File laliga\register_weekly_task.ps1
    powershell -ExecutionPolicy Bypass -File laliga\register_weekly_task.ps1 -DayOfWeek Monday -Time 09:00
#>

param(
    [ValidateSet("Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday")]
    [string] $DayOfWeek = "Tuesday",
    [string] $Time = "07:00",
    [switch] $NoPush,
    [switch] $WhatIf
)

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path      # ...\laliga
$RepoRoot   = Split-Path -Parent $ScriptDir                        # repo root
$PythonExe  = "C:\Users\puzik\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$TaskFolder = "\LaLiga"
$TaskName   = "LaLiga_WeeklyUpdate"

if (-not (Test-Path $PythonExe)) {
    Write-Warning "Python not found at $PythonExe - falling back to 'py'."
    $PythonExe = (Get-Command py).Source
}

$pyArgs = "laliga\weekly_update.py"
if ($NoPush) { $pyArgs += " --no-push" }

$startTime = [datetime]::ParseExact($Time, "HH:mm", $null)

Write-Host ""
Write-Host "La Liga weekly update task" -ForegroundColor Cyan
Write-Host ("=" * 70)
Write-Host "Python      : $PythonExe"
Write-Host "Working dir : $RepoRoot"
Write-Host "Task folder : Task Scheduler $TaskFolder"
Write-Host "Schedule    : every $DayOfWeek at $Time (local)"
Write-Host "Command     : $pyArgs"
Write-Host ("=" * 70)

if ($WhatIf) {
    Write-Host "[WHATIF] would register '$TaskName'" -ForegroundColor DarkGray
    exit 0
}

$action   = New-ScheduledTaskAction -Execute $PythonExe -Argument $pyArgs -WorkingDirectory $RepoRoot
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $startTime
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) -WakeToRun

Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskFolder -Action $action -Trigger $trigger `
    -Settings $settings -Description "Weekly La Liga fixtures/results refresh + scrape + push" -Force | Out-Null

Write-Host "[OK] Registered '$TaskName' - runs every $DayOfWeek at $Time." -ForegroundColor Green
Write-Host ""
Write-Host "View:    Get-ScheduledTask -TaskPath '\LaLiga\' -TaskName '$TaskName'"
Write-Host "Run now: Start-ScheduledTask -TaskPath '\LaLiga\' -TaskName '$TaskName'"
Write-Host ('Remove:  Unregister-ScheduledTask -TaskPath ''\LaLiga\'' -TaskName ''' + $TaskName + ''' -Confirm:$false')
Write-Host ""
