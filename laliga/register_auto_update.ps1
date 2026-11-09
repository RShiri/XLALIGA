<#
.SYNOPSIS
    Registers the single recurring task that updates the dashboard when a fixture finishes:
    every 15 min it runs laliga\auto_update.py (see that file for why this replaced the
    per-fixture tasks), and disables any leftover LaLigaScrape_* tasks.

    The PC has to be on and awake, and you logged in. A match that finishes while it is off
    is picked up on the first tick after it comes back.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File laliga\register_auto_update.ps1
#>

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python   = "C:\Users\puzik\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
if (-not (Test-Path $Python)) { throw "pythonw not found at $Python" }

$action  = New-ScheduledTaskAction -Execute $Python -Argument "laliga\auto_update.py" -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
# 90 min: three scrapes + the rebuild has taken ~30 min, and a run killed mid-write is worse
# than a slow one. IgnoreNew: ticks during a run are simply dropped.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 90)

Register-ScheduledTask -TaskName "LaLiga_AutoUpdate" -TaskPath "\LaLiga" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Scrape, rebuild and push La Liga matches once they finish" -Force | Out-Null

$old = @(Get-ScheduledTask -TaskPath "\LaLiga\*" | Where-Object { $_.TaskName -like "LaLigaScrape_*" -and $_.State -ne "Disabled" })
$old | Disable-ScheduledTask | Out-Null

Write-Host "LaLiga_AutoUpdate registered (every 15 min). Disabled $($old.Count) per-fixture task(s)."
