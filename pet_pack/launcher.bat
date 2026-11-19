@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Desktop AI Sprite
echo.
echo 正在启动桌面精灵……
echo.

set "PY_EXE="
set "PY_ARGS="
where python >nul 2>nul
if not errorlevel 1 (
  python --version >nul 2>nul
  if not errorlevel 1 set "PY_EXE=python"
)
if not defined PY_EXE (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 --version >nul 2>nul
    if not errorlevel 1 (
      set "PY_EXE=py"
      set "PY_ARGS=-3"
    )
  )
)
if not defined PY_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PY_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PY_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python39\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
if not defined PY_EXE if exist "%ProgramFiles%\Python314\python.exe" set "PY_EXE=%ProgramFiles%\Python314\python.exe"
if not defined PY_EXE if exist "%ProgramFiles%\Python313\python.exe" set "PY_EXE=%ProgramFiles%\Python313\python.exe"
if not defined PY_EXE if exist "%ProgramFiles%\Python312\python.exe" set "PY_EXE=%ProgramFiles%\Python312\python.exe"
if not defined PY_EXE (
  echo [错误] 找到不了 Python 3。安装 Python 3.9 或更新版本后重试。
  echo [Error] Could not find Python 3. Install Python 3.9 or newer.
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)

"%PY_EXE%" %PY_ARGS% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo [错误] 此 Python 缺少 Tkinter/Tcl 图形组件。
  echo [Error] This Python installation cannot load its Tcl/Tk GUI libraries.
  pause
  exit /b 1
)
"%PY_EXE%" %PY_ARGS% -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo 正在安装图片组件 Pillow……
  "%PY_EXE%" %PY_ARGS% -m pip install --user pillow
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
"%PY_EXE%" %PY_ARGS% "%~dp0desktop_pet.py" --sprite "%~dp0sprite.png"
if errorlevel 1 (
  echo.
  echo [错误] 桌面精灵报告了错误。请保留此窗口并截图。
  pause
)
endlocal
