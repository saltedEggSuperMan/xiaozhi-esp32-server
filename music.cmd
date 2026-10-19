@echo off
"%~dp0main\xiaozhi-server\.venv\Scripts\python.exe" -X utf8 "%~dp0scripts\music.py" %*
exit /b %errorlevel%
