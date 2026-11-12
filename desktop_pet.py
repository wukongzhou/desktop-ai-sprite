"""desktop_pet.py v3.5.1 — 拟人化桌宠（换性格必换名）。

在 v2 的透明窗、拖拽、右键菜单、AI 动作循环基础上，接上 pet_brain：
精灵自己随机动（走、跳、睡、发呆、凑近鼠标、假装忙……），心情值涨跌，
戳它会生气（连戳会躲起来），双击会说心里话，性格存在 pet_profile.json 里。

运行（Windows）：
    python desktop_pet.py                      # 用 saved_sprites/latest_sprite.png
    python desktop_pet.py --sprite xxx.png --animation yyy.webp
    python desktop_pet.py --new-personality    # 换一个性格重新掷
    python desktop_pet.py --profile 我的精灵.json
性格档案默认保存在脚本旁边的 pet_profile.json，第一次运行自动掷一个。
"""
from __future__ import annotations

import argparse
import math
import os
import random
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from PIL import Image, ImageOps, ImageTk

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from pet_brain import (  # noqa: E402
    ASK_MB, FULLNESS_REFUSE, Brain, describe_personality, roll_personality,
)

try:
    import pet_tools
except Exception:
    pet_tools = None

try:
    import pet_memory
except Exception:
    pet_memory = None

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except Exception:
    TkinterDnD = None
    DND_FILES = None

DEFAULT_SPRITE = ROOT / "saved_sprites" / "latest_sprite.png"
DEFAULT_PROFILE = ROOT / "pet_profile.json"

# 大脑行为 id -> 渲染分支
RENDER_STATE = {
    "idle_look": "idle", "walk": "walking", "jump": "jumping",
    "sleep": "sleepy", "stretch": "stretch", "excited": "happy",
    "grumpy": "grumpy", "curious_approach": "curious",
    "work_busy": "computer", "snack_search": "curious",
    "shy_hide": "walking", "dance": "happy", "ai_motion": "ai_motion",
    "pickup": "pickup", "playing": "playing",
    "eating": "happy", "overstuffed": "sleepy", "repairing": "repairing",
    "screen_slam": "slam",
}
# 渲染分支 -> 大脑行为 id（右键菜单手动点）
BEHAVIOR_FOR_STATE = {v: k for k, v in RENDER_STATE.items()}


