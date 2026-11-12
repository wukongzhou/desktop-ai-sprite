"""pet_memory.py — 桌宠的记事本（学习功能）：教它、记事、习惯推导。

纯逻辑，不依赖 tkinter。记忆存在 pet_memory.json 里，跟着精灵走。
- 教它：主人一句话教它（“我喜欢喝咖啡”“我叫亮哥”），规则解析成一条记忆。
- 记事：它自己观察到的事（喂了什么、修了什么、几点找它玩）攒成习惯。
- 回忆：随机翻一条记忆出来说；“它记得什么”里列出全部。
教它的解析是本地规则（固定句式），不是大模型：听得懂的句式就结构化，
听不懂的原样记下来。真要自由对话理解，得接本地小模型（阶段四）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

KIND_LABELS = {"image": "图片", "zip": "压缩包", "text": "文章", "other": "文件"}
INTERACTION_KINDS = {"poke", "pet", "play", "cheer", "teach", "ask", "fed", "repair"}


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _clean_tail(s: str) -> str:
    return s.strip().strip("。！!？?，,、 ")


class Memory:
    """主人的事 + 主人的习惯。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.owner_name: str | None = None
        self.facts: list[dict] = []  # {"text", "kind", "count", "first", "last"}
        self.events: dict = {
            "fed_ext": {}, "fed_kind": {}, "repair_kind": {},
            "active_hour": {}, "pats": 0, "plays": 0, "pokes": 0,
        }

    # ---------------------------------------------------------- 持久化
    def to_dict(self) -> dict:
        return {"owner_name": self.owner_name, "facts": self.facts, "events": self.events}

    @classmethod
    def from_dict(cls, data: dict, path: str | Path | None = None) -> "Memory":
        mem = cls(path)
        mem.owner_name = data.get("owner_name")
        mem.facts = list(data.get("facts", []))
        ev = data.get("events", {})
        mem.events.update(ev)
        return mem

    def save(self, path: str | Path | None = None) -> None:
        p = Path(path) if path else self.path
        if p is None:
            return
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path | None) -> "Memory":
        if not path:
            return cls(None)
        p = Path(path)
        if not p.exists():
            return cls(p)
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")), p)
        except Exception:
            return cls(p)

    # ---------------------------------------------------------- 教它
    def _add_fact(self, text: str, kind: str) -> dict:
        today = _today()
        for f in self.facts:
            if f["text"] == text:
                f["count"] = f.get("count", 1) + 1
                f["last"] = today
                return f
        entry = {"text": text, "kind": kind, "count": 1, "first": today, "last": today}
        self.facts.append(entry)
        return entry

    def teach(self, text: str) -> tuple[dict | None, str]:
        """教它一句话。返回（记忆条目，回复气泡）。"""
        text = (text or "").strip()
        if not text:
            return None, "你刚说什么？我没听清～"
        m = re.match(r"(?:我叫|我的名字是|我是)(.{1,8})$", text)
        if m and "是" not in m.group(1) and "的" not in m.group(1):
            name = _clean_tail(m.group(1))
            if name:
                self.owner_name = name
                self.facts = [f for f in self.facts if f.get("kind") != "name"]
                entry = self._add_fact(f"主人叫{name}", "name")
                return entry, f"记住了，{name}！以后我就这么叫你～"
        m = re.match(r"我(?:喜欢|爱)(.+)$", text)
        if m:
            thing = _clean_tail(m.group(1))
            if thing:
                entry = self._add_fact(f"主人喜欢{thing}", "like")
                if entry["count"] > 1:
                    return entry, f"这个我早就记着啦，主人喜欢{thing}！"
                return entry, f"记住了，主人喜欢{thing}！"
        m = re.match(r"我(?:不喜欢|讨厌)(.+)$", text)
        if m:
            thing = _clean_tail(m.group(1))
            if thing:
                entry = self._add_fact(f"主人不喜欢{thing}", "dislike")
                return entry, f"记住了，主人不喜欢{thing}，我以后不提它。"
        m = re.match(r"我每天(\d{1,2})点(.+)$", text)
        if m:
            hour, rest = m.group(1), _clean_tail(m.group(2))
            if rest:
                entry = self._add_fact(f"主人每天{hour}点{rest}", "routine")
                return entry, f"记住了，每天{hour}点{rest}！"
        entry = self._add_fact(f"主人说：{text}", "note")
        if entry["count"] > 1:
            return entry, "这句我早就记着啦！"
        return entry, "记下啦！主人说的我都记着呢。"

    # ---------------------------------------------------------- 观察
    def note_event(self, kind: str, data: dict | None = None) -> None:
        """记一桩观察到的事（喂了什么、修了什么、几点找它）。"""
        data = data or {}
        ev = self.events
        if kind in INTERACTION_KINDS:
            hour = str(time.localtime().tm_hour)
            ev["active_hour"][hour] = ev["active_hour"].get(hour, 0) + 1
        if kind == "fed":
            ext = (data.get("ext") or "").lower() or "?"
            ev["fed_ext"][ext] = ev["fed_ext"].get(ext, 0) + 1
            fk = data.get("file_kind") or "other"
            ev["fed_kind"][fk] = ev["fed_kind"].get(fk, 0) + 1
        elif kind == "repair":
            fk = data.get("file_kind") or "other"
            ev["repair_kind"][fk] = ev["repair_kind"].get(fk, 0) + 1
        elif kind == "pat":
            ev["pats"] += 1
        elif kind == "play":
            ev["plays"] += 1
        elif kind == "poke":
            ev["pokes"] += 1

    # ---------------------------------------------------------- 回忆
    def habits(self) -> list[str]:
        """从观察里推出主人的习惯。"""
        out: list[str] = []
        ev = self.events
        fed_ext = sorted(ev["fed_ext"].items(), key=lambda kv: -kv[1])
        fed_kind = sorted(ev["fed_kind"].items(), key=lambda kv: -kv[1])
        if fed_ext and fed_ext[0][1] >= 3:
            out.append(f"主人最常喂我 {fed_ext[0][0]} 文件（{fed_ext[0][1]} 次）")
        elif fed_kind and fed_kind[0][1] >= 2:
            out.append(f"主人最常让我吃 {KIND_LABELS.get(fed_kind[0][0], '文件')}（{fed_kind[0][1]} 次）")
        rep = sorted(ev["repair_kind"].items(), key=lambda kv: -kv[1])
        if rep and rep[0][1] >= 1:
            out.append(f"主人最常让我修 {KIND_LABELS.get(rep[0][0], '文件')}（{rep[0][1]} 次）")
        hours = sorted(ev["active_hour"].items(), key=lambda kv: -kv[1])
        if hours and hours[0][1] >= 3:
            out.append(f"主人总在 {hours[0][0]} 点左右找我（{hours[0][1]} 次）")
        if ev["pats"] >= 5:
            out.append(f"主人最爱摸我的头（{ev['pats']} 次）")
        if ev["plays"] >= 3:
            out.append(f"主人总陪我玩（{ev['plays']} 次）")
        return out

    def recall(self, rng) -> str | None:
        """随机翻一条记忆出来说。"""
        pool = [f["text"] for f in self.facts] + self.habits()
        if not pool:
            return None
        return rng.choice(pool)

    def summary(self) -> str:
        """“它记得什么”的全文。没记下任何事返回空串。"""
        lines: list[str] = []
        if self.owner_name:
            lines.append(f"主人：{self.owner_name}")
        if self.facts:
            lines.append("记得的事：")
            for f in self.facts:
                n = f.get("count", 1)
                lines.append(f"· {f['text']}" + (f"（记了 {n} 次）" if n > 1 else ""))
        hb = self.habits()
        if hb:
            lines.append("主人的习惯：")
            for h in hb:
                lines.append(f"· {h}")
        return "\n".join(lines)
