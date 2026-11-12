@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在叫醒你的桌面精灵……
set PYCMD=python
where python >nul 2>&1 || set PYCMD=py -3
%PYCMD% --version >nul 2>&1
if errorlevel 1 (
  echo 没找到 Python，正在打开官网下载页……
  echo Python not found. Opening the download page...
  start https://www.python.org/downloads/
  pause
  exit /b
)
%PYCMD% -c "import PIL" 2>nul || %PYCMD% -m pip install pillow
%PYCMD% -m pip install --timeout 15 tkinterdnd2 >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0创建快捷方式.ps1" -Action install -Dir "%~dp0"
where pythonw >nul 2>&1
if errorlevel 1 (
  start "" %PYCMD% "%~dp0desktop_pet.py"
) else (
  start "" pythonw "%~dp0desktop_pet.py"
)
exit
