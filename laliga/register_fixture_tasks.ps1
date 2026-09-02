<#
.SYNOPSIS
    Registers one Windows Scheduled Task PER upcoming La Liga match in a season,
    each firing at kick-off + 3h and running:

        py laliga\weekly_update.py --season <SEASON>

    weekly_update.py re-scans the whole season (refresh fixtures, scrape whatever's
    newly finished via backfill.py, rebuild the dashboard, plain `git push`), so
    each per-match firing safely catches exactly the match that just ended (and
    mops up anything an earlier firing missed).

.DESCRIPTION
    This is the per-fixture sibling of register_weekly_task.ps1 (one fixed weekly
    run) and deliberately does NOT reuse register_tasks.ps1 (which drives
    run_match.py --fotmob-id): run_match.py's auto-push goes through git_ops.py,
    which needs GIT_TOKEN in .env — not present in this clone. weekly_update.py
    pushes with plain `git push` against the local clone's own (already
    authenticated) remote instead, so it works without that token.

    Reads laliga/schedules/SCHEDULE_<Season>.json. Tasks live in Task Scheduler
    folder "\LaLiga\", named LaLigaScrape_<season>_<fotmob_id>_<home>_vs_<away>.
    StartWhenAvailable means a run missed because the PC was off catches up on
    next wake.

.PARAMETER Season      Season to schedule, e.g. 2026-27 (default).
.PARAMETER DaysAhead    Only register matches whose scrape time is within this many days (default 400).
.PARAMETER WhatIf       Show what would be registered without creating tasks.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File laliga\register_fixture_tasks.ps1 -Season 2026-27
#>

param(
    [string] $Season = "2026-27",
    [int]    $DaysAhead = 400,
    [switch] $WhatIf
)

$ErrorActionPreference = "Stop"

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path      # ...\laliga
$RepoRoot     = Split-Path -Parent $ScriptDir                        # repo root
$ScheduleJson = Join-Path $ScriptDir ("schedules\SCHEDULE_" + $Season + ".json")
$PythonExe    = "C:\Users\puzik\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$TaskFolder   = "\LaLiga"

if (-not (Test-Path $ScheduleJson)) {
    Write-Error ("Schedule not found: " + $ScheduleJson + ". Run: py laliga\build_schedule.py --season " + $Season)
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
Write-Host "La Liga per-fixture scrape task registration - season $Season" -ForegroundColor Cyan
Write-Host ("=" * 70)
Write-Host "Python      : $PythonExe"
Write-Host "Working dir : $RepoRoot"
Write-Host "Task folder : Task Scheduler $TaskFolder"
Write-Host "Window      : now to $($cutoff.ToString('yyyy-MM-dd HH:mm'))  [$DaysAhead days]"
Write-Host "Runs        : py laliga\weekly_update.py --season $Season"
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
    $taskName = "LaLigaScrape_" + $Season.Replace('-','') + "_" + $fid + "_" + $hName + "_vs_" + $aName

    $pyArgs = "laliga\weekly_update.py --season " + $Season

    $label = "MD$($g.matchday)  $fid  $($g.home) vs $($g.away)  scrape $($scrapeAt.ToString('yyyy-MM-dd HH:mm'))"

    if ($WhatIf) { Write-Host ("[WHATIF] " + $label) -ForegroundColor DarkGray; $registered++; continue }

    $action   = New-ScheduledTaskAction -Execute $PythonExe -Argument $pyArgs -WorkingDirectory $RepoRoot
    $trigger  = New-ScheduledTaskTrigger -Once -At $scrapeAt
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 25) -WakeToRun

    Register-ScheduledTask -TaskName $taskName -TaskPath $TaskFolder -Action $action -Trigger $trigger -Settings $settings -Description ("La Liga auto scrape+rebuild+push after " + $g.home + " vs " + $g.away) -Force | Out-Null
    Write-Host ("[OK]     " + $label) -ForegroundColor Green
    $registered++
}

Write-Host ("=" * 70)
Write-Host "Registered : $registered" -ForegroundColor Cyan
Write-Host "Skipped (past) $skippedPast  (beyond window) $skippedFar  (no kickoff time) $skippedNoTime"
Write-Host ""
Write-Host "View tasks:  Get-ScheduledTask -TaskPath '\LaLiga\*' | Where-Object TaskName -like 'LaLigaScrape_*'"
Write-Host 'Remove all:  Get-ScheduledTask -TaskPath ''\LaLiga\*'' | Where-Object TaskName -like ''LaLigaScrape_*'' | Unregister-ScheduledTask -Confirm:$false'
Write-Host ""
