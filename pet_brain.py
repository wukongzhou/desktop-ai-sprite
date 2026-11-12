"""pet_brain.py — 桌面精灵的大脑：性格、心情、行为引擎、气泡文案。

纯逻辑模块，不依赖 tkinter，网站端以后也可以直接 import 用。
桌宠端（desktop_pet.py）每帧调 brain.tick(dt)，按 brain.current 渲染；
互动事件（戳、双击、拖拽、哄）调对应的 on_* 方法拿反应。

性格是生成时掷好的：五维特征（0~1）+ 两个怪癖 + 名字 + 最爱吃的，
存在 pet_profile.json 里，跟着精灵一起走。
"""
from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

# ---------------------------------------------------------------- 性格
TRAIT_LABELS = {
    "sociable": "外向",
    "diligent": "勤快",
    "bold": "胆大",
    "foodie": "吃货",
    "tsundere": "傲娇",
}

NAMES = [
    "团团", "糖糖", "麻薯", "年糕", "啵啵", "饭团", "汤圆", "芝士",
    "布丁", "馒头", "豆包", "奶黄包", "小笼包", "糯米", "芋圆", "奶茶",
]

QUIRKS = [
    "怕黑", "爱收集亮晶晶的东西", "洁癖，总在擦桌子", "路痴",
    "偷偷记仇", "爱模仿主人", "梦游", "收藏袜子",
    "怕打雷", "话痨", "起床气", "看到圆形就想抱住",
]

FAVORITE_SNACKS = [
    ".jpg 小零食", "大大的 .mp4", "刚删掉的文件渣", "亮晶晶的 .png",
    "热乎乎的 .docx", "脆脆的 .zip", "甜甜的 .mp3",
]


def roll_personality(rng: random.Random | None = None) -> dict:
    """掷一个新性格。传 Random 实例可复现（测试用）。"""
    rng = rng or random.Random()
    traits = {k: round(rng.uniform(0.15, 0.95), 2) for k in TRAIT_LABELS}
    quirks = rng.sample(QUIRKS, 2)
    return {
        "name": rng.choice(NAMES),
        "traits": traits,
        "quirks": quirks,
        "favorite_snack": rng.choice(FAVORITE_SNACKS),
        "born": time.strftime("%Y-%m-%d"),
    }


def describe_personality(profile: dict) -> str:
    """一句话性格卡片（网站展示、右键菜单“性格卡”都用它）。"""
    traits = profile["traits"]
    top = sorted(traits.items(), key=lambda kv: -kv[1])[:2]
    top_text = "、".join(f"{TRAIT_LABELS[k]}（{v:.0%}）" for k, v in top)
    return (
        f"{profile['name']}：{top_text}；"
        f"怪癖是{'、'.join(profile['quirks'])}；"
        f"最爱吃{profile['favorite_snack']}。"
    )


