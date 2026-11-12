from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import re
import hashlib
import hmac
import html
import sqlite3
import time
import shutil
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from string import Template
from urllib.parse import urlparse
import os
import subprocess
import sys
import cloudflare_ai
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import set_key

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))
import comfy_chibi

ROOT = Path(__file__).parent
ASSETS_DIR = ROOT / "assets"
SPRITES_DIR = ROOT / "saved_sprites"
SPRITES_DIR.mkdir(exist_ok=True)

MODEL_ID = os.getenv("CARTOON_MODEL", "stabilityai/sd-turbo")
ENGINE_MODE = os.getenv("CARTOON_ENGINE", "auto")  # "auto", "procedural", "diffusers"
AI_PROVIDER = os.getenv("AI_PROVIDER", "cloudflare").strip().lower()

try:
    import torch
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32
except Exception:
    torch = None
    DEVICE = "cpu"
    DTYPE = None

app = FastAPI(title="Abstract Desktop Sprite Studio (抽象桌面精灵工坊)")
PUBLIC_HOSTED_MODE = os.getenv("PUBLIC_HOSTED_MODE", "0").strip().lower() in {"1", "true", "yes"}

def access_code_required() -> bool:
    """口令关卡：只有设置了 APP_ACCESS_CODE 才启用；不设口令 = 公开使用。"""
    return PUBLIC_HOSTED_MODE and bool(os.getenv("APP_ACCESS_CODE", "").strip())


@app.middleware("http")
async def public_access_gate(request: Request, call_next):
    if access_code_required() and request.url.path.startswith("/api/") and request.url.path != "/api/access/verify":
        expected = os.getenv("APP_ACCESS_CODE", "").strip()
        supplied = request.cookies.get("pet_access_code", "")
        if not hmac.compare_digest(supplied, expected):
            return JSONResponse(status_code=401, content={"detail": "请输入测试访问口令。"})
    return await call_next(request)

@app.post("/api/access/verify")
async def verify_public_access(request: Request):
    if not access_code_required():
        return {"required": False}
    expected = os.getenv("APP_ACCESS_CODE", "").strip()
    try:
        body = await request.json()
        supplied = str(body.get("code", ""))
    except Exception:
        supplied = ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(403, "访问口令不正确。")
    response = JSONResponse({"ok": True})
    response.set_cookie("pet_access_code", expected, httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=1209600)
    return response

# ---------------------------------------------------------------------------
# 使用统计 + 留言（SQLite，存在 data/stats.db；Render 免费版重启/重新部署会清空）
# ---------------------------------------------------------------------------
STATS_DIR = ROOT / "data"
STATS_DB = STATS_DIR / "stats.db"
EVENT_KINDS = {"page_view", "generate", "share", "download"}
STATS_TEMPLATE = Template('<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">\n<title>桌面精灵 · 数据后台</title>\n<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:0 auto;padding:24px 16px;color:#2b2522;background:#faf6f0}h1{font-size:20px}h2{font-size:16px;margin-top:28px}.cards{display:flex;gap:12px;flex-wrap:wrap}.card{background:#fff;border:1px solid #eadfd2;border-radius:16px;padding:14px 18px;min-width:120px}.card b{font-size:26px;display:block}table{border-collapse:collapse;width:100%;background:#fff}td,th{border:1px solid #eadfd2;padding:6px 8px;font-size:13px;text-align:center}ul{list-style:none;padding:0}li{background:#fff;border:1px solid #eadfd2;border-radius:12px;padding:10px 12px;margin-bottom:8px}li span{color:#8b7d72;font-size:12px}li p{margin:6px 0 0;font-size:14px;white-space:pre-wrap;word-break:break-word}a{color:#d8653c}</style></head>\n<body><h1>📊 桌面精灵 · 数据后台</h1><p><a href="/">← 回精灵工坊</a></p>\n<div class="cards"><div class="card">访问量<b>$views</b></div><div class="card">生成次数<b>$generates</b></div><div class="card">分享次数<b>$shares</b></div><div class="card">下载次数<b>$downloads</b></div><div class="card">留言条数<b>$comments_n</b></div></div>\n<h2>最近 7 天（UTC）</h2><table><tr><th>日期</th><th>访问</th><th>生成</th><th>分享</th><th>下载</th></tr>$rows</table>\n<h2>留言（最新 $n 条）</h2><ul>$body</ul></body></html>')


def _stats_conn():
    STATS_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(STATS_DB))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " kind TEXT NOT NULL, ts INTEGER NOT NULL, ip TEXT NOT NULL DEFAULT '')"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, ts INTEGER NOT NULL,"
        " ip TEXT NOT NULL DEFAULT '')"
    )
    conn.commit()
    return conn


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def record_event(kind: str, ip: str = "") -> None:
    try:
        conn = _stats_conn()
        conn.execute(
            "INSERT INTO events (kind, ts, ip) VALUES (?, ?, ?)",
            (kind, int(time.time()), ip),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _summary_data() -> dict:
    totals = {kind: 0 for kind in ("page_view", "generate", "share", "download")}
    daily = []
    comments = 0
    try:
        conn = _stats_conn()
        for kind, count in conn.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind"):
            if kind in totals:
                totals[kind] = int(count)
        today = datetime.now(timezone.utc).date()
        for back in range(7):
            day = today - timedelta(days=back)
            start = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
            row = dict(conn.execute(
                "SELECT kind, COUNT(*) FROM events WHERE ts >= ? AND ts < ? GROUP BY kind",
                (start, start + 86400),
            ).fetchall())
            daily.append({"date": day.isoformat(), **{k: 0 for k in totals},
                          **{k: int(v) for k, v in row.items() if k in totals}})
        comments = int(conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0])
        conn.close()
    except Exception:
        pass
    return {"totals": totals, "daily": daily, "comments": comments}


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8765", "http://localhost:8765"],
    allow_methods=["*"],
    allow_headers=["*"],
)

