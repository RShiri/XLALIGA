<#
.SYNOPSIS
    Registers one Windows Scheduled Task per upcoming Premier League match in a season.

    Each task fires once at kick-off + 3h (local time) and runs the one-shot:

        python -m epl.run_match --fotmob-id <ID> --season <SEASON>

    which deep-scrapes the finished match (FotMob + WhoScored + Understat), renders the
    PNG, refreshes the dashboard data, and pushes it to GitHub.

.DESCRIPTION
    Reads epl/schedules/SCHEDULE_<Season>.json (from build_schedule.py). Tasks live in
    the Task Scheduler folder "\EPL\". StartWhenAvailable means a run missed because the
    PC was off catches up on next wake.

.PARAMETER Season      Season to schedule, e.g. 2025-26 (default) or 2026-27.
.PARAMETER DaysAhead   Only register matches whose scrape time is within this many days (default 400).
.PARAMETER NoPost      Add --no-post (render + push, no WhatsApp).
.PARAMETER WhatIf      Show what would be registered without creating tasks.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File epl\register_tasks.ps1 -Season 2025-26
    powershell -ExecutionPolicy Bypass -File epl\register_tasks.ps1 -Season 2026-27 -DaysAhead 14
#>

param(
    [string] $Season = "2025-26",
    [int]    $DaysAhead = 400,
    [switch] $NoPost,
    [switch] $WhatIf
)

$ErrorActionPreference = "Stop"

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path      # ...\epl
$RepoRoot     = Split-Path -Parent $ScriptDir                        # repo root
$ScheduleJson = Join-Path $ScriptDir ("schedules\SCHEDULE_" + $Season + ".json")
$PythonExe    = "C:\Users\puzik\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$TaskFolder   = "\EPL"

if (-not (Test-Path $ScheduleJson)) {
    Write-Error "Schedule not found: $ScheduleJson. Run: py epl\build_schedule.py --season $Season"
    exit 1
}
if (-not (Test-Path $PythonExe)) {
    Write-Warning "Python not found at $PythonExe - falling back to 'py'."
    $PythonExe = (Get-Command py).Source
}

$data   = Get-Content $ScheduleJson -Raw -Encoding UTF8 | ConvertFrom-Json
$games  = $data.matches
$now    = Get-Date
$cutoff = $now.AddDays($DaysAhead)

Write-Host ""
Write-Host "Premier League Task Registration - season $Season" -ForegroundColor Cyan
Write-Host ("=" * 70)
Write-Host "Python      : $PythonExe"
Write-Host "Working dir : $RepoRoot"
Write-Host "Task folder : Task Scheduler $TaskFolder"
Write-Host "Window      : now to $($cutoff.ToString('yyyy-MM-dd HH:mm'))  [$DaysAhead days]"
Write-Host ("=" * 70)

$registered = 0; $skippedPast = 0; $skippedFar = 0; $skippedNoTime = 0

foreach ($g in $games) {
    if (-not $g.kickoff_utc) { $skippedNoTime++; continue }
    try {
        # kickoff_utc is ISO8601 UTC; scrape at kickoff + 3h in this PC's local time.
        $scrapeAt = ([datetimeoffset]::Parse($g.kickoff_utc)).LocalDateTime.AddHours(3)
    } catch { $skippedNoTime++; continue }

    if ($scrapeAt -le $now)    { $skippedPast++; continue }
    if ($scrapeAt -gt $cutoff) { $skippedFar++;  continue }

    $hName = ($g.home -replace '[^A-Za-z0-9]', '')
    $aName = ($g.away -replace '[^A-Za-z0-9]', '')
    $fid   = $g.fotmob_id
    $taskName = "EPL_" + $Season.Replace('-','') + "_" + $fid + "_" + $hName + "_vs_" + $aName

    $pyArgs = "-m epl.run_match --fotmob-id " + $fid + " --season " + $Season
    if ($NoPost) { $pyArgs = $pyArgs + " --no-post" }

    $label = "MD$($g.matchday)  $fid  $($g.home) vs $($g.away)  scrape $($scrapeAt.ToString('yyyy-MM-dd HH:mm'))"

    if ($WhatIf) { Write-Host ("[WHATIF] " + $label) -ForegroundColor DarkGray; $registered++; continue }

    $action   = New-ScheduledTaskAction -Execute $PythonExe -Argument $pyArgs -WorkingDirectory $RepoRoot
    $trigger  = New-ScheduledTaskTrigger -Once -At $scrapeAt
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 25) -WakeToRun

    Register-ScheduledTask -TaskName $taskName -TaskPath $TaskFolder -Action $action -Trigger $trigger -Settings $settings -Description ("Premier League auto scrape+render+push " + $g.home + " vs " + $g.away) -Force | Out-Null
    Write-Host ("[OK]     " + $label) -ForegroundColor Green
    $registered++
}

Write-Host ("=" * 70)
Write-Host "Registered : $registered" -ForegroundColor Cyan
Write-Host "Skipped (past) $skippedPast  (beyond window) $skippedFar  (no kickoff time) $skippedNoTime"
Write-Host ""
Write-Host "View tasks:   Get-ScheduledTask -TaskPath '\EPL\*'"
Write-Host "Remove all:   powershell -ExecutionPolicy Bypass -File epl\unregister_tasks.ps1"
Write-Host ""