# ---------------------------------------------------------------- 气泡文案
# 每条行为一组基础文案；傲娇值高时，部分行为会换成傲娇口吻。
LINES = {
    "idle_look": ["（左看看，右看看）", "今天的桌面也很和平～", "嗯？刚才是不是有什么动静？", "发会儿呆，充充电。"],
    "walk": ["出发散步啦！", "左看看右看看～", "巡视领地中……", "走走走，活动活动。"],
    "jump": ["蹦！再蹦一下！", "看我弹跳！", "芜湖，起飞！"],
    "sleep": ["呼噜噜……待机省电中 💤", "眼皮好沉呀……", "Zzz……别吵，让我睡会儿。"],
    "stretch": ["伸个大大的懒腰～", "啊——舒服！", "睡醒啦，满血复活！"],
    "excited": ["芜湖！太开心啦！🎉", "给你比个大大的赞！", "今天也是元气满满的一天！"],
    "grumpy": ["哼！别戳我大脑袋！😤", "本精灵需要喝杯冰美式！", "略略略～"],
    "curious_approach": ["咦？你在看什么好东西？👀", "让我凑近瞅一眼……", "发现华点！"],
    "work_busy": ["认真敲键盘中…", "今天也要努力工作！", "（假装很忙）"],
    "snack_search": ["（到处嗅嗅）有好吃的味道吗？", "零食……零食在哪里……", "肚子咕咕叫了。"],
    "shy_hide": ["（缩到角落）", "别看我……", "我躲一会儿就好。"],
    "dance": ["♪ 转个圈圈～", "跳舞跳舞！", "（摇摇摆摆）"],
    "ai_motion": ["看我的绝活！", "（表演时间）"],
    "pickup": ["发现了一个小东西！", "我来拿一下～"],
    "playing": ["自己玩球也很开心！", "接住！"],
    "eating": ["（大口大口）好吃！", "嘎吱嘎吱……真香！", "（鼓着腮帮子）嗯嗯！"],
    "overstuffed": ["（躺平）吃撑了……动不了了……", "肚子好鼓……让我躺一会儿……"],
    "repairing": ["（掏出小锤子）捣鼓捣鼓……", "让我看看哪里坏了……", "（翻工具箱）扳手呢，扳手呢……"],
    "repair_ok": ["修好啦！我厉害吧！🔧", "搞定！这点小毛病难不倒我！"],
    "repair_fail": ["呜……这个我真修不好……", "（委屈脸）我尽力了，真的修不好……"],
    "refuse_eat": ["吃不下了……肚子还鼓着呢……", "不行不行，再吃要爆炸了！"],
    "ask_meal": ["这个好大……真的要我吃吗？", "唔，这个分量有点吓人，吃还是不吃？"],
    "wake_up": ["唔……谁叫我？", "（揉眼睛）几点了？"],
    "dazed": ["（晕乎乎）转得我头都晕了……", "哎哟，轻点放我下来嘛。"],
    "cheered": ["嘿嘿，被哄开心啦！", "（蹭蹭）再哄一下嘛～"],
    "thought": ["我在想，云是什么味道的。", "如果文件有味道，.jpg 应该是草莓味。", "主人今天心情怎么样呢？", "等会儿要不要去屏幕那头看看。"],
}

TSUNDERE_LINES = {
    "excited": ["哼，也就一般开心啦……", "（别过头）才没有很开心呢。"],
    "grumpy": ["哼！我才没有生气！", "（抱臂）你自己想想错哪了。"],
    "curious_approach": ["（假装路过）我才不是特意来看的。"],
    "cheered": ["哼……看在你这么诚心的份上。"],
}

QUIRK_LINES = {
    "怕黑": ["天黑了记得开灯呀，我怕黑……"],
    "爱收集亮晶晶的东西": ["（盯着屏幕上的光标）亮晶晶的……"],
    "洁癖，总在擦桌子": ["（擦擦桌面）这里有点灰。"],
    "路痴": ["（原地转圈）我刚才要去哪来着？"],
    "偷偷记仇": ["刚才那一下，我记下了。哼。"],
    "爱模仿主人": ["（学主人点头）嗯嗯，有道理。"],
    "梦游": ["（闭着眼睛走）我没睡，我在巡逻。"],
    "收藏袜子": ["袜子……我的袜子呢？"],
    "怕打雷": ["打雷的时候我要躲到屏幕后面。"],
    "话痨": ["跟你说哦，今天我看到一个特别大的文件夹……"],
    "起床气": ["（刚睡醒）别跟我说话，先让我缓缓。"],
    "看到圆形就想抱住": ["（抱住鼠标圆点）圆圆的，好喜欢。"],
}


ASK_MB = 50
OVERSTUFF_MB = 500
FULLNESS_REFUSE = 90


