# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把 tkinter 桌宠打成 Windows 可执行程序。

用法（Windows）：
    pyinstaller --clean --noconfirm desktop-pet.spec
    # 或直接双击 / 运行同目录的 build-pet-exe.bat
产物：dist\\DesktopPet\\DesktopPet.exe（onedir，见下）

为什么是 onedir（文件夹）而不是 onefile（单个 exe），三个理由：
1. 启动快：桌宠要开机自启、登录就出现。onefile 每次启动都要先把整个包
   （Pillow + tkinter，约 20MB）解压到临时目录再运行，登录后要等好几秒；
   onedir 直接运行，没有解压这一步。
2. 数据文件能持久保存：程序默认把性格档案 pet_profile.json、记忆
   pet_memory.json 和精灵图 saved_sprites/latest_sprite.png 放在脚本旁边
   （Path(__file__).parent）。onefile 里 __file__ 指向临时解压目录，每次运行
   都是新目录，性格档案会被反复重掷、记忆全丢；onedir 里 __file__ 就是安装
   目录，这些文件一直留在那里。
3. 反正要包安装包：最终用户拿到的是安装器（NSIS / 7-Zip SFX），安装器本来
   就是复制一个文件夹，单文件 exe 的“方便携带”在这里没有意义。

入口与依赖：
- 入口 desktop_pet.py；同目录的 pet_brain.py、pet_tools.py、pet_memory.py
  由它 import，PyInstaller 会自动收进来，不用逐个列。
- 第三方依赖只有两个：Pillow（必需，PIL.Image / ImageTk）和 tkinterdnd2
  （可选，拖文件到精灵身上才用；没装也能跑，程序会自动降级成普通窗口）。
  打包机上两个都装上（见 build-pet-exe.bat / GitHub Actions），打出来的 exe
  就是满血版。
- winsound 是 Windows 自带标准库，在函数里才 import，写进 hiddenimports
  防止分析器漏掉（Linux 下打包会报找不到，那是正常的警告，不影响）。
- console=False：桌宠是图形程序，不弹黑窗口。

注意：这个 spec 本身不区分平台，在 Linux 上跑会打出 Linux 版（只用于验
证 spec 和 import 没错）。真正的 Windows .exe 必须在 Windows 上打
（GitHub Actions 的 windows-latest，或用户自己的 Windows 电脑）。
"""

import os

# spec 文件所在目录就是仓库根目录（desktop_pet.py 等四个文件都在这里）。
SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    [os.path.join(SPEC_DIR, "desktop_pet.py")],
    pathex=[SPEC_DIR],
    binaries=[],
    datas=[],
    hiddenimports=[
        "winsound",  # Windows 标准库，函数内 import；Linux 打包时报找不到是正常的
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DesktopPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # 打包机上不一定有 UPX；不要为压缩多一个外部依赖
    console=False,  # 图形程序，不弹控制台黑窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DesktopPet",
)
