# JARVIS Windows Startup Installer
# Configures JARVIS to start automatically upon user logon.
[CmdletBinding()]
param()

$TaskName = "JARVIS_Voice_Assistant"
$JarvisDir = (Split-Path -Parent $PSScriptRoot)
$VbsScript = Join-Path $JarvisDir "start_jarvis.vbs"
$WscriptExe = "$env:SystemRoot\System32\wscript.exe"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   JARVIS Windows Startup Installer" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Launcher VBS: $VbsScript"
Write-Host "Target User:  $env:USERNAME"

# 1. Create Windows Startup Folder shortcut (runs reliably on logon without requiring admin elevation)
$StartupFolder = [System.Environment]::GetFolderPath('Startup')
$ShortcutPath = Join-Path $StartupFolder "JARVIS.lnk"

try {
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $WscriptExe
    $Shortcut.Arguments = "`"$VbsScript`""
    $Shortcut.WorkingDirectory = $JarvisDir
    $Shortcut.Description = "JARVIS Voice Assistant Background Startup"
    $Shortcut.Save()
    Write-Host "[SUCCESS] Created Windows Startup shortcut:" -ForegroundColor Green
    Write-Host "          $ShortcutPath" -ForegroundColor Gray
} catch {
    Write-Host "[WARNING] Could not create Startup folder shortcut: $_" -ForegroundColor Yellow
}

# 2. Remove any older duplicate Task Scheduler registration.
try {
    & schtasks.exe /delete /tn $TaskName /f >$null 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[SUCCESS] Removed duplicate Task Scheduler task '$TaskName'." -ForegroundColor Green
    } else {
        Write-Host "[INFO] No duplicate Task Scheduler task found." -ForegroundColor Gray
    }
} catch {
    Write-Host "[INFO] Could not inspect Task Scheduler; Startup shortcut is active." -ForegroundColor Gray
}

Write-Host "`nJARVIS is configured to start automatically from the Startup folder." -ForegroundColor Green
