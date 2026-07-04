<#
.SYNOPSIS
    Removes all EPL scheduled tasks created by register_tasks.ps1.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File epl\unregister_tasks.ps1
#>

$ErrorActionPreference = "SilentlyContinue"

$tasks = Get-ScheduledTask -TaskPath "\EPL\*"
if (-not $tasks) {
    Write-Host "No EPL tasks found." -ForegroundColor Yellow
    return
}

$count = 0
foreach ($t in $tasks) {
    Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Confirm:$false
    Write-Host "[REMOVED] $($t.TaskName)" -ForegroundColor Green
    $count++
}
Write-Host ("-" * 50)
Write-Host "Removed $count EPL task(s)." -ForegroundColor Cyan
