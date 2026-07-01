<#
.SYNOPSIS
    Removes all LaLiga scheduled tasks created by register_tasks.ps1.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File laliga\unregister_tasks.ps1
#>

$ErrorActionPreference = "SilentlyContinue"

$tasks = Get-ScheduledTask -TaskPath "\LaLiga\*"
if (-not $tasks) {
    Write-Host "No LaLiga tasks found." -ForegroundColor Yellow
    return
}

$count = 0
foreach ($t in $tasks) {
    Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Confirm:$false
    Write-Host "[REMOVED] $($t.TaskName)" -ForegroundColor Green
    $count++
}
Write-Host ("-" * 50)
Write-Host "Removed $count LaLiga task(s)." -ForegroundColor Cyan
