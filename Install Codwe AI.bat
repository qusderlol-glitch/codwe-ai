@echo off
setlocal
cd /d "%~dp0"
title Codwe AI - Installer
echo.
echo  ===============================================
echo    Codwe AI - Installer
echo  ===============================================
echo.
echo  Please wait. This window prepares everything and then
echo  opens the installer window. DO NOT close this black window
echo  until the installation is finished.
echo.

rem ---- 1. find Python 3.10-3.13 ----
set "PYCMD="
for %%V in (3.12 3.13 3.11 3.10) do (
  if not defined PYCMD (
    py -%%V -c "import sys" >nul 2>&1 && set "PYCMD=py -%%V"
  )
)
if not defined PYCMD (
  python -c "import sys; assert (3,10)<=sys.version_info[:2]<=(3,13)" >nul 2>&1 && set "PYCMD=python"
)
if exist "venv\Scripts\python.exe" set "PYCMD=venv\Scripts\python.exe"

if not defined PYCMD (
  echo  Python was not found on this computer.
  echo  Python is a free program that Codwe AI needs.
  echo.
  where winget >nul 2>&1
  if errorlevel 1 (
    echo  Please install Python 3.12 from https://www.python.org/downloads/
    echo  IMPORTANT: tick the box "Add python.exe to PATH" in the installer.
    echo  Then run this file again.
    start "" "https://www.python.org/downloads/"
    pause
    exit /b 1
  )
  choice /m "  Install Python 3.12 automatically now"
  if errorlevel 2 (
    echo  OK. Install Python 3.12 yourself and run this file again.
    pause
    exit /b 1
  )
  winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
  py -3.12 -c "import sys" >nul 2>&1 && set "PYCMD=py -3.12"
  if not defined PYCMD (
    echo.
    echo  Python was installed. Please CLOSE this window and run this file once more.
    pause
    exit /b 1
  )
)

rem ---- 2. private Python folder "venv" ----
if not exist "venv\Scripts\python.exe" (
  echo  Creating the working folder "venv" ...
  %PYCMD% -m venv venv
  if errorlevel 1 (
    echo  Could not create the "venv" folder. Is there free disk space?
    pause
    exit /b 1
  )
)

rem ---- 2b. report: what is installed, and links for what is missing ----
echo.
echo  ===============================================
echo    WHAT IS ON YOUR COMPUTER
echo  ===============================================
echo    [OK] Python found
set "HAVE_OLLAMA="
where ollama >nul 2>&1 && set "HAVE_OLLAMA=1"
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "HAVE_OLLAMA=1"
if defined HAVE_OLLAMA (echo    [OK] Ollama - runs the smart chat) else (echo    [..] Ollama is NOT installed. The installer will download it for you.& echo         Manual link: https://ollama.com/download/windows)
nvidia-smi >nul 2>&1
if errorlevel 1 (echo    [!!] NVIDIA video card or driver NOT found. Pictures and video need it.& echo         Driver link: https://www.nvidia.com/Download/index.aspx) else (echo    [OK] NVIDIA video card and driver)
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (echo    [OK] Microsoft Edge) else (echo    [!!] Microsoft Edge NOT found. Link: https://www.microsoft.com/edge)
echo.
echo    The installer window will show the same report with buttons.
echo.
timeout /t 4 >nul

rem ---- 3. tools for the installer window ----
echo  Preparing the installer (1-2 minutes) ...
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q fastapi==0.161.1 uvicorn==0.54.0 requests==2.34.2 psutil==7.2.2 huggingface_hub==1.33.0 pydantic==2.14.1
if errorlevel 1 (
  echo.
  echo  Could not download the installer tools. Check your internet and run this file again.
  pause
  exit /b 1
)

rem ---- 4. open the installer window ----
echo  Opening the installer window ...
"venv\Scripts\python.exe" "installer\installer.py"
echo.
echo  The installer was closed. You can close this window.
pause
