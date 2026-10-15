@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-all.ps1" %*
if errorlevel 1 (
    echo Startup failed. Review the messages above and .runtime\logs.
    pause
    exit /b 1
)
