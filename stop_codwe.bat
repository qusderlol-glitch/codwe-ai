@echo off
cd /d "%~dp0"
"venv\Scripts\python.exe" -c "import urllib.request as u; r=u.Request('http://127.0.0.1:8765/api/shutdown', method='POST', headers={'X-Codwe':'1'}); u.urlopen(r, timeout=5); print('Codwe AI stopped.')" 2>nul || echo Codwe AI is not running.
timeout /t 2 >nul
