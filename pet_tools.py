"""pet_tools.py — 桌宠的工具箱（阶段二）：吃文件、修文件、看回收站。

纯逻辑 + 少量 Windows API（ctypes），不依赖 tkinter，网站端也能 import。
- 吃 = 把文件移进回收站（可恢复），不是彻底删除；大文件吃之前要主人点头。
- 修 = 图片重新编码 / zip 抢救 / 文本转码；修不好就认栽，不硬撑。
- 回收站监听：读每个盘 $Recycle.Bin 里的 $I 文件（原路径 + 大小 + 删除时间）。
"""
from __future__ import annotations

import os
import struct
import sys
import zipfile
from pathlib import Path

ASK_MB = 50          # 超过这个大小，吃之前先问主人
OVERSTUFF_MB = 500   # 吃掉这么大，吃撑躺平
FULLNESS_REFUSE = 90  # 饱腹超过这个值，拒绝再吃
RECYCLE_POLL_SECONDS = 5

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".log", ".xml", ".html", ".py", ".js", ".css"}


def human_size(n: int) -> str:
    """512B / 800KB / 5.2MB / 1.1GB，给气泡和菜单看的。"""
    if n < 1024:
        return f"{n}B"
    if n < 1024 ** 2:
        return f"{n / 1024:.0f}KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f}MB"
    return f"{n / 1024 ** 3:.1f}GB"


def classify_file(path: str | Path) -> str:
    """按后缀分：image / zip / text / other。"""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext == ".zip":
        return "zip"
    if ext in TEXT_EXTS:
        return "text"
    return "other"


def fullness_gain(size: int) -> int:
    """吃一口涨多少饱腹：小零食 +5，大餐最多 +40。"""
    return 5 + min(35, size // (10 * 1024 * 1024))


# ---------------------------------------------------------------- 修文件
def repair_image(path: str | Path, out_dir: str | Path | None = None) -> tuple[bool, Path | None, str]:
    """图片重新编码：能打开就存一份干净的 PNG。"""
    path = Path(path)
    try:
        from PIL import Image
    except Exception:
        return False, None, "没装 Pillow，修不了图。"
    out_dir = Path(out_dir) if out_dir else path.parent
    try:
        img = Image.open(path)
        img.load()
    except Exception:
        return False, None, "打不开这张图。"
    out = out_dir / f"{path.stem}_fixed.png"
    try:
        img.convert("RGBA").save(out, format="PNG")
    except Exception:
        return False, None, "存不下来。"
    return True, out, "重编码好了"


def repair_zip(path: str | Path, out_dir: str | Path | None = None) -> tuple[bool, Path | None, str]:
    """zip 抢救：把 CRC 还好的条目捞出来，打成一个新包。"""
    path = Path(path)
    out_dir = Path(out_dir) if out_dir else path.parent
    try:
        zf = zipfile.ZipFile(path)
    except Exception:
        return False, None, "压缩包打不开了。"
    with zf:
        names = zf.namelist()
        if not names:
            return False, None, "这是个空压缩包。"
        good = []
        for name in names:
            try:
                data = zf.read(name)
            except Exception:
                continue
            good.append((name, data))
        if not good:
            return False, None, "一个都救不回来……"
        out = out_dir / f"{path.stem}_fixed.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as new:
            for name, data in good:
                new.writestr(name, data)
    return True, out, f"救回了 {len(good)}/{len(names)} 个文件"


def repair_text(path: str | Path, out_dir: str | Path | None = None) -> tuple[bool, Path | None, str]:
    """文本转码：GBK/UTF-16 等乱码文本转成 UTF-8。"""
    path = Path(path)
    out_dir = Path(out_dir) if out_dir else path.parent
    raw = path.read_bytes()
    try:
        raw.decode("utf-8-sig")
        return True, None, "already_ok"
    except UnicodeDecodeError:
        pass
    for enc in ("gbk", "gb2312", "utf-16"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        out = out_dir / f"{path.stem}_fixed.txt"
        out.write_text(text, encoding="utf-8")
        return True, out, f"从 {enc} 转成 UTF-8 啦"
    return False, None, "这不是文本，读不懂"


def repair_file(path: str | Path, out_dir: str | Path | None = None) -> dict:
    """修一个文件。返回 {"ok", "kind", "out", "already_ok", "note"}。"""
    path = Path(path)
    if not path.exists() or not path.is_file():
        return {"ok": False, "kind": "other", "out": None, "already_ok": False, "note": "文件不见了。"}
    kind = classify_file(path)
    if kind == "image":
        ok, out, note = repair_image(path, out_dir)
    elif kind == "zip":
        ok, out, note = repair_zip(path, out_dir)
    elif kind == "text":
        ok, out, note = repair_text(path, out_dir)
    else:
        return {"ok": False, "kind": kind, "out": None, "already_ok": False, "note": "这个我不会修……"}
    return {"ok": ok, "kind": kind,
            "out": str(out) if out else None,
            "already_ok": note == "already_ok", "note": note}


# ---------------------------------------------------------------- 吃文件
def send_to_recycle_bin(path: str | Path) -> bool:
    """把文件移进回收站（SHFileOperation + FOF_ALLOWUNDO，可恢复）。
    非 Windows 或失败返回 False，绝不改成彻底删除。"""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", wintypes.UINT),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", wintypes.LPVOID),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        op = SHFILEOPSTRUCT()
        op.wFunc = 0x3  # FO_DELETE
        op.pFrom = str(path) + "\x00"  # ctypes 再补一个 \0，正好双空结尾
        op.fFlags = 0x40 | 0x10 | 0x4  # FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return result == 0 and not op.fAnyOperationsAborted
    except Exception:
        return False


# ---------------------------------------------------------------- 看回收站
def _parse_recycle_info(data: bytes) -> dict | None:
    """解析 $I 文件：8 字节头 + 8 字节大小 + 8 字节删除时间 + UTF-16 原路径。"""
    if len(data) < 24:
        return None
    size = struct.unpack_from("<Q", data, 8)[0]
    try:
        path = data[24:].decode("utf-16-le").split("\x00")[0]
    except Exception:
        return None
    if not path:
        return None
    # 名字用 Windows 风格切（Linux 跑测试时 Path 认不出反斜杠）
    name = path.replace("\\", "/").rstrip("/").split("/")[-1]
    return {"path": path, "name": name, "size": size}


def find_recycle_bin_items() -> list[dict]:
    """列出回收站里的东西（系统盘）。非 Windows 返回空列表。
    每项：{"path", "name", "size", "info_path", "mtime"}，按删除时间升序。"""
    if not sys.platform.startswith("win"):
        return []
    drive = os.environ.get("SystemDrive", "C:")
    base = Path(drive) / "$Recycle.Bin"
    items: list[dict] = []
    if not base.exists():
        return []
    try:
        sid_dirs = [p for p in base.iterdir() if p.is_dir()]
    except Exception:
        return []
    for sid in sid_dirs:
        try:
            infos = list(sid.glob("$I*"))
        except Exception:
            continue
        for info in infos:
            try:
                parsed = _parse_recycle_info(info.read_bytes())
            except Exception:
                continue
            if not parsed:
                continue
            parsed["info_path"] = str(info)
            try:
                parsed["mtime"] = info.stat().st_mtime
            except Exception:
                parsed["mtime"] = 0.0
            items.append(parsed)
    items.sort(key=lambda it: it["mtime"])
    return items
