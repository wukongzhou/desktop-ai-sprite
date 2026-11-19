@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Desktop AI Sprite
echo.
echo 正在启动桌面精灵……
echo.

set "PYCMD="
where python >nul 2>nul
if not errorlevel 1 (
  python --version >nul 2>nul
  if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 --version >nul 2>nul
    if not errorlevel 1 set "PYCMD=py -3"
  )
)
if not defined PYCMD (
  echo [错误] 未检测到可用的 Python 3。
  echo [Error] Python 3 was not found. Install Python 3.9+:
  echo https://www.python.org/downloads/
  echo 安装时请勾选 Add Python to PATH，然后重新运行本启动器。
  pause
  exit /b 1
)

%PYCMD% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo [错误] 当前 Python 没有 Tkinter。请重新安装 python.org 的 Windows 版本并包含 Tcl/Tk。
  pause
  exit /b 1
)
%PYCMD% -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo 正在安装图片组件 Pillow……
  %PYCMD% -m pip install --user pillow
  if errorlevel 1 (
    echo [错误] Pillow 安装失败。请检查网络后重试。
    pause
    exit /b 1
  )
)
if not exist "%~dp0sprite.png" (
  echo [错误] 安装包里缺少 sprite.png，无法启动这只精灵。
  pause
  exit /b 1
)

echo 使用本安装包中的 sprite.png 启动精灵……
%PYCMD% "%~dp0desktop_pet.py" --sprite "%~dp0sprite.png"
if errorlevel 1 (
  echo.
  echo [错误] 桌面精灵报告了错误。请保留此窗口并截图。
  pause
)
endlocal
