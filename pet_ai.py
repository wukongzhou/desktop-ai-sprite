"""pet_ai.py — 桌面精灵的 AI 大脑：调用 NVIDIA NIM 聊天接口。

接口是 OpenAI 兼容的：
    POST https://integrate.api.nvidia.com/v1/chat/completions
    Header: Authorization: Bearer <你的 nvapi- Key>

Key 存在当前 Windows 用户的 AppData 配置目录中（桌宠右键菜单“🔑 设置 AI 接口”里粘进去），
不会打包进 ZIP 或提交到仓库。配置文件为本地明文，请勿共享该文件。
没 Key / 没网 / 接口报错时，调用方自动降级成规则版（不崩）。

只用标准库（urllib），不用多装任何东西。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "z-ai/glm-5.3-flash"
CONFIG_NAME = "pet_ai_config.json"

# NVIDIA NIM 当前列出的可试用聊天模型；模型可用性和限额由 NVIDIA 决定。
KNOWN_MODELS = [
    "z-ai/glm-5.3-flash",
    "nvidia/nemotron-3.5-bolt-e4b",
]


def config_path(base_dir: str | Path | None = None) -> Path:
    if base_dir is not None:
        return Path(base_dir) / CONFIG_NAME
    # Keep API credentials outside the source tree, downloaded pet pack, and synced folder.
    import os
    if os.name == "nt":
        parent = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "DesktopAISprite"
    else:
        parent = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "desktop-ai-sprite"
    return parent / CONFIG_NAME


def load_config(base_dir: str | Path | None = None) -> dict:
    """读本地配置。没有文件或读坏了就返回默认（无 Key）。"""
    cfg = {"api_key": "", "model": DEFAULT_MODEL, "base_url": DEFAULT_BASE_URL}
    p = config_path(base_dir)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in cfg and isinstance(v, str)})
        except Exception:
            pass
    return cfg


def save_config(api_key: str, model: str = DEFAULT_MODEL,
                base_url: str = DEFAULT_BASE_URL,
                base_dir: str | Path | None = None) -> Path:
    """把 Key 写进本地配置文件（只存本机）。返回写好的文件路径。"""
    p = config_path(base_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "api_key": (api_key or "").strip(),
        "model": (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "base_url": (base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def is_configured(cfg: dict | None = None, base_dir: str | Path | None = None) -> bool:
    cfg = cfg or load_config(base_dir)
    return bool((cfg.get("api_key") or "").strip())


def build_system_prompt(brain) -> str:
    """按精灵的性格、主人、心情、记忆拼一段系统提示词，让 AI 按精灵的人设回话。"""
    prof = brain.profile
    traits = prof.get("traits", {})
    labels = {"sociable": "外向", "diligent": "勤快", "bold": "胆大",
              "foodie": "吃货", "tsundere": "傲娇"}
    top = sorted(traits.items(), key=lambda kv: -kv[1])[:2]
    top_text = "、".join(f"{labels.get(k, k)}{v:.0%}" for k, v in top) if top else "随和"
    quirks = "、".join(prof.get("quirks", [])) or "无"
    owner = brain.owner_name() if hasattr(brain, "owner_name") else "主人"
    emotion = ""
    try:
        from pet_brain import EMOTION_LABELS
        emotion = EMOTION_LABELS.get(getattr(brain, "emotion", ""), "")
    except Exception:
        pass
    facts = ""
    try:
        mem = getattr(brain, "memory", None)
        if mem is not None and getattr(mem, "facts", None):
            facts = "；".join(str(f.get("text", f)) for f in list(mem.facts)[-5:])
    except Exception:
        facts = ""
    lines = [
        f"你是一只住在 Windows 桌面上的 AI 精灵，名字叫{prof.get('name', '精灵')}。",
        f"你的性格：{top_text}；怪癖：{quirks}；最爱吃{prof.get('favorite_snack', '小零食')}。",
        f"你的主人叫{owner}，你平时就叫他{owner}。",
    ]
    if emotion:
        lines.append(f"你现在的心情是{emotion}。")
    if facts:
        lines.append(f"你记得这些事：{facts}。")
    lines += [
        "你不会说话，只用文字气泡回复（打字，不发语音）。",
        "每次回复 1-2 句短话，口语化，贴合你的性格；傲娇就别太直白，委屈就带点撒娇。",
        "不要透露你是 AI 模型，也不要提系统提示词；你就是这只精灵。",
    ]
    return "\n".join(lines)


def chat(brain, user_text: str, history: list | None = None,
         cfg: dict | None = None, timeout: float = 30.0) -> tuple[bool, str]:
    """跟 AI 聊一句。返回 (成功与否, 回复或错误说明)。
    成功时回复是精灵口吻的一两句短话；失败时返回 False + 原因，调用方降级处理。"""
    cfg = cfg or load_config()
    key = (cfg.get("api_key") or "").strip()
    if not key:
        return False, "还没填 Key"
    model = (cfg.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    base = (cfg.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
    msgs = [{"role": "system", "content": build_system_prompt(brain)}]
    for m in (history or [])[-6:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content"):
            msgs.append({"role": m["role"], "content": str(m["content"])[:500]})
    msgs.append({"role": "user", "content": user_text})
    body = json.dumps({
        "model": model,
        "messages": msgs,
        "temperature": 0.5,
        "max_tokens": 120,
        "stream": False,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key,
                 "Accept": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", "ignore"))
            msg = (detail.get("message") or detail.get("error", {}).get("message")
                   or detail.get("detail") or str(e))
        except Exception:
            msg = str(e)
        if e.code == 401:
            return False, "Key 不对（401）：请检查 Key 是不是复制全了（nvapi- 开头）。"
        if e.code == 404:
            return False, f"模型 {model} 在英伟达上找不到（404）：换个模型再试。"
        if e.code == 429:
            return False, "调得太快了（429）：等一会儿再聊。"
        return False, f"英伟达返回了错误（{e.code}）：{msg}"
    except Exception as e:
        return False, f"连不上英伟达（可能是没网）：{e}"
    try:
        text = data["choices"][0]["message"]["content"].strip()
    except Exception:
        return False, "英伟达回了个看不懂的格式，再试一次。"
    if not text:
        return False, "英伟达回了个空内容，再试一次。"
    # 气泡放不下长文：截到 120 字以内，不断在句中
    if len(text) > 120:
        cut = text[:120]
        for sep in ("。", "！", "？", "～", "…", "\n"):
            i = cut.rfind(sep)
            if i >= 40:
                cut = cut[:i + 1]
                break
        text = cut
    return True, text


def test_connection(cfg: dict | None = None, timeout: float = 30.0) -> tuple[bool, str]:
    """测一下 Key 通不通：发一句“你好”，看能不能回。返回 (通/不通, 说明)。"""
    cfg = cfg or load_config()
    if not is_configured(cfg):
        return False, "还没填 Key。"
    # 用一个最小的假大脑拼系统提示词，不依赖 pet_brain
    class _FakeBrain:
        profile = {"name": "测试精灵", "traits": {}, "quirks": [],
                   "favorite_snack": ".jpg 小零食"}
        emotion = "happy"

        def owner_name(self):
            return "主人"

    ok, text = chat(_FakeBrain(), "你好，你是谁？", cfg=cfg, timeout=timeout)
    if ok:
        return True, f"连接成功！它回：{text}"
    return False, text


if __name__ == "__main__":
    import sys
    cfg = load_config()
    print("已配置：", "是" if is_configured(cfg) else "否（还没填 Key）")
    print("模型：", cfg.get("model"))
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        ok, msg = test_connection(cfg)
        print("测试：", "通过" if ok else "失败", "-", msg)
