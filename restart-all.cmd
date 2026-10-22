@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-all.ps1" -Restart -SelectAudioOutput -SelectServerAddress %*
if "%errorlevel%"=="130" exit /b 130
if errorlevel 1 (
    echo Restart failed. Review the messages above and .runtime\logs.
    pause
    exit /b 1
)