pipe = None
pet_process: Optional[subprocess.Popen] = None

STYLE_PROMPTS = {
    "big": (
        "funny exaggerated editorial caricature desktop sprite, oversized head, chibi proportions, "
        "joyful smile, expressive giant eyes, hand painted comic illustration, vivid colors, "
        "clean sticker silhouette, keep the same person's recognizable facial identity"
    ),
    "weird": (
        "surreal humorous comic caricature sprite, wonderfully odd surprised expression, "
        "expressive enlarged features, quirky asymmetry, hand drawn illustration, bold shapes, "
        "preserve recognizable identity"
    ),
    "geo": (
        "abstract geometric pop art caricature sprite, cubist playful colorful shapes, "
        "simplified iconic face, bold flat color blocks, humorous editorial illustration, "
        "preserve recognizable facial identity"
    ),
    "grumpy": (
        "witty satirical newspaper caricature sprite, expressive grumpy face, "
        "exaggerated brows and nose, bold ink outlines, painted comic sticker style, "
        "preserve recognizable identity"
    ),
    "pop": (
        "neo-pop art sticker sprite, high contrast halftone comic aesthetics, "
        "amplified iconic accessories and eyes, vibrant neon accent colors, clean outline"
    ),
    "pixel": (
        "expressive 16-bit retro pixel art desktop pet sprite, oversized head and eyes, "
        "crisp pixel clusters, vibrant palette, recognizable character traits"
    ),
}


@app.on_event("startup")
def load_model() -> None:
    global pipe
    app.state.model_error = None
    if AI_PROVIDER == "cloudflare":
        app.state.model_error = "Cloudflare Workers AI active; local model loading disabled."
        return
    if ENGINE_MODE == "procedural":
        app.state.model_error = "Configured for instant procedural abstract sprite engine (CARTOON_ENGINE=procedural)."
        return
    try:
        from diffusers import AutoPipelineForImage2Image
        if torch is None:
            raise RuntimeError("PyTorch is not installed.")
        pipe = AutoPipelineForImage2Image.from_pretrained(
            MODEL_ID, torch_dtype=DTYPE, safety_checker=None
        )
        pipe.to(DEVICE)
    except Exception as e:
        app.state.model_error = f"Diffusers model ({MODEL_ID}) not loaded: {e}. Procedural Sprite Engine is active."