class DesktopSpriteApp:
    """Frameless, always-on-top transparent Windows desktop pet runner."""

    def __init__(self, sprite_path: Path, name: str | None = None, scale: float = 1.0,
                 animation_path: Path | None = None,
                 profile_path: Path = DEFAULT_PROFILE,
                 new_personality: bool = False,
                 boot: bool = False):
        self._dnd = False
        if TkinterDnD is not None:
            try:
                self.root = TkinterDnD.Tk()
                self._dnd = True
            except Exception:
                self.root = tk.Tk()
        else:
            self.root = tk.Tk()
        self.scale = max(0.4, min(2.5, scale))
        self.base_size = int(220 * self.scale)
        self.profile_path = Path(profile_path)
        self.memory_path = self.profile_path.with_name("pet_memory.json")

        # ---- 性格档案：有就读，没有（或要求重掷）就掷一个并存下来
        brain = Brain.load(self.profile_path, memory_path=self.memory_path)
        fresh_profile = brain is None or new_personality
        if brain is None or new_personality:
            profile = roll_personality()
            if name:
                profile["name"] = name
            brain = Brain(profile, memory_path=self.memory_path)
            brain.save(self.profile_path)
            print("新精灵诞生：", describe_personality(profile))
        elif name:
            brain.profile["name"] = name
            brain.name = name
        self.brain = brain
        self.name = brain.name

        self.root.title(f"桌面精灵 - {self.name}")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        # Configure transparent key color on Windows; fallback cleanly on other OS
        self.transparent_key = "#010203"
        self.root.configure(bg=self.transparent_key)
        if sys.platform.startswith("win"):
            self.root.wm_attributes("-transparentcolor", self.transparent_key)
        else:
            try:
                self.root.wm_attributes("-alpha", 0.96)
            except Exception:
                pass

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.x = max(60, screen_w - self.base_size - 120)
        self.y = max(60, screen_h - self.base_size - 160)
        self.root.geometry(f"{self.base_size + 60}x{self.base_size + 80}+{self.x}+{self.y}")

        self.canvas = tk.Canvas(
            self.root,
            width=self.base_size + 60,
            height=self.base_size + 80,
            bg=self.transparent_key,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.raw_image = Image.open(sprite_path).convert("RGBA")
        self.animation_frames = []
        if animation_path and animation_path.exists():
            try:
                anim = Image.open(animation_path)
                for index in range(getattr(anim, "n_frames", 1)):
                    anim.seek(index)
                    frame = anim.convert("RGBA").copy()
                    if sys.platform.startswith("win"):
                        frame.putalpha(frame.getchannel("A").point(lambda v: 255 if v >= 110 else 0))
                    self.animation_frames.append(frame)
            except Exception as exc:
                print(f"Could not read AI motion WebP: {exc}")
        if sys.platform.startswith("win"):
            # Tk 在 Windows 上用"透明色键"实现透明：半透明边缘会和键色混出一圈深色毛边。
            # 把 alpha 二值化，3D 萌宠的柔和边缘在桌面上就是干净的。
            alpha = self.raw_image.getchannel("A").point(lambda v: 255 if v >= 110 else 0)
            self.raw_image.putalpha(alpha)

        self.brain.has_ai_motion = bool(self.animation_frames)
        self.state = "ai_motion" if self.animation_frames else "idle"
        self.tick = 0
        self.drag_offset_x = 0
        self.drag_offset_y = 0
        self._press_x = 0
        self._press_y = 0
        self._press_tick = 0
        self._dragged = False
        self._click_seq = 0  # 点按序号：双击到来时作废等着的那一下戳
        self.vx = 1.8
        self.facing = 1  # 1 朝右，-1 朝左
        self._save_tick = 0
        self._pending_file: dict | None = None  # 拖进来 / 选进来的文件，等主人选吃还是修
        self._recycle_queue: list[dict] = []
        self._recycle_seen: set[str] = set()
        self._recycle_ignore_until = 0.0
        self._repair_busy = False
        self._repair_result: dict | None = None
        self._repair_started = 0.0
        self._menu_eat_label = "🍽️ 吃掉这个文件"
        self._menu_fix_label = "🔧 修修这个文件"
        self._last_pointer = (0, 0)
        self._pointer_idle_since = time.monotonic()
        self._bubble_scale = 1.0
        self._bubble_text_shown = ""
        self._slam_sounded = False

        self.bubble_text = f"嗨！我是{self.name} ✨"
        self.bubble_timer = 110
        if boot:
            # 开机启动进来的：像刚睡醒一样伸个懒腰
            self.brain.force("stretch")
            self.bubble_text = "（伸懒腰）睡醒啦！今天也要元气满满！"
            self.bubble_timer = 160

        self.tk_img = None
        self._bind_events()
        self._build_context_menu()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._autostart_done = False
        if fresh_profile and sys.platform.startswith("win"):
            # 第一次启动：晚 1.5 秒再注册开机启动，先让精灵露脸
            self.root.after(1500, self._ensure_autostart_once)
        if pet_tools is not None:
            try:
                self._recycle_seen = {it["info_path"] for it in pet_tools.find_recycle_bin_items()}
            except Exception:
                self._recycle_seen = set()
            self.root.after(1500, self._poll_recycle_bin)
        self._animate()

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        if self._dnd:
            try:
                self.canvas.drop_target_register(DND_FILES)
                self.canvas.dnd_bind("<<Drop>>", self._on_file_drop)
            except Exception:
                self._dnd = False

    def _build_context_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="✨ 待机呼吸 (Idle)", command=lambda: self.set_state("idle"))
        self.menu.add_command(label="🎉 开心弹跳 (Happy)", command=lambda: self.set_state("happy"))
        self.menu.add_command(label="👀 好奇歪头 (Curious)", command=lambda: self.set_state("curious"))
        self.menu.add_command(label="😤 傲娇抖动 (Grumpy)", command=lambda: self.set_state("grumpy"))
        self.menu.add_command(label="💤 犯困打盹 (Sleepy)", command=lambda: self.set_state("sleepy"))
        if self.animation_frames:
            self.menu.add_separator()
            self.menu.add_command(label="🎞️ 播放 AI 全身动作", command=lambda: self.set_state("ai_motion"))
        else:
            self.menu.add_separator()
            self.menu.add_command(label="🚶 来回走动（基础动作）", command=lambda: self.set_state("walking"))
            self.menu.add_command(label="🦘 蹦蹦跳跳（基础动作）", command=lambda: self.set_state("jumping"))
            self.menu.add_command(label="📦 拿取小物件（基础动作）", command=lambda: self.set_state("pickup"))
            self.menu.add_command(label="💻 使用电脑（基础动作）", command=lambda: self.set_state("computer"))
            self.menu.add_command(label="⚽ 自己玩球（基础动作）", command=lambda: self.set_state("playing"))
        self.menu.add_separator()
        self.menu.add_command(label="🚶 切换桌面自由巡游", command=self.toggle_roam)
        self.menu.add_separator()
        self.menu.add_command(label="💛 哄它一下", command=self._cheer)
        self.menu.add_command(label="🤲 摸摸头", command=self._pet_head)
        self.menu.add_command(label="⚽ 陪它玩一会儿", command=self._play_with)
        self.menu.add_command(label="💬 问问它在想什么", command=self._ask_it)
        if pet_memory is not None:
            self.menu.add_command(label="📖 教它一句话…", command=self._teach_via_dialog)
            self.menu.add_command(label="🧠 它记得什么？", command=self._show_memory)
        self.menu.add_command(label="💤 睡觉 / 叫醒", command=self._toggle_sleep)
        self.menu.add_command(label="🪪 看看性格卡", command=self._show_profile)
        self.menu.add_command(label="🎲 换个性格（重掷）", command=self._reroll)
        if sys.platform.startswith("win"):
            self.menu.add_command(label="🔄 开机自启 开/关", command=self._toggle_autostart)
        if pet_tools is not None:
            self.menu.add_separator()
            self.menu.add_command(label="🍽️ 喂它吃个文件…", command=self._feed_via_dialog)
            self.menu.add_command(label="🔧 让它修个文件…", command=self._repair_via_dialog)
            self.menu.add_command(label="🍽️ 吃掉这个文件", command=self._eat_pending_file, state="disabled")
            self.menu.add_command(label="🔧 修修这个文件", command=self._repair_pending_file, state="disabled")
            self.menu.add_command(label="🚫 算了，不吃了", command=self._cancel_pending)
        self.menu.add_separator()
        self.menu.add_command(label="❌ 退出桌面精灵", command=self._on_close)

    def set_state(self, new_state: str) -> None:
        bid = BEHAVIOR_FOR_STATE.get(new_state, "idle_look")
        self.brain.force(bid)
        self.state = new_state
        self.bubble_text = self.brain.bubble_for(bid)
        self.bubble_timer = 110

    def toggle_roam(self) -> None:
        self.brain.roam = not self.brain.roam
        self.bubble_text = "开启桌面巡游模式！" if self.brain.roam else "已固定在当前位置~"
        self.bubble_timer = 80

    def _cheer(self) -> None:
        self.bubble_text = self.brain.on_cheer()
        self.bubble_timer = 110

    # ---------------------------------------------------------- 主人互动
    def _pet_head(self) -> None:
        _bid, text = self.brain.on_pet()
        self.bubble_text = text
        self.bubble_timer = 140

    def _play_with(self) -> None:
        _bid, text = self.brain.on_play()
        self.bubble_text = text
        self.bubble_timer = 140

    def _ask_it(self) -> None:
        self.bubble_text = self.brain.on_ask()
        self.bubble_timer = 200

    # ---------------------------------------------------------- 学习功能
    def _teach_via_dialog(self) -> None:
        text = simpledialog.askstring("教它一句话", "教它点什么？比如：我喜欢喝咖啡、我叫亮哥、我每天 9 点上班")
        if text:
            self._teach_text(text)

    def _teach_text(self, text: str) -> None:
        _bid, reply = self.brain.on_teach(text)
        self.bubble_text = reply
        self.bubble_timer = 200
        self.brain.save_memory()

    def _show_memory(self) -> None:
        summary = self.brain.memory.summary()
        messagebox.showinfo(
            f"它记得的事 · {self.name}",
            summary if summary else "我还什么都没记住呢，教教我吧～")

    # ---------------------------------------------------------- 砸屏幕
    def _play_thud(self) -> None:
        """砸屏幕的“咚咚”声（低频两下，线程里响，不卡画面）。非 Windows 没声。"""
        if not sys.platform.startswith("win"):
            return

        def _beep() -> None:
            try:
                import winsound
                winsound.Beep(110, 130)
                winsound.Beep(90, 100)
            except Exception:
                pass

        threading.Thread(target=_beep, daemon=True).start()



    def _toggle_sleep(self) -> None:
        _bid, text = self.brain.on_wake_toggle()
        self.bubble_text = text
        self.bubble_timer = 110

    def _shortcut_script(self) -> Path:
        return ROOT / "创建快捷方式.ps1"

    def _run_shortcut_script(self, action: str) -> bool:
        ps1 = self._shortcut_script()
        if not ps1.exists():
            return False
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(ps1), "-Action", action, "-Dir", str(ROOT)],
                capture_output=True, timeout=60)
            return r.returncode == 0
        except Exception:
            return False

    def _startup_link(self) -> Path:
        appdata = os.environ.get("APPDATA", "")
        return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
                / "Programs" / "Startup" / "桌面精灵.lnk")

    def _ensure_autostart_once(self) -> None:
        if self._autostart_done or not sys.platform.startswith("win"):
            return
        self._autostart_done = True
        if self._run_shortcut_script("install"):
            print("已加入开机启动。")

    def _toggle_autostart(self) -> None:
        if not sys.platform.startswith("win"):
            self.bubble_text = "开机自启只在 Windows 上可用。"
            self.bubble_timer = 110
            return
        link = self._startup_link()
        if link.exists():
            ok = self._run_shortcut_script("remove")
            self.bubble_text = "已取消开机自启。" if ok else "取消失败，手动删掉开机启动里的「桌面精灵」就行。"
        else:
            ok = self._run_shortcut_script("install")
            self.bubble_text = "开机自启已开启，下次开机它就醒了！" if ok else "没找到 创建快捷方式.ps1，装不上开机启动。"
        self.bubble_timer = 140

    # ---------------------------------------------------------- 阶段二：吃文件 / 修文件
    def _offer_file(self, path: Path, ask_text: str) -> None:
        """拖进来 / 选进来的文件先放着，等主人右键选吃还是修。"""
        self._pending_file = {"path": str(path), "at": time.monotonic()}
        for label in (self._menu_eat_label, self._menu_fix_label):
            try:
                self.menu.entryconfig(label, state="normal")
            except Exception:
                pass
        self.bubble_text = ask_text
        self.bubble_timer = 220

    def _clear_pending_file(self) -> None:
        self._pending_file = None
        for label in (self._menu_eat_label, self._menu_fix_label):
            try:
                self.menu.entryconfig(label, state="disabled")
            except Exception:
                pass

    def _on_file_drop(self, event) -> None:
        try:
            paths = self.root.tk.splitlist(event.data)
        except Exception:
            return
        if not paths:
            return
        path = Path(paths[0])
        if not path.exists() or not path.is_file():
            self.bubble_text = "这个我接不住，再拖一个文件试试～"
            self.bubble_timer = 120
            return
        if len(paths) > 1:
            self.bubble_text = "一次一个哦，先处理这个～"
            self.bubble_timer = 120
        self._offer_file(path, "这个文件……吃掉还是修修看？右键菜单选一个～")

    def _feed_via_dialog(self) -> None:
        path = filedialog.askopenfilename(title="喂它吃个文件（会移进回收站，可恢复）")
        if path:
            self._feed_file(Path(path))

    def _feed_file(self, path: Path, confirmed: bool = False) -> None:
        if pet_tools is None:
            self.bubble_text = "缺少 pet_tools.py，吃不了文件。"
            self.bubble_timer = 120
            return
        if not path.exists() or not path.is_file():
            return
        size = path.stat().st_size
        if self.brain.fullness >= FULLNESS_REFUSE:
            _bid, text = self.brain.on_eat(size, pet_tools.classify_file(path), path.name)
            self.bubble_text = text
            self.bubble_timer = 140
            return
        if not confirmed and size > ASK_MB * 1024 * 1024:
            self._offer_file(path, "这个好大……真的要我吃吗？右键选'吃掉这个文件'。")
            return
        self._do_eat(path, size)

    def _do_eat(self, path: Path, size: int) -> None:
        kind = pet_tools.classify_file(path)
        ok = pet_tools.send_to_recycle_bin(path) if sys.platform.startswith("win") else False
        if not ok:
            self.bubble_text = "吃不下……放不进回收站。"
            self.bubble_timer = 140
            return
        self._recycle_ignore_until = time.monotonic() + 12
        _bid, text = self.brain.on_eat(size, kind, path.name)
        self.bubble_text = text
        self.bubble_timer = 160
        try:
            self.brain.save(self.profile_path)
        except Exception:
            pass

    def _eat_pending_file(self) -> None:
        if not self._pending_file:
            return
        path = Path(self._pending_file["path"])
        self._clear_pending_file()
        if not path.exists():
            self.bubble_text = "咦，文件不见了？"
            self.bubble_timer = 120
            return
        self._feed_file(path, confirmed=True)

    def _repair_via_dialog(self) -> None:
        path = filedialog.askopenfilename(title="让它修个文件（图片 / 压缩包 / 文本）")
        if path:
            self._repair_file(Path(path))

    def _repair_pending_file(self) -> None:
        if not self._pending_file:
            return
        path = Path(self._pending_file["path"])
        self._clear_pending_file()
        if not path.exists():
            self.bubble_text = "咦，文件不见了？"
            self.bubble_timer = 120
            return
        self._repair_file(path)

    def _repair_file(self, path: Path) -> None:
        if pet_tools is None:
            self.bubble_text = "缺少 pet_tools.py，修不了。"
            self.bubble_timer = 120
            return
        if self._repair_busy:
            self.bubble_text = "还在修上一个呢，等会儿～"
            self.bubble_timer = 120
            return
        self._repair_busy = True
        self._repair_result: dict | None = None
        self._repair_started = time.monotonic()
        self.bubble_text = self.brain.on_repair_start()
        self.bubble_timer = 160

        def _work() -> None:
            try:
                self._repair_result = pet_tools.repair_file(path)
            except Exception:
                self._repair_result = {"ok": False, "kind": "other", "out": None,
                                       "already_ok": False, "note": "修的时候出错了。"}

        threading.Thread(target=_work, daemon=True).start()
        self.root.after(120, self._check_repair_done)

    def _check_repair_done(self) -> None:
        if self._repair_result is None:
            if self._repair_busy and time.monotonic() - self._repair_started < 30:
                self.root.after(120, self._check_repair_done)
            elif self._repair_busy:
                self._repair_busy = False
                self.bubble_text = "修得有点久，先算了。"
                self.bubble_timer = 140
            return
        self._finish_repair(self._repair_result)

    def _finish_repair(self, result: dict) -> None:
        self._repair_busy = False
        _bid, text = self.brain.on_repair(result)
        self.bubble_text = text
        self.bubble_timer = 200
        try:
            self.brain.save(self.profile_path)
        except Exception:
            pass

    def _cancel_pending(self) -> None:
        had = False
        if self.brain.pending_meal:
            self.brain.cancel_pending_meal()
            had = True
        if self._pending_file:
            self._clear_pending_file()
            had = True
        self.bubble_text = "好吧，不吃了。" if had else "没什么要不吃的呀。"
        self.bubble_timer = 120

    def _poll_recycle_bin(self) -> None:
        try:
            if pet_tools is not None:
                now = time.monotonic()
                if self.brain.pending_meal and now - self.brain.pending_meal_at > 60:
                    self.brain.cancel_pending_meal()
                    self.bubble_text = "算了，不吃了。"
                    self.bubble_timer = 120
                if self._pending_file and now - self._pending_file.get("at", 0) > 60:
                    self._clear_pending_file()
                    self.bubble_text = "算了，先放着～"
                    self.bubble_timer = 120
                items = pet_tools.find_recycle_bin_items()
                for it in items:
                    if it["info_path"] not in self._recycle_seen:
                        self._recycle_seen.add(it["info_path"])
                        if now < self._recycle_ignore_until:
                            continue
                        self._recycle_queue.append(it)
                if not self.brain.pending_meal and self._recycle_queue:
                    self._handle_recycle_item(self._recycle_queue.pop(0))
        except Exception:
            pass
        self.root.after(pet_tools.RECYCLE_POLL_SECONDS * 1000, self._poll_recycle_bin)

    def _handle_recycle_item(self, item: dict) -> None:
        size = item["size"]
        kind = pet_tools.classify_file(Path(item["path"]))
        name = item.get("name") or Path(item["path"]).name
        if self.brain.fullness >= FULLNESS_REFUSE:
            self.bubble_text = "唔，肚子还鼓着呢，这顿先不吃了。"
            self.bubble_timer = 140
            return
        if size > ASK_MB * 1024 * 1024:
            item = dict(item)
            item["kind"] = kind
            item["name"] = name
            ask = self.brain.set_pending_meal(item)
            self.bubble_text = ask + "点我一下就是'吃吧'！"
            self.bubble_timer = 220
            return
        _bid, text = self.brain.on_eat(size, kind, name)
        self.bubble_text = text
        self.bubble_timer = 160
        try:
            self.brain.save(self.profile_path)
        except Exception:
            pass

    def _show_profile(self) -> None:
        messagebox.showinfo(
            f"性格卡 · {self.name}",
            describe_personality(self.brain.profile) + "\n\n" + self.brain.status_line())

    def _reroll(self) -> None:
        old_name = self.brain.name
        # 保证换出不一样的名字：同名就重掷（名字池 16 个，最多 50 次兜底）
        profile = roll_personality()
        tries = 1
        while profile["name"] == old_name and tries < 50:
            profile = roll_personality()
            tries += 1
        self.brain = Brain(profile, has_ai_motion=bool(self.animation_frames),
                           memory_path=self.memory_path)
        self.brain.save(self.profile_path)
        self.name = self.brain.name
        self.root.title(f"桌面精灵 - {self.name}")
        self.bubble_text = f"我是新来的{self.name}！{old_name}先下班啦～"
        self.bubble_timer = 140

    def _on_drag_start(self, event) -> None:
        self.drag_offset_x = event.x
        self.drag_offset_y = event.y
        self._press_x, self._press_y = event.x, event.y
        self._press_tick = self.tick
        self._dragged = False

    def _on_drag_motion(self, event) -> None:
        if abs(event.x - self._press_x) + abs(event.y - self._press_y) > 6:
            self._dragged = True
        nx = self.root.winfo_x() + event.x - self.drag_offset_x
        ny = self.root.winfo_y() + event.y - self.drag_offset_y
        self.x, self.y = nx, ny
        self.root.geometry(f"+{nx}+{ny}")

    def _on_release(self, _event) -> None:
        was_drag = self._dragged
        quick = self.tick - self._press_tick < 12
        if was_drag:
            # 拖完就晕，无需等双击判定
            _bid, text = self.brain.on_drag_end()
            self.bubble_text = text
            self.bubble_timer = 110
        elif quick:
            # 轻点一下 = 戳它；但先等 260ms，看是不是双击的前半下
            seq = self._click_seq + 1
            self._click_seq = seq
            self.root.after(260, lambda s=seq: self._maybe_poke(s))

    def _maybe_poke(self, seq: int) -> None:
        if seq != self._click_seq:
            return  # 期间来了双击，这一下不算戳
        if self.brain.pending_meal:
            result = self.brain.confirm_pending_meal()
            if result:
                _bid, text = result
                self.bubble_text = text
                self.bubble_timer = 160
                try:
                    self.brain.save(self.profile_path)
                except Exception:
                    pass
            return
        _bid, text = self.brain.on_poke()
        self.bubble_text = text
        self.bubble_timer = 110

    def _on_double_click(self, _event) -> None:
        self._click_seq += 1  # 作废等着的那一下戳
        self.bubble_text = self.brain.on_double_click()
        self.bubble_timer = 140

    def _on_right_click(self, event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def _on_close(self) -> None:
        try:
            self.brain.save(self.profile_path)
            self.brain.save_memory()
        except Exception:
            pass
        self.root.destroy()

    def _animate(self) -> None:
        self.tick += 1
        t = self.tick * 0.12

        # 大脑推进一帧；行为刚切换就换一句气泡
        changed = self.brain.tick(0.033)
        if changed:
            self.bubble_text = self.brain.bubble_for(self.brain.current)
            self.bubble_timer = 110
            self._slam_sounded = False
        if self.brain.emotion_changed:
            self.brain.emotion_changed = False
            self.bubble_text = self.brain.emotion_line()
            self.bubble_timer = 110
        # 鼠标 60 秒没动：它自己凑过来问你在干嘛
        try:
            px, py = self.root.winfo_pointerx(), self.root.winfo_pointery()
        except Exception:
            px, py = self._last_pointer
        if (px, py) != self._last_pointer:
            self._last_pointer = (px, py)
            self._pointer_idle_since = time.monotonic()
        elif (time.monotonic() - self._pointer_idle_since > 60
              and self.brain.current not in ("sleep", "overstuffed")
              and not self.brain.pending_meal and not self._pending_file):
            self._pointer_idle_since = time.monotonic()
            self.brain.force("curious_approach")
            self.bubble_text = f"{self.brain.owner_name()}，你在干嘛呀？"
            self.bubble_timer = 160
        self.state = RENDER_STATE.get(self.brain.current, "idle")

        # Compute squash/stretch, bounce, and tilt by state
        sx, sy, dy, angle = 1.0, 1.0, 0.0, 0.0
        if self.state == "idle":
            sx = 1.0 + math.sin(t) * 0.035
            sy = 1.0 - math.sin(t) * 0.035
            dy = math.sin(t) * 5.0
        elif self.state == "happy":
            bounce = abs(math.sin(t * 1.8))
            sx = 1.0 - bounce * 0.08 + (1.0 - bounce) * 0.06
            sy = 1.0 + bounce * 0.10 - (1.0 - bounce) * 0.06
            dy = -bounce * 22.0
            angle = math.sin(t * 0.9) * 6.0
            if self.brain.current == "dance":
                angle = math.sin(t * 2.2) * 14.0
        elif self.state == "curious":
            angle = 12.0 * math.sin(t * 0.7)
            sx = 1.04
            sy = 0.98
            dy = math.sin(t) * 3.0
        elif self.state == "grumpy":
            dy = math.sin(t * 4.5) * 2.5
            angle = math.cos(t * 5.0) * 4.0
            sx = 1.05 + math.sin(t * 3.0) * 0.03
        elif self.state == "sleepy":
            sx = 1.0 + math.sin(t * 0.45) * 0.04
            sy = 0.94 - math.sin(t * 0.45) * 0.04
            dy = 6.0 + math.sin(t * 0.45) * 3.0
        elif self.state == "stretch":
            sx = 0.94
            sy = 1.0 + max(0.0, math.sin(t * 1.2)) * 0.12
            dy = -4.0
        elif self.state == "walking":
            dy = abs(math.sin(t * 4.0)) * 3
            angle = math.sin(t * 4.0) * 2.0
        elif self.state == "jumping":
            dy = -abs(math.sin(t * 1.5)) * 24
        elif self.state == "pickup":
            reach = max(0.0, math.sin(t * 0.65))
            angle = reach * 7.0
            dy = reach * 4.0
        elif self.state == "computer":
            dy = math.sin(t * 3.0) * 1.5
        elif self.state == "repairing":
            angle = math.sin(t * 3.0) * 4.0
            dy = math.sin(t * 3.0) * 2.0
        elif self.state == "slam":
            bt = self.brain.behavior_t
            if bt < 0.3:
                # 蓄力：往后仰
                sy = 1.0 + min(0.12, bt * 0.4)
                sx = 1.0 - min(0.08, bt * 0.3)
                dy = -6.0
                angle = -8.0
            elif bt < 0.7:
                # 砸下去：压扁 + 抖
                sy = 0.78
                sx = 1.12
                dy = 10.0
                angle = math.sin(t * 30.0) * 5.0
            else:
                # 回弹
                sy = 1.0 + math.sin(t * 2.0) * 0.05
                dy = -4.0
        elif self.state == "playing":
            dy = -abs(math.sin(t * 2.0)) * 8
        # 砸屏幕的“咚咚”声：只响一次（低频，两下）
        if self.state == "slam" and 0.3 <= self.brain.behavior_t < 0.4 and not self._slam_sounded:
            self._slam_sounded = True
            self._play_thud()

        # ---- 移动：走路 / 巡游 / 凑近鼠标 / 躲起来
        screen_w = self.root.winfo_screenwidth()
        if self.brain.current in ("walk", "shy_hide") or self.brain.roam:
            speed = abs(self.vx) * (2.2 if self.brain.current == "shy_hide" else 1.0)
            if self.brain.current == "shy_hide":
                # 往最近的屏幕边躲
                self.vx = -speed if self.x < screen_w // 2 else speed
            self.x += int(speed) if self.vx > 0 else -int(speed)
            if self.x < 20 or self.x > screen_w - self.base_size - 80:
                self.vx = -self.vx
                self.x = max(20, min(screen_w - self.base_size - 80, self.x))
            self.facing = 1 if self.vx > 0 else -1
            self.root.geometry(f"+{self.x}+{self.y}")
        elif self.brain.current == "curious_approach":
            try:
                px = self.root.winfo_pointerx()
            except Exception:
                px = self.x + self.base_size // 2
            target = px - (self.x + self.base_size // 2)
            if abs(target) > 12:
                step = 3 if target > 0 else -3
                self.x += step
                self.facing = 1 if step > 0 else -1
                self.x = max(20, min(screen_w - self.base_size - 80, self.x))
                self.root.geometry(f"+{self.x}+{self.y}")

        w = max(32, int(self.base_size * sx))
        h = max(32, int(self.base_size * sy))
        if self.state == "ai_motion" and self.animation_frames:
            motion = self.animation_frames[(self.tick // 3) % len(self.animation_frames)]
            frame = motion.resize((self.base_size, self.base_size), Image.Resampling.LANCZOS)
            dy = 0
            angle = 0
        else:
            frame = self.raw_image.resize((w, h), Image.Resampling.BILINEAR)
            if self.facing < 0:
                frame = ImageOps.mirror(frame)
        if abs(angle) > 0.4:
            frame = frame.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)

        self.tk_img = ImageTk.PhotoImage(frame)
        self.canvas.delete("all")

        cx = (self.base_size + 60) // 2
        cy = (self.base_size + 80) // 2 + 12

        # Draw ground shadow
        shadow_rx = int(self.base_size * 0.28 * sx)
        shadow_ry = 8
        shadow_y = cy + self.base_size // 2 - 8
        self.canvas.create_oval(
            cx - shadow_rx,
            shadow_y - shadow_ry,
            cx + shadow_rx,
            shadow_y + shadow_ry,
            fill="#221d1a",
            outline="",
        )

        # Draw sprite
        self.canvas.create_image(cx, int(cy + dy), image=self.tk_img)

        # Action props give the native desktop pet a clear interaction target.
        if self.state == "pickup":
            lift = (math.sin(t * 0.65) + 1) / 2
            bx, by = cx + 62, shadow_y - 7 - int(lift * 28)
            self.canvas.create_line(cx + 25, cy + 35, bx, by, fill="#ffcf8a", width=4, smooth=True)
            self.canvas.create_rectangle(bx - 9, by - 10, bx + 9, by + 7, fill="#f3a64a", outline="#fff1c7", width=2)
        elif self.state == "computer":
            lx, ly = cx - 38, shadow_y - 30
            self.canvas.create_rectangle(lx, ly - 30, lx + 76, ly + 2, fill="#202a46", outline="#b9c7ed", width=2)
            self.canvas.create_rectangle(lx + 5, ly - 25, lx + 71, ly - 3, fill="#83d9ff", outline="")
            self.canvas.create_polygon(lx - 5, ly + 3, lx + 81, ly + 3, lx + 72, ly + 9, lx + 4, ly + 9, fill="#d8e5ff", outline="#202a46")
            if math.sin(t * 9) > 0:
                self.canvas.create_text(lx + 20, ly - 15, text="▰ ▪ ▰", fill="#fff", font=("Consolas", 8))
        elif self.state == "playing":
            bx = cx + int(math.sin(t * 1.2) * 66)
            by = shadow_y - 8 - int(abs(math.sin(t * 2.0)) * 32)
            self.canvas.create_oval(bx - 9, by - 9, bx + 9, by + 9, fill="#ffce50", outline="#fff3b0", width=2)
        elif self.state == "repairing":
            hx, hy = cx + 58, shadow_y - 26
            self.canvas.create_line(hx, hy + 14, hx + 6, hy - 6, fill="#8a5a2b", width=5)
            self.canvas.create_rectangle(hx - 4, hy - 12, hx + 16, hy - 2, fill="#9aa3ad", outline="#5f6b76", width=2)
        elif self.state == "slam" and 0.3 <= self.brain.behavior_t < 0.9:
            ix, iy = cx + 58, shadow_y - 30
            for dx, dy2 in ((-18, -14), (0, -20), (18, -12), (-24, 4), (24, 2), (-12, 14), (12, 16)):
                self.canvas.create_line(ix, iy, ix + dx, iy + dy2, fill="#ffd166", width=3)
            self.canvas.create_oval(ix - 7, iy - 7, ix + 7, iy + 7, fill="#ff5d3b", outline="#fff1c7", width=2)
        elif self.state == "walking":
            for foot in (-13, 13):
                fx = cx + foot + int(math.sin(t * 4) * 5)
                self.canvas.create_oval(fx - 4, shadow_y - 3, fx + 4, shadow_y + 1, fill="#ffebbb", outline="")

        # Draw speech bubble if active（动漫对话框：带尾巴，弹出来）
        if self.bubble_timer > 0:
            self.bubble_timer -= 1
            if self.bubble_text != self._bubble_text_shown:
                self._bubble_text_shown = self.bubble_text
                self._bubble_scale = 0.0
            self._bubble_scale = min(1.0, self._bubble_scale + 0.25)
            s = self._bubble_scale
            x0 = cx - int((cx - 14) * s)
            x1 = cx + int((self.base_size + 46 - cx) * s)
            y0 = 21 - int(15 * s)
            y1 = 21 + int(15 * s)
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#fff8ef", outline="#d8754e", width=2)
            if s >= 0.9:
                self.canvas.create_polygon(cx - 12, y1, cx + 12, y1, cx, 52,
                                           fill="#fff8ef", outline="")
                self.canvas.create_line(cx - 12, y1, cx, 52, fill="#d8754e", width=2)
                self.canvas.create_line(cx + 12, y1, cx, 52, fill="#d8754e", width=2)
            if s >= 0.7:
                self.canvas.create_text(cx, 21, text=self.bubble_text, fill="#302a27",
                                        font=("Microsoft YaHei", 9, "bold"))

        # 每 ~30 秒存一次档案（心情、被戳次数不断档）
        self._save_tick += 1
        if self._save_tick >= 900:
            self._save_tick = 0
            try:
                self.brain.save(self.profile_path)
                self.brain.save_memory()
            except Exception:
                pass

        self.root.after(33, self._animate)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Always-on-Top Abstract Desktop Sprite")
    parser.add_argument("--sprite", type=str, default=str(DEFAULT_SPRITE), help="Path to transparent sprite PNG")
    parser.add_argument("--name", type=str, default="", help="Sprite display name (overrides profile)")
    parser.add_argument("--scale", type=float, default=1.0, help="Sprite size multiplier")
    parser.add_argument("--animation", type=str, default="", help="Optional AI-generated animated WebP")
    parser.add_argument("--profile", type=str, default=str(DEFAULT_PROFILE), help="Personality profile JSON path")
    parser.add_argument("--new-personality", action="store_true", help="Roll a fresh personality")
    parser.add_argument("--boot", action="store_true", help="Wake-up mode: started from system startup")
    args = parser.parse_args()

    sprite_path = Path(args.sprite)
    if not sprite_path.exists():
        print(f"Sprite file not found: {sprite_path}. Please generate a sprite in Cartoon Studio first.")
        sys.exit(1)

    animation_path = Path(args.animation) if args.animation else None
    pet = DesktopSpriteApp(sprite_path=sprite_path, name=args.name or None, scale=args.scale,
                           animation_path=animation_path,
                           profile_path=Path(args.profile),
                           new_personality=args.new_personality,
                           boot=args.boot)
    pet.run()


if __name__ == "__main__":
    main()
