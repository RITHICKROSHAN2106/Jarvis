# Installs a user-level `jarvis` command without requiring administrator access.
[CmdletBinding()]
param()

$JarvisDir = Split-Path -Parent $PSScriptRoot
$CommandDir = Join-Path $env:USERPROFILE "bin"
$CommandPath = Join-Path $CommandDir "jarvis.cmd"

New-Item -ItemType Directory -Path $CommandDir -Force | Out-Null
$Launcher = @"
@echo off
set "JARVIS_DIR=$JarvisDir\"
set "JARVIS_NO_PAUSE=1"
call "%JARVIS_DIR%start_jarvis.bat" %*
"@
Set-Content -Path $CommandPath -Value $Launcher -Encoding ASCII

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$PathEntries = @($UserPath -split ';' | Where-Object { $_ })
if ($PathEntries -notcontains $CommandDir) {
    [Environment]::SetEnvironmentVariable("Path", (($PathEntries + $CommandDir) -join ';'), "User")
}

Write-Host "Installed: $CommandPath" -ForegroundColor Green
Write-Host "Open a new PowerShell or Command Prompt, then run: jarvis" -ForegroundColor Cyan