def fullness_gain(size: int) -> int:
    """吃一口涨多少饱腹：小零食 +5，大餐最多 +40。"""
    return 5 + min(35, size // (10 * 1024 * 1024))


# ---------------------------------------------------------------- 行为表
# weight：基础权重；duration：持续秒数范围；mood：结束时的心情变化。
BEHAVIORS = {
    "idle_look":      {"weight": 10, "duration": (2.0, 5.0), "mood": {}},
    "walk":           {"weight": 8,  "duration": (3.0, 8.0), "mood": {"energy": -2}},
    "jump":           {"weight": 4,  "duration": (2.0, 4.0), "mood": {"energy": -3, "happy": +2}},
    "sleep":          {"weight": 3,  "duration": (20.0, 60.0), "mood": {"energy": +40}},
    "stretch":        {"weight": 2,  "duration": (2.0, 3.0), "mood": {"energy": +3}},
    "excited":        {"weight": 3,  "duration": (2.0, 4.0), "mood": {"happy": +4}},
    "grumpy":         {"weight": 2,  "duration": (3.0, 6.0), "mood": {"happy": -3}},
    "curious_approach": {"weight": 5, "duration": (3.0, 6.0), "mood": {"curiosity": +5}},
    "work_busy":      {"weight": 4,  "duration": (4.0, 9.0), "mood": {"energy": -2}},
    "snack_search":   {"weight": 3,  "duration": (3.0, 6.0), "mood": {}},
    "shy_hide":       {"weight": 2,  "duration": (3.0, 6.0), "mood": {"happy": -1}},
    "dance":          {"weight": 2,  "duration": (3.0, 5.0), "mood": {"happy": +4, "energy": -3}},
    "ai_motion":      {"weight": 4,  "duration": (3.0, 3.0), "mood": {"happy": +3}},
    "eating":         {"weight": 0,  "duration": (2.0, 3.0), "mood": {"happy": +4}},
    "overstuffed":    {"weight": 0,  "duration": (8.0, 15.0), "mood": {"energy": +10}},
    "repairing":      {"weight": 0,  "duration": (3.0, 6.0), "mood": {}},
    # 手动动作：权重 0，大脑不会自己选，只能右键菜单点
    "pickup":         {"weight": 0,  "duration": (3.0, 6.0), "mood": {}},
    "playing":        {"weight": 0,  "duration": (3.0, 6.0), "mood": {"happy": +2}},
}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


class Brain:
    """心情 + 行为调度。所有随机都走 self.rng，方便测试复现。"""

    def __init__(self, profile: dict, rng: random.Random | None = None,
                 has_ai_motion: bool = False):
        self.profile = profile
        self.rng = rng or random.Random()
        self.has_ai_motion = has_ai_motion
        self.traits = dict(profile.get("traits", {}))
        self.name = profile.get("name", "抽象小精灵")
        # 心情值
        self.energy = 80.0
        self.happy = 70.0
        self.fullness = 60.0   # 阶段一只有“找零食”行为用到；吃文件是阶段二
        self.curiosity = 50.0
        # 统计
        self.stats = {"pokes": 0, "pets": 0, "drags": 0, "wake_ups": 0,
                      "meals": 0, "repairs_ok": 0, "repairs_fail": 0}
        # 行为状态
        self.current = "idle_look"
        self.behavior_t = 0.0
        self.behavior_duration = 3.0
        self.roam = False      # 手动巡游开关：开着时多走路、少睡觉
        self._pokes_window: list[float] = []  # 最近戳击的时间戳（防暴躁）
        self.pending_meal: dict | None = None  # 回收站大件：等主人点头才吃
        self.pending_meal_at = 0.0
        self._last_sleep = False

    # ---------------------------------------------------------- 权重
    def _weight(self, bid: str) -> float:
        spec = BEHAVIORS[bid]
        w = float(spec["weight"])
        t = self.traits
        if bid in ("curious_approach", "dance", "excited"):
            w *= 0.5 + t.get("sociable", 0.5)
        if bid == "work_busy":
            w *= 0.3 + 1.4 * t.get("diligent", 0.5)
        if bid == "sleep":
            w *= 1.2 - 0.5 * t.get("diligent", 0.5)
        if bid in ("walk", "jump"):
            w *= 0.5 + t.get("bold", 0.5)
        if bid == "shy_hide":
            w *= 1.3 - t.get("bold", 0.5)
        if bid == "snack_search":
            w *= 0.3 + 1.6 * t.get("foodie", 0.5)
        if bid == "grumpy":
            w *= 0.5 + 1.2 * t.get("tsundere", 0.5)
        # 心情修正
        if self.energy < 25:
            if bid == "sleep":
                w *= 8
            elif bid in ("jump", "dance"):
                w *= 0.2
            elif bid == "walk":
                w *= 0.4
        elif self.energy > 75 and bid in ("jump", "dance"):
            w *= 1.5
        if self.happy < 35:
            if bid == "grumpy":
                w *= 2
            elif bid == "excited":
                w *= 0.3
        elif self.happy > 80 and bid in ("dance", "excited"):
            w *= 1.6
        if self.curiosity > 70 and bid == "curious_approach":
            w *= 2
        if self.roam:
            if bid == "walk":
                w *= 5
            elif bid == "sleep":
                w *= 0
        if bid == "ai_motion" and not self.has_ai_motion:
            return 0.0
        return max(0.0, w)

    def choose_behavior(self) -> str:
        ids = list(BEHAVIORS)
        weights = [self._weight(b) for b in ids]
        if sum(weights) <= 0:
            return "idle_look"
        pick = self.rng.choices(ids, weights=weights, k=1)[0]
        # 睡醒之后先伸懒腰，不直接干别的
        if self._last_sleep and pick != "sleep":
            self._last_sleep = False
            return "stretch"
        return pick

    # ---------------------------------------------------------- 主循环
    def tick(self, dt: float) -> bool:
        """推进 dt 秒；返回行为是否刚切换。"""
        self.behavior_t += dt
        # 心情自然变化
        if self.current == "sleep":
            self.energy = _clamp(self.energy + dt * 1.2)
        elif self.current in ("jump", "dance", "walk"):
            self.energy = _clamp(self.energy - dt * 0.6)
        else:
            self.energy = _clamp(self.energy - dt * 0.15)
        self.happy = _clamp(self.happy + (60 - self.happy) * dt * 0.02)
        self.fullness = _clamp(self.fullness - dt * 0.05)
        self.curiosity = _clamp(self.curiosity + self.rng.uniform(-0.4, 0.4) * dt)
        if self.behavior_t >= self.behavior_duration:
            self._apply_mood(BEHAVIORS[self.current].get("mood", {}))
            self._start(self.choose_behavior())
            return True
        return False

    def _start(self, bid: str) -> None:
        self._last_sleep = self.current == "sleep"
        self.current = bid
        self.behavior_t = 0.0
        lo, hi = BEHAVIORS[bid]["duration"]
        self.behavior_duration = self.rng.uniform(lo, hi)

    def force(self, bid: str) -> None:
        """外部强制切行为（右键菜单手动点）。"""
        if bid in BEHAVIORS:
            self._start(bid)

    def _apply_mood(self, delta: dict) -> None:
        for k, v in delta.items():
            setattr(self, k, _clamp(getattr(self, k) + v))

    # ---------------------------------------------------------- 文案
    def bubble_for(self, bid: str) -> str:
        pool = list(LINES.get(bid, LINES["idle_look"]))
        if self.traits.get("tsundere", 0) > 0.6 and bid in TSUNDERE_LINES:
            pool = pool + TSUNDERE_LINES[bid]
        if bid == "idle_look":
            for q in self.profile.get("quirks", []):
                if q in QUIRK_LINES and self.rng.random() < 0.35:
                    pool = pool + QUIRK_LINES[q]
        return self.rng.choice(pool)

    # ---------------------------------------------------------- 互动
    def on_poke(self) -> tuple[str, str]:
        """戳一下。返回（行为，气泡）。连戳会惹毛它。"""
        now = time.monotonic()
        self._pokes_window = [t for t in self._pokes_window if now - t < 4.0]
        self._pokes_window.append(now)
        self.stats["pokes"] += 1
        if self.current == "sleep":
            self.stats["wake_ups"] += 1
            self.energy = _clamp(self.energy + 5)
            self._start("grumpy")
            return "grumpy", self.rng.choice(LINES["wake_up"])
        if len(self._pokes_window) >= 3:
            self._pokes_window.clear()
            self.happy = _clamp(self.happy - 8)
            self._start("shy_hide")
            return "shy_hide", "别戳了别戳了！我要躲起来了！"
        self.happy = _clamp(self.happy + 3)
        self._start("excited")
        return "excited", self.bubble_for("excited")

    def on_double_click(self) -> str:
        """双击：说一句心里话。"""
        self.stats["pets"] += 1
        return self.rng.choice(LINES["thought"])

    def on_drag_end(self) -> tuple[str, str]:
        self.stats["drags"] += 1
        self._start("idle_look")
        return "idle_look", self.rng.choice(LINES["dazed"])

    def on_cheer(self) -> str:
        """哄它一下。"""
        self.happy = _clamp(self.happy + 20)
        self._start("excited")
        return self.bubble_for("cheered")

    def on_wake_toggle(self) -> tuple[str, str]:
        if self.current == "sleep":
            self.force("stretch")
            return "stretch", self.bubble_for("stretch")
        self.force("sleep")
        return "sleep", self.bubble_for("sleep")

    # ---------------------------------------------------------- 阶段二：吃文件 / 修文件
    def on_eat(self, size_bytes: int, kind: str = "other", name: str = "") -> tuple[str, str]:
        """吃掉一个文件。返回（行为，气泡）。"""
        if self.fullness >= FULLNESS_REFUSE:
            self._start("grumpy")
            return "grumpy", self.rng.choice(LINES["refuse_eat"])
        self.stats["meals"] += 1
        label = name or {"image": "一张图", "zip": "一个压缩包", "text": "一篇文章"}.get(kind, "一个文件")
        if size_bytes > OVERSTUFF_MB * 1024 * 1024:
            self.fullness = 100.0
            self._start("overstuffed")
            return "overstuffed", self.rng.choice(LINES["overstuffed"])
        gain = fullness_gain(size_bytes)
        self.fullness = _clamp(self.fullness + gain)
        self._start("eating")
        return "eating", f"（大口大口）{label}真香！"

    def set_pending_meal(self, item: dict) -> str:
        """回收站里来了个大件：先问主人，点了才吃。"""
        self.pending_meal = dict(item)
        self.pending_meal_at = time.monotonic()
        return self.rng.choice(LINES["ask_meal"])

    def confirm_pending_meal(self) -> tuple[str, str] | None:
        if not self.pending_meal:
            return None
        item = self.pending_meal
        self.pending_meal = None
        return self.on_eat(item["size"], item.get("kind", "other"), item.get("name", ""))

    def cancel_pending_meal(self) -> str:
        self.pending_meal = None
        return "好吧，不吃了。"

    def on_repair_start(self) -> str:
        self._start("repairing")
        return self.rng.choice(LINES["repairing"])

    def on_repair(self, result: dict) -> tuple[str, str]:
        if result.get("ok"):
            self.stats["repairs_ok"] += 1
            self._start("excited")
            if result.get("already_ok"):
                return "excited", "检查过了，这个本来就是好的，不用修～"
            out = result.get("out") or ""
            name = Path(out).name if out else ""
            return "excited", f"修好啦！存成{name}啦！"
        self.stats["repairs_fail"] += 1
        self._start("grumpy")
        return "grumpy", self.rng.choice(LINES["repair_fail"])

    def status_line(self) -> str:
        return (f"饱腹 {self.fullness:.0f}% · 吃过 {self.stats['meals']} 个文件 · "
                f"修好 {self.stats['repairs_ok']} 个")


    # ---------------------------------------------------------- 持久化
    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "moods": {
                "energy": round(self.energy, 1),
                "happy": round(self.happy, 1),
                "fullness": round(self.fullness, 1),
                "curiosity": round(self.curiosity, 1),
            },
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, data: dict, rng=None, has_ai_motion: bool = False) -> "Brain":
        brain = cls(data["profile"], rng=rng, has_ai_motion=has_ai_motion)
        for k, v in data.get("moods", {}).items():
            if hasattr(brain, k):
                setattr(brain, k, float(v))
        brain.stats.update(data.get("stats", {}))
        return brain

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, rng=None, has_ai_motion: bool = False) -> "Brain | None":
        p = Path(path)
        if not p.exists():
            return None
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")),
                                 rng=rng, has_ai_motion=has_ai_motion)
        except Exception:
            return None


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="掷一个精灵性格 / 看性格卡")
    ap.add_argument("--new", action="store_true", help="掷一个新性格并打印")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--profile", type=str, default="", help="读指定档案并打印性格卡")
    args = ap.parse_args()
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    if args.profile:
        b = Brain.load(args.profile, rng=rng)
        if b is None:
            print("档案不存在或损坏。")
            raise SystemExit(1)
        print(describe_personality(b.profile))
    else:
        prof = roll_personality(rng)
        print(describe_personality(prof))
        print(json.dumps(prof, ensure_ascii=False, indent=2))
