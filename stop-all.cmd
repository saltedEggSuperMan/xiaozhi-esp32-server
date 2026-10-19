@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-all.ps1" -Stop %*
if errorlevel 1 (
    echo Stop failed. Review the messages above. Try running as administrator if access is denied.
    pause
    exit /b 1
)
