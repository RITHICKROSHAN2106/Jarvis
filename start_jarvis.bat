@echo off
title JARVIS Voice Assistant
set "JARVIS_DIR=%~dp0"
cd /d "%JARVIS_DIR%"
echo ==============================================
echo        JARVIS Voice Assistant Console
echo ==============================================
if exist "%JARVIS_DIR%venv\Scripts\python.exe" (
	set "JARVIS_PYTHON=%JARVIS_DIR%venv\Scripts\python.exe"
) else if exist "%JARVIS_DIR%..\venv\Scripts\python.exe" (
	set "JARVIS_PYTHON=%JARVIS_DIR%..\venv\Scripts\python.exe"
) else (
	where py >nul 2>&1
	if not errorlevel 1 (
		set "JARVIS_PYTHON=py -3"
	) else (
		set "JARVIS_PYTHON=python"
	)
)

echo Using Python: %JARVIS_PYTHON%
%JARVIS_PYTHON% "%JARVIS_DIR%supervisor.py" %*
set "JARVIS_EXIT=%ERRORLEVEL%"
if not "%JARVIS_EXIT%"=="0" echo JARVIS exited with code %JARVIS_EXIT%. See logs\supervisor.log and logs\jarvis_stderr.log.
if not "%JARVIS_NO_PAUSE%"=="1" pause