def extract_foreground_mask(img: Image.Image, tolerance: float = 0.22) -> Image.Image:
    """Extract subject foreground mask by corner background sampling + edge radial weighting."""
    rgba = img.convert("RGBA")
    arr = np.asarray(rgba, dtype=np.float32)
    h, w, _ = arr.shape
    rgb = arr[:, :, :3]

    # Sample border pixels mostly from corners and top/side edges
    border_samples = np.concatenate([
        rgb[0:max(4, h // 12), :, :].reshape(-1, 3),
        rgb[:, 0:max(4, w // 12), :].reshape(-1, 3),
        rgb[:, max(0, w - max(4, w // 12)):w, :].reshape(-1, 3),
    ], axis=0)
    bg_color = np.median(border_samples, axis=0)

    diff = np.linalg.norm(rgb - bg_color[None, None, :], axis=2) / 441.67
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w * 0.5, h * 0.54
    radial = np.sqrt(((xx - cx) / (w * 0.48)) ** 2 + ((yy - cy) / (h * 0.48)) ** 2)

    # Combine color distance from background with central subject prior
    score = diff + np.clip(1.0 - radial * 0.85, -0.2, 0.35) * 0.32
    alpha = np.clip((score - tolerance) / max(0.08, tolerance * 0.55), 0.0, 1.0)
    # Keep center core opaque
    core = radial < 0.58
    alpha = np.where(core, np.maximum(alpha, 0.96), alpha)
    alpha_img = Image.fromarray((alpha * 255).astype(np.uint8), mode="L")
    alpha_img = alpha_img.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.GaussianBlur(1.8))
    return alpha_img


def nonlinear_feature_magnify(
    img: Image.Image,
    head_scale: float = 1.38,
    eye_scale: float = 1.45,
    quirk: float = 0.25,
    focus_x: float = 0.5,
    focus_y: float = 0.42,
    left_eye: tuple[float, float] = (0.37, 0.40),
    right_eye: tuple[float, float] = (0.63, 0.40),
    mouth_pos: tuple[float, float] = (0.50, 0.62),
) -> Image.Image:
    """Apply multi-lobe radial & swirl warps to amplify head, eyes, and facial expression."""
    rgba = img.convert("RGBA")
    arr = np.asarray(rgba, dtype=np.float32)
    h, w, c = arr.shape

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx = xx / w
    ny = yy / h

    # 1. Global Chibi Head Expansion & Body Taper
    dx = nx - focus_x
    dy = ny - focus_y
    r = np.sqrt(dx * dx + dy * dy) + 1e-6
    head_radius = 0.44
    head_strength = max(0.0, (head_scale - 1.0) * 0.62)
    mask_head = np.clip(1.0 - r / head_radius, 0.0, 1.0) ** 1.6
    factor_head = 1.0 - head_strength * mask_head
    src_x = focus_x + dx * factor_head
    src_y = focus_y + dy * factor_head

    # Taper lower torso into cute compact sprite body
    lower_mask = np.clip((ny - 0.62) / 0.38, 0.0, 1.0)
    src_x = 0.5 + (src_x - 0.5) / (1.0 - lower_mask * 0.32)

    # 2. Left & Right Eye Focal Bulge (Amplify characteristic eyes/glasses)
    eye_str = max(0.0, (eye_scale - 1.0) * 0.58)
    eye_rad = 0.16
    for ex, ey in (left_eye, right_eye):
        edx = src_x - ex
        edy = src_y - ey
        er = np.sqrt(edx * edx + edy * edy) + 1e-6
        emask = np.clip(1.0 - er / eye_rad, 0.0, 1.0) ** 1.8
        efac = 1.0 - eye_str * emask
        src_x = ex + edx * efac
        src_y = ey + edy * efac

    # 3. Mouth / Expression Warp + Quirky Asymmetry
    mdx = src_x - mouth_pos[0]
    mdy = src_y - mouth_pos[1]
    mr = np.sqrt(mdx * mdx + mdy * mdy) + 1e-6
    mmask = np.clip(1.0 - mr / 0.20, 0.0, 1.0) ** 1.5
    src_x = mouth_pos[0] + mdx * (1.0 - quirk * 0.35 * mmask)
    src_y = mouth_pos[1] + mdy * (1.0 - quirk * 0.25 * mmask) - (quirk * 0.035 * mmask * (1.0 - abs(mdx) * 4.0))

    # Bilinear sample
    px = np.clip(src_x * (w - 1), 0, w - 1.001)
    py = np.clip(src_y * (h - 1), 0, h - 1.001)
    x0 = np.floor(px).astype(np.int32)
    y0 = np.floor(py).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    wx = (px - x0)[:, :, None]
    wy = (py - y0)[:, :, None]

    out = (
        arr[y0, x0] * (1 - wx) * (1 - wy)
        + arr[y0, x1] * wx * (1 - wy)
        + arr[y1, x0] * (1 - wx) * wy
        + arr[y1, x1] * wx * wy
    )
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), mode="RGBA")


def stylize_abstract_sprite(
    source: Image.Image,
    style: str = "big",
    strength: float = 0.65,
    head_scale: float = 1.42,
    eye_scale: float = 1.48,
    outline_px: int = 8,
    remove_bg: bool = True,
) -> Image.Image:
    """Procedural abstract feature-magnifying sprite generator."""
    size = 512
    im = ImageOps.fit(source.convert("RGB"), (size, size), method=Image.Resampling.LANCZOS)

    # Style-specific exaggeration presets
    quirk = 0.18
    if style == "big":
        head_scale = max(head_scale, 1.0 + strength * 0.68)
        eye_scale = max(eye_scale, 1.0 + strength * 0.62)
        quirk = 0.22 * strength
    elif style == "weird":
        head_scale = max(head_scale, 1.0 + strength * 0.55)
        eye_scale = max(eye_scale, 1.0 + strength * 0.85)
        quirk = 0.55 * strength
    elif style == "geo":
        head_scale = max(head_scale, 1.0 + strength * 0.50)
        eye_scale = max(eye_scale, 1.0 + strength * 0.52)
        quirk = 0.30 * strength
    elif style == "grumpy":
        head_scale = max(head_scale, 1.0 + strength * 0.58)
        eye_scale = max(eye_scale, 1.0 + strength * 0.45)
        quirk = -0.42 * strength
    elif style == "pop":
        head_scale = max(head_scale, 1.0 + strength * 0.60)
        eye_scale = max(eye_scale, 1.0 + strength * 0.70)
        quirk = 0.28 * strength
    elif style == "pixel":
        head_scale = max(head_scale, 1.0 + strength * 0.62)
        eye_scale = max(eye_scale, 1.0 + strength * 0.65)
        quirk = 0.20 * strength

    # Step 1: Posterize & vibrant cartoon color abstraction
    smoothed = im.filter(ImageFilter.MedianFilter(5))
    sat_boost = 1.45 if style in ("big", "pop", "geo") else 1.30
    con_boost = 1.28 if style in ("geo", "grumpy", "pop") else 1.18
    vibrant = ImageEnhance.Color(smoothed).enhance(sat_boost)
    vibrant = ImageEnhance.Contrast(vibrant).enhance(con_boost)

    bits = 3 if style in ("geo", "pop") else 4
    poster = ImageOps.posterize(vibrant, bits=bits)

    # Step 2: Bold comic ink edge extraction
    gray = im.convert("L").filter(ImageFilter.GaussianBlur(1.1))
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edges = ImageEnhance.Contrast(edges).enhance(3.2)
    edge_arr = np.asarray(edges, dtype=np.float32)
    ink_mask = np.clip((edge_arr - 42.0) / 85.0, 0.0, 1.0) * (0.65 + 0.30 * strength)

    poster_arr = np.asarray(poster, dtype=np.float32)
    ink_color = np.array([32.0, 24.0, 28.0], dtype=np.float32)
    blended_rgb = poster_arr * (1.0 - ink_mask[:, :, None]) + ink_color[None, None, :] * ink_mask[:, :, None]
    stylized = Image.fromarray(np.clip(blended_rgb, 0, 255).astype(np.uint8), mode="RGB")

    if style == "pixel":
        small = stylized.resize((96, 96), resample=Image.Resampling.BILINEAR)
        stylized = small.resize((size, size), resample=Image.Resampling.NEAREST)

    # Step 3: Extract foreground mask if requested
    rgba = stylized.convert("RGBA")
    if remove_bg:
        mask = extract_foreground_mask(im, tolerance=0.20)
        rgba.putalpha(mask)

    # Step 4: Non-linear feature magnification (Head + Eyes + Expression)
    warped = nonlinear_feature_magnify(
        rgba,
        head_scale=head_scale,
        eye_scale=eye_scale,
        quirk=quirk,
    )

    # Step 5: Add crisp die-cut sticker outline & drop shadow for desktop sprite feel
    if remove_bg and outline_px > 0:
        alpha = warped.getchannel("A")
        dilated = alpha
        for _ in range(max(1, outline_px // 2)):
            dilated = dilated.filter(ImageFilter.MaxFilter(5))
        sticker_bg = Image.new("RGBA", (size, size), (255, 252, 245, 0))
        sticker_bg.putalpha(dilated)
        canvas = Image.alpha_composite( Image.new("RGBA", (size, size), (0, 0, 0, 0)), sticker_bg )
        canvas = Image.alpha_composite(canvas, warped)
        return canvas

    return warped


class CloudflareSettings(BaseModel):
    account_id: str = Field(default="", max_length=80)
    api_token: str = Field(default="", max_length=500)
    vision_model: str = Field(default="@cf/moondream/moondream3.1-9B-A2B", max_length=180)
    image_model: str = Field(default="@cf/black-forest-labs/flux-2-klein-4b", max_length=180)
    video_model: str = Field(default="pixverse/v6", max_length=180)


@app.get("/api/settings/ai")
def get_ai_settings():
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    return {
        "provider": "cloudflare-workers-ai",
        "configured": bool(account and token),
        "account_id": account,
        "vision_model": cloudflare_ai.VISION_MODEL,
        "image_model": cloudflare_ai.IMAGE_MODEL,
        "video_model": os.getenv("CLOUDFLARE_VIDEO_MODEL", "pixverse/v6"),
        "api_token_saved": bool(token),
    }


@app.post("/api/settings/ai")
def save_ai_settings(settings: CloudflareSettings):
    global AI_PROVIDER
    if PUBLIC_HOSTED_MODE:
        raise HTTPException(403, "公网测试版由服务器环境变量管理 AI 密钥，访客不能修改共享配置。")
    account = settings.account_id.strip() or os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = settings.api_token.strip() or os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account or not token:
        raise HTTPException(400, "请填写 Account ID 和 API Token。若 Token 已保存，令牌栏可留空。")
    if not all(ch.isalnum() or ch in "-_" for ch in account):
        raise HTTPException(400, "Account ID 格式不正确。")
    vision = settings.vision_model.strip() or cloudflare_ai.VISION_MODEL
    image = settings.image_model.strip() or cloudflare_ai.IMAGE_MODEL
    video = settings.video_model.strip() or os.getenv("CLOUDFLARE_VIDEO_MODEL", "pixverse/v6")
    env_path = ROOT / ".env"
    for key, value in {
        "AI_PROVIDER": "cloudflare",
        "CLOUDFLARE_ACCOUNT_ID": account,
        "CLOUDFLARE_API_TOKEN": token,
        "CLOUDFLARE_VISION_MODEL": vision,
        "CLOUDFLARE_IMAGE_MODEL": image,
        "CLOUDFLARE_VIDEO_MODEL": video,
    }.items():
        set_key(str(env_path), key, value, quote_mode="always")
        os.environ[key] = value
    cloudflare_ai.VISION_MODEL = vision
    cloudflare_ai.IMAGE_MODEL = image
    AI_PROVIDER = "cloudflare"
    return {"saved": True, "configured": True, "vision_model": vision, "image_model": image, "video_model": video}


@app.post("/api/settings/ai/test")
async def test_ai_settings():
    if not os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip() or not os.getenv("CLOUDFLARE_API_TOKEN", "").strip():
        raise HTTPException(400, "请先保存 Cloudflare Account ID 和 API Token。")
    probe = Image.new("RGB", (96, 96), (235, 220, 205))
    bio = io.BytesIO()
    probe.save(bio, format="JPEG", quality=70)
    uri = "data:image/jpeg;base64," + base64.b64encode(bio.getvalue()).decode("ascii")
    result = await run_in_threadpool(cloudflare_ai._run, cloudflare_ai.VISION_MODEL, {
        "task": "query", "image": uri, "question": "Describe the visible colors in one short phrase.",
        "max_tokens": 40, "stream": False,
    }, 90)
    return {"ok": True, "model": cloudflare_ai.VISION_MODEL, "message": "视觉模型 API 连通，认证和模型调用成功。"}


TRANSLATION_LANGS = {
    "en":"english", "es":"spanish", "hi":"hindi", "ar":"arabic", "bn":"bengali",
    "pt":"portuguese", "ru":"russian", "ja":"japanese", "pa":"punjabi", "de":"german",
    "fr":"french", "ko":"korean", "vi":"vietnamese", "it":"italian", "tr":"turkish",
    "fa":"persian", "sw":"swahili", "th":"thai", "jv":"javanese", "mr":"marathi",
    "te":"telugu", "ta":"tamil", "ur":"urdu", "gu":"gujarati", "pl":"polish",
    "uk":"ukrainian", "nl":"dutch", "id":"indonesian", "ms":"malay",
}

@app.post("/api/translate-ui")
async def translate_ui(request: Request):
    body = await request.json()
    language = str(body.get("language", ""))
    source_language = str(body.get("source_language", "chinese"))
    texts = body.get("texts")
    if language not in TRANSLATION_LANGS or source_language not in ("chinese", "english") or not isinstance(texts, list) or not 1 <= len(texts) <= 12:
        raise HTTPException(400, "Unsupported language or translation batch")
    clean = [str(text).strip()[:700] for text in texts]
    glossary = [
        ("桌面AI精灵", "Desktop AI pet"), ("桌面 AI 精灵", "Desktop AI pet"),
        ("桌面精灵", "desktop pet"), ("桌面萌宠", "desktop pet"), ("AI萌宠", "AI pet"),
        ("原图", "source image"), ("上传", "upload"), ("人物特点", "distinctive character traits"),
        ("视觉特点", "visual traits"), ("特点", "distinctive features"), ("风格流派", "art style"),
        ("风格", "style"), ("精灵", "pet"), ("萌宠", "cute pet"), ("宠物", "pet"),
        ("人物", "person"), ("照片", "photo"), ("图片", "image"), ("图像", "image"),
        ("表情", "expression"), ("动作", "action"), ("生成", "generate"), ("下载", "download"),
        ("保存", "save"), ("切换", "switch"), ("设置", "settings"), ("按钮", "button"),
    ]
    if source_language == "chinese":
        for i, text in enumerate(clean):
            for term, replacement in glossary:
                text = text.replace(term, " " + replacement + " ")
            clean[i] = text
    async def translate(lines, source, target):
        if source == target:
            return lines
        # Keep each translation in one API call per 12 labels. Numeric sentinels
        # survive paragraph reflow and preserve the exact source-to-result map.
        markers = [f"ZXQ{i:03d}QXZ" for i in range(len(lines))]
        packed = " ".join(f"{markers[i]} {line}" for i, line in enumerate(lines))
        result = await run_in_threadpool(cloudflare_ai._run, "@cf/meta/m2m100-1.2b", {
            "text": packed, "source_lang": source, "target_lang": target,
        }, 90)
        translated = str(result.get("translated_text", ""))
        found = list(re.finditer(r"ZXQ(\d{3})QXZ", translated, flags=re.IGNORECASE))
        if len(found) == len(lines) and [int(match.group(1)) for match in found] == list(range(len(lines))):
            output = []
            for i, match in enumerate(found):
                begin = match.end()
                finish = found[i + 1].start() if i + 1 < len(found) else len(translated)
                output.append(translated[begin:finish].strip(" \t\r\n:.-"))
            if all(output):
                return output
        # Fallback for a translation model that removed the markers: retry each
        # phrase separately rather than silently restoring the whole UI to Chinese.
        async def one(line):
            result = await run_in_threadpool(cloudflare_ai._run, "@cf/meta/m2m100-1.2b", {
                "text": line, "source_lang": source, "target_lang": target,
            }, 90)
            return str(result.get("translated_text", "")).strip() or line
        return await asyncio.gather(*(one(line) for line in lines))
    english = await translate(clean, "chinese" if source_language == "chinese" else "english", "english")
    destination = TRANSLATION_LANGS[language]
    final = english if destination == "english" else await translate(english, "english", destination)
    return {"translations": final}


@app.get("/api/status")
def status():
    global pet_process
    pet_running = pet_process is not None and pet_process.poll() is None
    return {
        "ready": True,
        "provider": AI_PROVIDER,
        "cloudflare_configured": bool(os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip() and os.getenv("CLOUDFLARE_API_TOKEN", "").strip()),
        "diffusers_ready": pipe is not None,
        "engine": "cloudflare-workers-ai" if AI_PROVIDER == "cloudflare" else (MODEL_ID if pipe is not None else "procedural-abstract-v2"),
        "model": MODEL_ID,
        "device": DEVICE,
        "pet_running": pet_running,
        "error": getattr(app.state, "model_error", None),
        "comfy": comfy_chibi.status(),
    }


@app.post("/api/generate")
async def generate_legacy_filter():
    """Retired: this product now creates AI-generated 3D pets only."""
    raise HTTPException(410, "滤镜版生成接口已停用。请使用 /api/chibi3d 生成 3D 萌宠。")


@app.post("/api/chibi3d")
async def chibi3d(
    image: UploadFile = File(...),
    traits: str = Form(""),
    subject: str = Form("person"),
    texture: str = Form("blindbox"),
    style: str = Form("big"),
    similarity: float = Form(0.85),
    seed: int = Form(-1),
    outline_px: int = Form(6),
):
    """抓取照片中人物/宠物的特点，通过本机 ComfyUI 重新生成全新的 3D 大头小身子萌宠（不对原图抠图）。"""
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(415, detail="Upload an image file")
    raw = await image.read()
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(413, detail="Image must be smaller than 12 MB")
    try:
        source = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        raise HTTPException(400, detail="Could not decode image")
    source.thumbnail((1024, 1024))

    if AI_PROVIDER == "cloudflare":
        cf_result = await run_in_threadpool(
            cloudflare_ai.generate_sprite, source, traits=traits,
            subject=subject if subject in ("person", "pet") else "person", texture=texture, style=style,
        )
        buf = io.BytesIO()
        cf_result["sprite"].save(buf, format="PNG")
        png_bytes = buf.getvalue()
        latest_path = SPRITES_DIR / "latest_sprite.png"
        if not PUBLIC_HOSTED_MODE:
            latest_path.write_bytes(png_bytes)
        record_event("generate")
        return JSONResponse({
            "image": "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii"),
            "engine": f"Cloudflare Workers AI ({cloudflare_ai.VISION_MODEL} + {cloudflare_ai.IMAGE_MODEL})",
            "seed": None, "lora": None, "ipadapter": None,
            "prompt": cf_result["prompt"], "traits": cf_result["traits"],
            "warnings": ["图片会发送至 Cloudflare 进行特征分析和生成。"],
            "saved_path": str(latest_path),
        })

    try:
        result = await run_in_threadpool(
            comfy_chibi.generate_chibi,
            source,
            traits=traits,
            subject=subject if subject in ("person", "pet") else "person",
            texture=texture,
            style=style if style in ("big", "weird", "geo", "grumpy", "pop", "pixel") else "big",
            similarity=similarity,
            seed=seed,
            outline_px=max(0, min(20, int(outline_px))),
        )
    except comfy_chibi.ComfyError as e:
        raise HTTPException(e.status, detail=str(e))

    buf = io.BytesIO()
    result["sprite"].save(buf, format="PNG")
    png_bytes = buf.getvalue()
    latest_path = SPRITES_DIR / "latest_sprite.png"
    if not PUBLIC_HOSTED_MODE:
        latest_path.write_bytes(png_bytes)
        result["raw"].save(SPRITES_DIR / "latest_chibi_raw.png")

    record_event("generate")
    return JSONResponse({
        "image": "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii"),
        "engine": f"comfyui:{result['checkpoint']}",
        "seed": result["seed"],
        "lora": result["lora"],
        "ipadapter": result["ipadapter"],
        "prompt": result["positive"],
        "warnings": result["warnings"],
        "saved_path": str(latest_path),
    })


@app.post("/api/animate-pet")
async def animate_pet(sprite_data: str = Form(...), action: str = Form("walk"), expression: str = Form("cheerful")):
    """Generate an opt-in, model-driven action clip and a transparent looping WebP for the desktop pet."""
    if PUBLIC_HOSTED_MODE:
        raise HTTPException(404, "公网测试版暂不开放 AI 动作生成。")
    actions = {
        "walk": "The character walks back and forth with clear alternating steps, swinging arms, and visible leg movement.",
        "jump": "The character performs several energetic full-body two-foot jumps, raising both arms and landing with a playful crouch.",
        "pickup": "The character bends its knees, reaches down, picks up a small colorful object from the floor, then proudly holds it up.",
        "computer": "The character sits at a tiny laptop, types rapidly with both hands, looks at the screen, and reacts happily.",
        "play": "The character plays with a small bouncing ball, batting it with its hands and hopping after it with lively full-body motion.",
    }
    if action not in actions:
        raise HTTPException(400, "不支持的 AI 动作类型。")
    expressions = {
        "cheerful": "a warm broad smile, bright smiling eyes, natural blinking and lively eyebrows",
        "curious": "curious raised eyebrows, eyes glance around, head tilt, small questioning mouth and blinks",
        "focused": "focused slightly furrowed brows, attentive eyes tracking the task, subtle mouth movement and blinks",
        "surprised": "eyes widen, eyebrows pop up, mouth opens in a funny surprised expression, then blink",
        "grumpy": "playfully grumpy angled brows, a tiny pout and side-eye, then an expressive blink",
    }
    if expression not in expressions:
        raise HTTPException(400, "不支持的表情类型。")
    if not sprite_data.startswith("data:image/") or len(sprite_data) > 9_000_000:
        raise HTTPException(400, "请先生成 3D 萌宠；角色图片数据无效或过大。")
    try:
        header, encoded = sprite_data.split(",", 1)
        source_bytes = base64.b64decode(encoded, validate=True)
        source = Image.open(io.BytesIO(source_bytes)).convert("RGBA")
    except Exception:
        raise HTTPException(400, "无法读取精灵图片。")
    if not os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip() or not os.getenv("CLOUDFLARE_API_TOKEN", "").strip():
        raise HTTPException(503, "请先在 AI 接口设置中配置 Cloudflare API。")
    video_model = os.getenv("CLOUDFLARE_VIDEO_MODEL", "pixverse/v6").strip() or "pixverse/v6"
    cache_key = hashlib.sha256(source_bytes + action.encode("utf-8") + expression.encode("utf-8") + video_model.encode("utf-8") + b"360p").hexdigest()[:20]
    animation_path = SPRITES_DIR / f"motion_{cache_key}.webp"
    video_path = SPRITES_DIR / f"motion_{cache_key}.mp4"
    cached = animation_path.exists() and video_path.exists()
    if not cached:
        # Use the selected mascot as image-to-video reference. Calls happen only after an explicit user click.
        source.thumbnail((512, 512))
        source_buf = io.BytesIO()
        white_bg = Image.new("RGBA", source.size, (255, 255, 255, 255))
        white_bg.alpha_composite(source)
        white_bg.save(source_buf, format="PNG", optimize=True)
        image_uri = "data:image/png;base64," + base64.b64encode(source_buf.getvalue()).decode("ascii")
        prompt = (
            "Animate the exact same single cute 3D chibi desktop pet from the reference image. "
            "Preserve its face, hair, clothing, accessories, colors, proportions, and toy material. "
            "Full body stays visible, fixed camera, centered character, plain pure white background. "
            "Animate the face clearly throughout: eyes, eyebrows, mouth shape and natural blinking must change with the action; avoid a blank frozen stare. "
            "No scene changes, no cuts, no extra characters, no text. " + actions[action] + " Facial expression: " + expressions[expression]
        )[:1800]
        account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
        token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
        api_url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run"
        payload = {"model": video_model, "input": {
            "prompt": prompt, "image_input": image_uri, "duration": 3,
            "aspect_ratio": "1:1", "quality": "360p", "generate_audio": False,
        }}
        request = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                result = json.loads(response.read().decode("utf-8")).get("result", {})
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                message = "; ".join(str(e.get("message", e)) for e in detail.get("errors", []))
            except Exception:
                message = "云端视频模型请求失败。"
            raise HTTPException(502, f"AI 动作模型返回 {exc.code}：{message or '请检查 Cloudflare 视频模型权限和额度。'}")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise HTTPException(502, f"连接 AI 动作模型失败：{exc}")
        video_url = result.get("video")
        if not video_url or urlparse(video_url).scheme != "https" or urlparse(video_url).hostname not in {"media.pixverse.ai"} and not (urlparse(video_url).hostname or "").endswith(".r2.dev"):
            raise HTTPException(502, "AI 视频模型返回了非受信任的视频地址，已拒绝下载。")
        try:
            with urllib.request.urlopen(video_url, timeout=90) as remote:
                video_bytes = remote.read(40 * 1024 * 1024 + 1)
            if len(video_bytes) > 40 * 1024 * 1024:
                raise HTTPException(502, "动作视频超过 40 MB，已停止下载。")
            video_path.write_bytes(video_bytes)
        except urllib.error.URLError as exc:
            raise HTTPException(502, f"下载动作视频失败：{exc}")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            video_path.unlink(missing_ok=True)
            raise HTTPException(503, "生成视频成功，但本机没有 FFmpeg。请安装 FFmpeg 后再生成透明桌宠动作。")
        with tempfile.TemporaryDirectory(prefix="pet-motion-") as temp_dir:
            frame_pattern = str(Path(temp_dir) / "frame_%03d.png")
            proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video_path),
                "-vf", "fps=10,scale=256:256:force_original_aspect_ratio=decrease,pad=256:256:(ow-iw)/2:(oh-ih)/2",
                "-frames:v", "36", frame_pattern], capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                raise HTTPException(502, "无法从生成的动作视频提取动画帧。")
            frames = []
            for frame_file in sorted(Path(temp_dir).glob("frame_*.png")):
                frame = Image.open(frame_file).convert("RGBA")
                # Flood only near-white pixels connected to the border; preserve eyes and teeth inside the character.
                for point in ((0, 0), (255, 0), (0, 255), (255, 255)):
                    ImageDraw.floodfill(frame, point, (255, 255, 255, 0), thresh=46)
                frames.append(frame)
            if len(frames) < 4:
                raise HTTPException(502, "动作模型返回的视频帧数不足。")
            # Ping-pong loop avoids a hard jump at the loop boundary.
            loop_frames = frames + frames[-2:0:-1]
            loop_frames[0].save(animation_path, format="WEBP", save_all=True, append_images=loop_frames[1:],
                duration=100, loop=0, quality=84, method=4)
    return {"animation_url": f"/saved_sprites/{animation_path.name}",
        "animation_file": animation_path.name, "video_url": f"/saved_sprites/{video_path.name}",
        "expression": expression,
        "cached": cached}


@app.post("/api/launch-pet")
async def launch_pet(
    sprite_data: Optional[str] = Form(None),
    pet_name: str = Form("抽象小精灵"),
    scale: float = Form(1.0),
    animation_file: str = Form(""),
):
    """Save sprite data URL (if provided) and launch desktop_pet.py on local Windows/desktop."""
    global pet_process
    if PUBLIC_HOSTED_MODE:
        raise HTTPException(404, "桌面启动仅支持本机 Windows 版。")
    latest_path = SPRITES_DIR / "latest_sprite.png"

    if sprite_data and sprite_data.startswith("data:image/"):
        try:
            header, b64 = sprite_data.split(",", 1)
            latest_path.write_bytes(base64.b64decode(b64))
        except Exception as e:
            raise HTTPException(400, detail=f"Invalid sprite_data: {e}")

    if not latest_path.exists():
        raise HTTPException(400, detail="No sprite generated yet. Generate or upload a sprite first.")

    # Terminate previous instance if running
    if pet_process is not None and pet_process.poll() is None:
        try:
            pet_process.terminate()
        except Exception:
            pass

    pet_script = ROOT / "desktop_pet.py"
    if not pet_script.exists():
        raise HTTPException(404, detail="desktop_pet.py not found in project root.")

    try:
        launch_args = [sys.executable, str(pet_script), "--sprite", str(latest_path), "--name", pet_name, "--scale", str(scale)]
        if animation_file:
            safe_name = Path(animation_file).name
            animation_path = (SPRITES_DIR / safe_name).resolve()
            if animation_path.parent != SPRITES_DIR.resolve() or animation_path.suffix.lower() != ".webp" or not animation_path.is_file():
                raise HTTPException(400, "桌宠动作文件无效。")
            launch_args.extend(["--animation", str(animation_path)])
        pet_process = subprocess.Popen(
            launch_args,
            cwd=str(ROOT),
        )
        return {"launched": True, "pid": pet_process.pid, "sprite": str(latest_path), "name": pet_name}
    except Exception as e:
        raise HTTPException(500, detail=f"Failed to launch desktop pet window: {e}")


@app.post("/api/stats/event")
async def stats_event(request: Request):
    try:
        body = await request.json()
        kind = str(body.get("kind", ""))
    except Exception:
        kind = ""
    if kind not in EVENT_KINDS:
        raise HTTPException(400, "未知统计类型。")
    record_event(kind, _client_ip(request))
    return {"ok": True}


@app.get("/api/stats/summary")
def stats_summary():
    return _summary_data()


class CommentIn(BaseModel):
    name: str = Field(default="", max_length=20)
    text: str = Field(default="", max_length=200)


@app.get("/api/comments")
def list_comments():
    items = []
    try:
        conn = _stats_conn()
        for cid, name, text, ts in conn.execute(
            "SELECT id, name, text, ts FROM comments ORDER BY id DESC LIMIT 50"
        ):
            items.append({"id": cid, "name": name, "text": text, "ts": ts})
        conn.close()
    except Exception:
        pass
    return {"comments": items}


@app.post("/api/comments")
def post_comment(request: Request, body: CommentIn):
    text = body.text.strip()
    name = body.name.strip() or "匿名"
    if not text:
        raise HTTPException(400, "留言内容不能为空。")
    ip = _client_ip(request)
    try:
        conn = _stats_conn()
        last = conn.execute(
            "SELECT ts FROM comments WHERE ip = ? ORDER BY id DESC LIMIT 1", (ip,)
        ).fetchone()
        if last and int(time.time()) - int(last[0]) < 30:
            conn.close()
            raise HTTPException(429, "留言太频繁，30 秒后再试。")
        conn.execute(
            "INSERT INTO comments (name, text, ts, ip) VALUES (?, ?, ?, ?)",
            (name, text, int(time.time()), ip),
        )
        conn.commit()
        conn.close()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "留言保存失败。")
    return {"ok": True}


@app.get("/stats", include_in_schema=False)
def stats_page():
    data = _summary_data()
    totals = data["totals"]
    rows = "".join(
        f"<tr><td>{html.escape(d['date'])}</td><td>{d['page_view']}</td>"
        f"<td>{d['generate']}</td><td>{d['share']}</td><td>{d['download']}</td></tr>"
        for d in data["daily"]
    )
    comments = []
    try:
        conn = _stats_conn()
        for _cid, name, text, ts in conn.execute(
            "SELECT id, name, text, ts FROM comments ORDER BY id DESC LIMIT 100"
        ):
            when = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%m-%d %H:%M")
            comments.append(
                f"<li><b>{html.escape(name)}</b> <span>{html.escape(when)}</span>"
                f"<p>{html.escape(text)}</p></li>"
            )
        conn.close()
    except Exception:
        pass
    body = "".join(comments) if comments else "<li>还没有留言。</li>"
    page = STATS_TEMPLATE.safe_substitute(
        views=totals["page_view"], generates=totals["generate"],
        shares=totals["share"], downloads=totals["download"],
        comments_n=data["comments"], rows=rows, body=body, n=len(comments),
    )
    return Response(page, media_type="text/html; charset=utf-8")


@app.get("/", include_in_schema=False)
async def home_page():
    return FileResponse(ROOT / "index.html", media_type="text/html; charset=utf-8")

app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

