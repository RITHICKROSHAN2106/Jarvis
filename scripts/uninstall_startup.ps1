# JARVIS Windows Startup Uninstaller
[CmdletBinding()]
param()

$TaskName = "JARVIS_Voice_Assistant"
$StartupFolder = [System.Environment]::GetFolderPath('Startup')
$ShortcutPath = Join-Path $StartupFolder "JARVIS.lnk"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   JARVIS Windows Startup Uninstaller" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Remove Startup shortcut
if (Test-Path $ShortcutPath) {
    Remove-Item -Path $ShortcutPath -Force
    Write-Host "[SUCCESS] Removed Startup shortcut: $ShortcutPath" -ForegroundColor Green
} else {
    Write-Host "[INFO] No Startup shortcut found at: $ShortcutPath" -ForegroundColor Gray
}

# 2. Remove Task Scheduler task if present
try {
    schtasks.exe /query /tn "$TaskName" >$null 2>&1
    if ($LASTEXITCODE -eq 0) {
        schtasks.exe /delete /tn "$TaskName" /f >$null 2>&1
        Write-Host "[SUCCESS] Removed Task Scheduler task '$TaskName'." -ForegroundColor Green
    }
} catch {}

Write-Host "`nJARVIS startup configuration removed." -ForegroundColor Green
