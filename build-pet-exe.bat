@echo off
REM ============================================================
REM  在 Windows 上一键打出桌宠 .exe（onedir，见 desktop-pet.spec）
REM  用法：把本文件和 desktop_pet.py、pet_brain.py、pet_tools.py、
REM  pet_memory.py、desktop-pet.spec 放在同一个文件夹，双击运行。
REM  要求：python.org 的 Python 3.12（安装时勾上 tkinter，默认就有；
REM  微软商店版 Python 可能没有 tkinter，打出来的 exe 会起不来）。
REM  产物：dist\DesktopPet\DesktopPet.exe
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo [1/4] 检查 Python ...
python --version >nul 2>&1
if errorlevel 1 (
    echo 找不到 python。请先装 python.org 的 Python 3.12，再双击本文件。
    pause
    exit /b 1
)
python --version

echo [2/4] 检查 tkinter（Windows 版 Python 自带）...
python -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo 你的 Python 没有 tkinter（常见于微软商店版）。
    echo 请去 python.org 下安装版 Python 3.12，安装时保持默认勾选。
    pause
    exit /b 1
)
echo tkinter OK

echo [3/4] 安装打包依赖（PyInstaller、Pillow、tkinterdnd2）...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo pip 升级失败，请检查网络后重试。
    pause
    exit /b 1
)
python -m pip install pyinstaller Pillow tkinterdnd2
if errorlevel 1 (
    echo 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)

echo [4/4] 打包（onedir）...
pyinstaller --clean --noconfirm desktop-pet.spec
if errorlevel 1 (
    echo 打包失败，请把上面的报错发回来。
    pause
    exit /b 1
)

echo.
echo 打包完成：dist\DesktopPet\DesktopPet.exe
echo 把 dist\DesktopPet 整个文件夹拷走就能用；精灵图放在
echo dist\DesktopPet\saved_sprites\latest_sprite.png，或启动时加
echo   DesktopPet.exe --sprite 你的精灵图.png
echo.
pause
