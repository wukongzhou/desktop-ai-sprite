"""desktop_pet.py v3 — 拟人化桌宠（阶段一：活过来）。

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
import tkinter as tk
from pathlib import Path
from PIL import Image, ImageOps, ImageTk

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from pet_brain import (  # noqa: E402
    Brain, describe_personality, roll_personality,
)

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
        self.root = tk.Tk()
        self.scale = max(0.4, min(2.5, scale))
        self.base_size = int(220 * self.scale)
        self.profile_path = Path(profile_path)

        # ---- 性格档案：有就读，没有（或要求重掷）就掷一个并存下来
        brain = Brain.load(self.profile_path)
        fresh_profile = brain is None or new_personality
        if brain is None or new_personality:
            profile = roll_personality()
            if name:
                profile["name"] = name
            brain = Brain(profile)
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
        self._animate()

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_right_click)

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
        self.menu.add_command(label="💤 睡觉 / 叫醒", command=self._toggle_sleep)
        self.menu.add_command(label="🪪 看看性格卡", command=self._show_profile)
        self.menu.add_command(label="🎲 换个性格（重掷）", command=self._reroll)
        if sys.platform.startswith("win"):
            self.menu.add_command(label="🔄 开机自启 开/关", command=self._toggle_autostart)
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

    def _show_profile(self) -> None:
        self.bubble_text = describe_personality(self.brain.profile)
        self.bubble_timer = 200

    def _reroll(self) -> None:
        profile = roll_personality()
        old_name = self.brain.name
        self.brain = Brain(profile, has_ai_motion=bool(self.animation_frames))
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
        elif self.state == "playing":
            dy = -abs(math.sin(t * 2.0)) * 8

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
        elif self.state == "walking":
            for foot in (-13, 13):
                fx = cx + foot + int(math.sin(t * 4) * 5)
                self.canvas.create_oval(fx - 4, shadow_y - 3, fx + 4, shadow_y + 1, fill="#ffebbb", outline="")

        # Draw speech bubble if active
        if self.bubble_timer > 0:
            self.bubble_timer -= 1
            self.canvas.create_rectangle(
                14, 6, self.base_size + 46, 36, fill="#fff8ef", outline="#d8754e", width=2
            )
            self.canvas.create_text(
                cx, 21, text=self.bubble_text, fill="#302a27", font=("Microsoft YaHei", 9, "bold")
            )

        # 每 ~30 秒存一次档案（心情、被戳次数不断档）
        self._save_tick += 1
        if self._save_tick >= 900:
            self._save_tick = 0
            try:
                self.brain.save(self.profile_path)
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
