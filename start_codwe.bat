@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\pythonw.exe" goto novenv
start "" "venv\Scripts\pythonw.exe" "%~dp0launcher.py"
exit /b 0
:novenv
echo The "venv" folder was not found next to this file.
echo Keep the whole Codwe AI folder together.
pause
