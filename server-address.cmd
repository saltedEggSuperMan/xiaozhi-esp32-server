@echo off
"%~dp0main\xiaozhi-server\.venv\Scripts\python.exe" -X utf8 "%~dp0scripts\server_address.py" %*
exit /b %errorlevel%
