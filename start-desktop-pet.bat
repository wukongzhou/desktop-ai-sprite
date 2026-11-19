@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting Desktop AI Sprite...
set "PYCMD="
where python >nul 2>&1 && set "PYCMD=python"
if "%PYCMD%"=="" (
  py -3 --version >nul 2>&1 && set "PYCMD=py -3"
)
if "%PYCMD%"=="" (
  echo Python 3.9 or newer was not found. Install it from https://www.python.org/downloads/
  pause
  exit /b 1
)
%PYCMD% -c "import PIL" >nul 2>&1
if errorlevel 1 (
  echo Installing Pillow for image display...
  %PYCMD% -m pip install pillow
  if errorlevel 1 (echo Pillow install failed. Check your connection and retry.& pause& exit /b 1)
)
%PYCMD% -c "import tkinterdnd2" >nul 2>&1 || %PYCMD% -m pip install --timeout 20 tkinterdnd2 >nul 2>&1
%PYCMD% desktop_pet.py
if errorlevel 1 pause
