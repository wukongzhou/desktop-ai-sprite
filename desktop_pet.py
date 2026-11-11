from __future__ import annotations

import argparse
import math
import random
import sys
import tkinter as tk
from pathlib import Path
from PIL import Image, ImageTk

ROOT = Path(__file__).parent
DEFAULT_SPRITE = ROOT / "saved_sprites" / "latest_sprite.png"


class DesktopSpriteApp:
    """Frameless, always-on-top transparent Windows desktop pet runner."""

    def __init__(self, sprite_path: Path, name: str = "抽象小精灵", scale: float = 1.0, animation_path: Path | None = None):
        self.root = tk.Tk()
        self.name = name
        self.scale = max(0.4, min(2.5, scale))
        self.base_size = int(220 * self.scale)

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
        if self.animation_frames:
            self.state = "ai_motion"
        if sys.platform.startswith("win"):
            # Tk 在 Windows 上用"透明色键"实现透明：半透明边缘会和键色混出一圈深色毛边。
            # 把 alpha 二值化，3D 萌宠的柔和边缘在桌面上就是干净的。
            alpha = self.raw_image.getchannel("A").point(lambda v: 255 if v >= 110 else 0)
            self.raw_image.putalpha(alpha)
        self.state = "ai_motion" if self.animation_frames else "idle"  # AI loop when available, otherwise idle
        self.tick = 0
        self.drag_offset_x = 0
        self.drag_offset_y = 0
        self.roam_enabled = False
        self.vx = 1.8
        self.action_started = 0

        self.quotes = {
            "idle": [f"嗨！我是{self.name} ✨", "今天写代码灵感爆棚！", "双击我可以换个表情哦~"],
            "happy": ["芜湖！特点放大100倍！🎉", "太酷啦！蓄力跳跃！", "给你比个大大大的赞！"],
            "curious": ["咦？你在看什么好东西？👀", "让我凑近瞅一眼...", "发现华点！"],
            "grumpy": ["哼！别戳我大脑袋！😤", "本精灵需要喝杯冰美式！", "略略略~"],
            "sleepy": ["呼噜噜... 待机省电中 💤", "眼皮好沉呀...", "Zzz..."],
        }
        self.bubble_text = random.choice(self.quotes["idle"])
        self.bubble_timer = 90

        self.tk_img = None
        self._bind_events()
        self._build_context_menu()
        self._animate()

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_motion)
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
        self.menu.add_command(label="❌ 退出桌面精灵", command=self.root.destroy)

    def set_state(self, new_state: str) -> None:
        self.state = new_state
        self.action_started = self.tick
        action_quotes = {
            "walking": ["出发散步啦！", "左看看右看看～"],
            "jumping": ["蹦！再蹦一下！", "看我弹跳！"],
            "pickup": ["发现了一个小东西！", "我来拿一下～"],
            "computer": ["认真敲键盘中…", "今天也要努力工作！"],
            "playing": ["自己玩球也很开心！", "接住！"],
        }
        self.bubble_text = random.choice(action_quotes.get(new_state, self.quotes.get(new_state, self.quotes["idle"])))
        self.bubble_timer = 110

    def toggle_roam(self) -> None:
        self.roam_enabled = not self.roam_enabled
        self.bubble_text = "开启桌面巡游模式！" if self.roam_enabled else "已固定在当前位置~"
        self.bubble_timer = 80

    def _on_drag_start(self, event) -> None:
        self.drag_offset_x = event.x
        self.drag_offset_y = event.y

    def _on_drag_motion(self, event) -> None:
        nx = self.root.winfo_x() + event.x - self.drag_offset_x
        ny = self.root.winfo_y() + event.y - self.drag_offset_y
        self.x, self.y = nx, ny
        self.root.geometry(f"+{nx}+{ny}")

    def _on_double_click(self, _event) -> None:
        states = ["idle", "happy", "curious", "grumpy", "sleepy", "walking", "jumping", "pickup", "computer", "playing"]
        if self.animation_frames:
            states.append("ai_motion")
        idx = (states.index(self.state) + 1) % len(states)
        self.set_state(states[idx])

    def _on_right_click(self, event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def _animate(self) -> None:
        self.tick += 1
        t = self.tick * 0.12

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
        elif self.state == "walking":
            dx = math.sin(t * 0.8) * 9
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
            dx = math.sin(t * 1.2) * 7
            dy = -abs(math.sin(t * 2.0)) * 8

        if self.roam_enabled or self.state == "walking":
            screen_w = self.root.winfo_screenwidth()
            self.x += int(self.vx)
            if self.x < 20 or self.x > screen_w - self.base_size - 80:
                self.vx = -self.vx
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

        self.root.after(33, self._animate)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Always-on-Top Abstract Desktop Sprite")
    parser.add_argument("--sprite", type=str, default=str(DEFAULT_SPRITE), help="Path to transparent sprite PNG")
    parser.add_argument("--name", type=str, default="抽象小精灵", help="Sprite display name")
    parser.add_argument("--scale", type=float, default=1.0, help="Sprite size multiplier")
    parser.add_argument("--animation", type=str, default="", help="Optional AI-generated animated WebP")
    args = parser.parse_args()

    sprite_path = Path(args.sprite)
    if not sprite_path.exists():
        print(f"Sprite file not found: {sprite_path}. Please generate a sprite in Cartoon Studio first.")
        sys.exit(1)

    animation_path = Path(args.animation) if args.animation else None
    pet = DesktopSpriteApp(sprite_path=sprite_path, name=args.name, scale=args.scale, animation_path=animation_path)
    pet.run()


if __name__ == "__main__":
    main()

