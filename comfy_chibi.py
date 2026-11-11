"""ComfyUI 对接模块：把照片里的人物/宠物"特点"提取出来，重新生成一个全新的 3D 大头小身子萌宠。

设计要点
--------
1. 不对原图抠图。原图只作为"特征参考图"喂给 IP-Adapter，再配合自动提取的颜色 + 用户补充的特点文字，
   从空白画布 (txt2img) 生成全新的 3D 形象，因此体型比例（大头小身子）由提示词/LoRA 决定，而不是被原图姿势限制。
2. 自动探测 ComfyUI 里已有的 checkpoint / LoRA / IP-Adapter 节点，找不到 IP-Adapter 时自动退化为 img2img。
3. 只用标准库 + Pillow + numpy，不新增依赖。

环境变量（均可选）
-----------------
COMFY_URL          ComfyUI 地址，默认 http://127.0.0.1:8188
COMFY_CHECKPOINT   指定底模文件名（与 ComfyUI 下拉列表中一致）
COMFY_LORA         指定 LoRA 文件名；设为 none 则不用 LoRA
COMFY_LORA_WEIGHT  LoRA 强度，默认 0.8
COMFY_WIDTH/HEIGHT 出图尺寸，默认 512x640（4-6GB 显存友好）
COMFY_STEPS / COMFY_CFG  采样步数/CFG，默认 24 / 6.5
COMFY_TIMEOUT      单次生成最长等待秒数，默认 900（首次加载模型较慢）
"""
from __future__ import annotations

import colorsys
import io
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

COMFY_URL = os.getenv("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
COMFY_API_KEY = os.getenv("COMFY_API_KEY", "").strip()
OUT_W = int(os.getenv("COMFY_WIDTH", "512"))
OUT_H = int(os.getenv("COMFY_HEIGHT", "640"))
STEPS = int(os.getenv("COMFY_STEPS", "24"))
CFG = float(os.getenv("COMFY_CFG", "6.5"))
TIMEOUT = int(os.getenv("COMFY_TIMEOUT", "900"))


class ComfyError(RuntimeError):
    """ComfyUI 不可用或工作流执行失败。status 对应建议的 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------------------
# HTTP 小工具（标准库）
# --------------------------------------------------------------------------------------
def _request(path: str, data: Optional[bytes] = None, headers: Optional[dict] = None, timeout: float = 20):
    request_headers = dict(headers or {})
    if COMFY_API_KEY:
        request_headers["Authorization"] = f"Bearer {COMFY_API_KEY}"
    req = urllib.request.Request(COMFY_URL + path, data=data, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise ComfyError(_explain_http_error(body) or f"ComfyUI 返回 HTTP {e.code}", 502) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        raise ComfyError(
            f"连不上 ComfyUI（{COMFY_URL}）。请先启动 ComfyUI，并确认地址端口正确。详情：{e}", 503
        ) from None


def _get_json(path: str, timeout: float = 20) -> Any:
    return json.loads(_request(path, timeout=timeout).decode("utf-8"))


def _explain_http_error(body: str) -> str:
    """把 ComfyUI /prompt 的校验错误整理成人能看懂的一句话。"""
    try:
        payload = json.loads(body)
    except Exception:
        return body[:300]
    parts = []
    err = payload.get("error")
    if isinstance(err, dict):
        parts.append(str(err.get("message", "")) + (f"：{err.get('details')}" if err.get("details") else ""))
    node_errors = payload.get("node_errors") or {}
    for nid, info in node_errors.items():
        cls = info.get("class_type", nid)
        for e in info.get("errors", []):
            parts.append(f"[{cls}] {e.get('message', '')} {e.get('details', '')}".strip())
    return "；".join(p for p in parts if p)[:600]


def _multipart(fields: dict[str, str], file_field: str, filename: str, file_bytes: bytes, mime: str):
    boundary = "----cartoonstudio" + uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    out.write(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n".encode()
    )
    out.write(file_bytes)
    out.write(f"\r\n--{boundary}--\r\n".encode())
    return out.getvalue(), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


# --------------------------------------------------------------------------------------
# 探测 ComfyUI 里有什么
# --------------------------------------------------------------------------------------
def _node_input_options(info: dict, node: str, field: str) -> list[str]:
    try:
        spec = info[node]["input"]
        spec = {**spec.get("required", {}), **spec.get("optional", {})}[field]
        opts = spec[0]
        return list(opts) if isinstance(opts, list) else []
    except Exception:
        return []


def _object_info(node: str) -> dict:
    try:
        data = _get_json(f"/object_info/{urllib.parse.quote(node)}", timeout=15)
        return data if isinstance(data, dict) else {}
    except ComfyError as e:
        if e.status == 503:
            raise
        return {}


def probe() -> dict:
    """返回 ComfyUI 当前可用资源。离线时抛 ComfyError(503)。"""
    ckpt_info = _object_info("CheckpointLoaderSimple")
    lora_info = _object_info("LoraLoader")
    unified = _object_info("IPAdapterUnifiedLoader")
    ipa = _object_info("IPAdapter")
    return {
        "checkpoints": _node_input_options(ckpt_info, "CheckpointLoaderSimple", "ckpt_name"),
        "loras": _node_input_options(lora_info, "LoraLoader", "lora_name"),
        "ipadapter_ready": bool(unified) and bool(ipa),
        "ip_presets": _node_input_options(unified, "IPAdapterUnifiedLoader", "preset"),
        "ip_weight_types": _node_input_options(ipa, "IPAdapter", "weight_type"),
    }


def status() -> dict:
    """给 /api/status 用：不抛异常。"""
    try:
        p = probe()
        return {
            "online": True,
            "url": COMFY_URL,
            "checkpoint": pick_checkpoint(p["checkpoints"]),
            "lora": pick_lora(p["loras"]),
            "ipadapter_ready": p["ipadapter_ready"],
            "checkpoint_count": len(p["checkpoints"]),
        }
    except ComfyError as e:
        return {"online": False, "url": COMFY_URL, "error": str(e)}


def pick_checkpoint(names: list[str]) -> Optional[str]:
    forced = os.getenv("COMFY_CHECKPOINT")
    if forced:
        return forced
    if not names:
        return None

    prefer = ["pixar", "disney", "3d", "cartoon", "toon", "dreamshaper", "revanimated", "rev_animated",
              "majicmix", "cute", "anime", "v1-5", "v1_5", "sd15", "sd1.5"]

    def score(n: str) -> int:
        low = n.lower()
        s = 0
        for i, kw in enumerate(prefer):
            if kw in low:
                s += len(prefer) - i
        # 4-6GB 显存：避开 SDXL / 局部重绘 / refiner 等
        if "xl" in low or "inpaint" in low or "refiner" in low or "flux" in low or "sd3" in low:
            s -= 100
        return s

    return sorted(names, key=score, reverse=True)[0]


def pick_lora(names: list[str]) -> Optional[str]:
    forced = os.getenv("COMFY_LORA")
    if forced:
        return None if forced.lower() == "none" else forced
    kws = ["chibi", "blindbox", "blind_box", "blind-box", "3d", "clay", "pixar", "toy", "cute"]
    for kw in kws:
        for n in names:
            low = n.lower()
            if kw in low and "xl" not in low:
                return n
    return None


def _pick_option(options: list[str], preferred_substrings: list[str]) -> Optional[str]:
    for sub in preferred_substrings:
        for o in options:
            if sub.lower() in o.lower():
                return o
    return options[0] if options else None


# --------------------------------------------------------------------------------------
# 从原图提取"特点"：颜色 + 用户补充文字（中文关键词自动转英文）
# --------------------------------------------------------------------------------------
_ZH2EN = [
    ("头戴耳机", "headphones"), ("耳机", "headphones"), ("圆框", "round frame"), ("方框", "square frame"),
    ("墨镜", "sunglasses"), ("眼镜", "glasses"), ("蝴蝶结", "bow"), ("领结", "bow tie"), ("领带", "necktie"),
    ("贝雷帽", "beret"), ("棒球帽", "baseball cap"), ("帽子", "hat"), ("围巾", "scarf"),
    ("卷发", "curly hair"), ("短发", "short hair"), ("长发", "long hair"), ("黑发", "black hair"),
    ("金发", "blonde hair"), ("胡子", "beard"), ("大笑", "laughing"), ("感染力", ""), ("微笑", "smiling"),
    ("开心", "happy"), ("傲娇", "pouting"), ("生气", "grumpy"), ("卫衣", "hoodie"), ("撞色", ""),
    ("西装", "suit"), ("裙子", "dress"), ("英短", "british shorthair cat"), ("虎斑", "tabby"),
    ("猫", "cat"), ("狗", "dog"), ("兔", "rabbit"), ("牙套", "braces"), ("雀斑", "freckles"),
    ("酒窝", "dimples"), ("圆脸", "round face"), ("大眼睛", "big eyes"),
    ("蓝灰", "blue-gray"), ("灰色", "gray"), ("灰", "gray"), ("红", "red"), ("橙", "orange"), ("鹅黄", "yellow"),
    ("黄", "yellow"), ("深青", "dark teal"), ("青", "teal"), ("绿", "green"), ("蓝", "blue"), ("紫", "purple"),
    ("粉", "pink"), ("黑", "black"), ("白", "white"), ("棕", "brown"), ("亮", "bright"), ("深", "dark"),
    ("色", ""), ("的", ""),
]


def traits_to_english(text: str) -> str:
    """支持中英文混写。无法识别的中文词会被丢弃（SD1.5 的文本编码器不认中文），英文原样保留。"""
    if not text:
        return ""
    out = text
    for zh, en in sorted(_ZH2EN, key=lambda kv: -len(kv[0])):
        out = out.replace(zh, f" {en} " if en else " ")
    out = re.sub(r"[^\x00-\x7f]+", " ", out)            # 去掉剩余非 ASCII
    out = re.sub(r"[;,，、/|]+", ",", out)
    out = re.sub(r"\s+", " ", out).strip(" ,")
    return out


def _color_name(rgb: tuple[int, int, int]) -> Optional[str]:
    r, g, b = [c / 255.0 for c in rgb]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h *= 360
    if v < 0.16:
        return "black"
    if s < 0.14:
        return "white" if v > 0.86 else ("light gray" if v > 0.6 else "gray" if v > 0.35 else "charcoal")
    # 肤色区间：不当作"特点颜色"，避免生成橙色皮肤
    if 8 <= h <= 38 and 0.15 <= s <= 0.62 and v > 0.45:
        return None
    if h < 15 or h >= 345:
        name = "red"
    elif h < 40:
        name = "brown" if v < 0.55 else "orange"
    elif h < 65:
        name = "yellow"
    elif h < 160:
        name = "green"
    elif h < 200:
        name = "teal"
    elif h < 255:
        name = "blue"
    elif h < 290:
        name = "purple"
    else:
        name = "pink"
    if v < 0.42 and name not in ("brown",):
        name = "dark " + name
    return name


def dominant_colors(img: Image.Image, max_colors: int = 3) -> list[str]:
    """本地统计主体主色（排除与边缘背景相近的像素与肤色）。仅作提示词提示，不做抠图。"""
    im = ImageOps.contain(img.convert("RGB"), (96, 96))
    arr = np.asarray(im, dtype=np.float32)
    h, w, _ = arr.shape
    border = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]], axis=0)
    bg = np.median(border, axis=0)
    diff = np.linalg.norm(arr - bg, axis=2) / 441.67
    keep = diff > 0.14
    if keep.sum() < 50:
        return []
    px = arr[keep].astype(np.uint8)
    tiny = Image.fromarray(px.reshape(-1, 1, 3), "RGB")
    q = tiny.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: 6 * 3]
    counts = sorted(q.getcolors() or [], reverse=True)
    names: list[str] = []
    for cnt, idx in counts:
        nm = _color_name((pal[idx * 3], pal[idx * 3 + 1], pal[idx * 3 + 2]))
        if nm and nm not in names:
            names.append(nm)
        if len(names) >= max_colors:
            break
    return names


TEXTURES = {
    "blindbox": "3d render, blind box toy figure, glossy vinyl toy, pop mart style, smooth plastic material",
    "clay": "clay figure, plasticine, handmade claymation, soft fingerprint texture, matte clay material",
    "pixar": "pixar style 3d animated character, subsurface scattering, soft studio lighting, disney 3d",
    "plush": "plush toy, fuzzy soft fabric, stitched seams, felt material, stuffed doll",
}

BASE_POS = (
    "cute chibi character, super deformed, oversized head, tiny body, short stubby arms and legs, "
    "full body, standing, front view, huge shiny eyes, adorable, octane render, soft rim light, "
    "plain white background, simple background, centered, masterpiece, best quality"
)

STYLE_DIRECTIONS = {
    "big": "oversized-head cute chibi collectible, tiny round body, giant sparkling eyes, bright toy colors and a happy grin",
    "weird": "absurd surreal comedy caricature, lopsided silhouette, mismatched eyes, strange playful proportions and eccentric color pairings",
    "geo": "three-dimensional geometric cubist sculpture made of angular planes, cubes, triangles and bold contrasting color blocks",
    "grumpy": "cheeky satirical comic caricature, arched brows, exaggerated nose and smirk, dark ink contours and cel-shaded planes",
    "pop": "high-energy pop-art sticker collectible, saturated cyan magenta yellow, thick comic outlines, halftone dots and glossy enamel",
    "pixel": "retro 16-bit voxel mascot built from chunky square pixels, limited arcade palette, crisp blocky edges and pixel-cluster details",
}

BASE_NEG = (
    "realistic, photo, photograph, text, watermark, signature, logo, cropped, out of frame, "
    "extra limbs, deformed, bad anatomy, long legs, tall body, adult proportions, blurry, lowres, "
    "worst quality, low quality, multiple characters, busy background, floor, shadow on wall"
)


def build_prompts(traits: str, colors: list[str], texture: str, subject: str, style: str = "big") -> tuple[str, str]:
    tex = TEXTURES.get(texture, TEXTURES["blindbox"])
    style_direction = STYLE_DIRECTIONS.get(style, STYLE_DIRECTIONS["big"])
    parts = [tex, style_direction, BASE_POS]
    if subject == "pet":
        parts.insert(0, "cute animal mascot")
    en = traits_to_english(traits)
    if en:
        parts.append(f"({en}:1.15)")
    if colors:
        parts.append("color scheme of " + " and ".join(colors))
    return ", ".join(parts), BASE_NEG


# --------------------------------------------------------------------------------------
# 工作流（ComfyUI API 格式）
# --------------------------------------------------------------------------------------
def build_workflow(
    *,
    ckpt: str,
    lora: Optional[str],
    lora_weight: float,
    positive: str,
    negative: str,
    seed: int,
    image_name: str,
    use_ipadapter: bool,
    ip_preset: Optional[str] = None,
    ip_weight_type: str = "linear",
    ip_weight: float = 0.85,
    width: int = OUT_W,
    height: int = OUT_H,
    steps: int = STEPS,
    cfg: float = CFG,
    img2img_denoise: float = 0.72,
) -> dict:
    wf: dict[str, dict] = {}
    wf["1"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}}
    model, clip, vae = ["1", 0], ["1", 1], ["1", 2]

    if lora:
        wf["2"] = {
            "class_type": "LoraLoader",
            "inputs": {"model": model, "clip": clip, "lora_name": lora,
                       "strength_model": lora_weight, "strength_clip": lora_weight},
        }
        model, clip = ["2", 0], ["2", 1]

    wf["3"] = {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": clip}}
    wf["4"] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": clip}}
    wf["5"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}

    if use_ipadapter:
        wf["6"] = {"class_type": "IPAdapterUnifiedLoader",
                   "inputs": {"model": model, "preset": ip_preset}}
        wf["7"] = {
            "class_type": "IPAdapter",
            "inputs": {"model": ["6", 0], "ipadapter": ["6", 1], "image": ["5", 0],
                       "weight": ip_weight, "start_at": 0.0, "end_at": 1.0,
                       "weight_type": ip_weight_type},
        }
        model = ["7", 0]
        # 从空白画布生成：体型由提示词决定，原图只提供"特征"
        wf["8"] = {"class_type": "EmptyLatentImage",
                   "inputs": {"width": width, "height": height, "batch_size": 1}}
        latent, denoise = ["8", 0], 1.0
    else:
        # 退化方案：没有 IP-Adapter 时用 img2img，从原图出发（相似度靠 denoise 控制）
        wf["8"] = {"class_type": "ImageScale",
                   "inputs": {"image": ["5", 0], "upscale_method": "lanczos",
                              "width": width, "height": height, "crop": "center"}}
        wf["9"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["8", 0], "vae": vae}}
        latent, denoise = ["9", 0], img2img_denoise

    wf["10"] = {
        "class_type": "KSampler",
        "inputs": {"model": model, "seed": seed, "steps": steps, "cfg": cfg,
                   "sampler_name": "dpmpp_2m", "scheduler": "karras",
                   "positive": ["3", 0], "negative": ["4", 0], "latent_image": latent, "denoise": denoise},
    }
    wf["11"] = {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": vae}}
    wf["12"] = {"class_type": "SaveImage",
                "inputs": {"images": ["11", 0], "filename_prefix": "cartoon_studio_chibi"}}
    return wf


# --------------------------------------------------------------------------------------
# 去除"生成图"的纯色背景（不是抠原图人物）+ 居中裁切 + 贴纸白边
# --------------------------------------------------------------------------------------
def _border_connected(cand: np.ndarray) -> np.ndarray:
    """cand 中与图像边缘四连通的区域（纯 numpy/标准库实现，不依赖 scipy）。"""
    from collections import deque

    h, w = cand.shape
    seen = np.zeros_like(cand, dtype=bool)
    dq: deque = deque()
    for x in range(w):
        for y in (0, h - 1):
            if cand[y, x] and not seen[y, x]:
                seen[y, x] = True
                dq.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if cand[y, x] and not seen[y, x]:
                seen[y, x] = True
                dq.append((y, x))
    while dq:
        y, x = dq.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and cand[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                dq.append((ny, nx))
    return seen


def finish_sprite(img: Image.Image, outline_px: int = 6, size: int = 512, tol: float = 0.075) -> Image.Image:
    rgb = img.convert("RGB")
    arr = np.asarray(rgb, dtype=np.float32)
    h, w, _ = arr.shape
    border = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]], axis=0)
    bg = np.median(border, axis=0)
    diff = np.linalg.norm(arr - bg, axis=2) / 441.67
    cand = diff < tol
    bg_mask = _border_connected(cand)                 # 只去掉与边缘连通的背景，内部白色（眼白等）保留

    alpha = Image.fromarray(np.where(bg_mask, 0, 255).astype(np.uint8), "L")
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.9))
    rgba = rgb.convert("RGBA")
    rgba.putalpha(alpha)

    bbox = alpha.point(lambda v: 255 if v > 24 else 0).getbbox()
    if bbox:
        pad = int(max(bbox[2] - bbox[0], bbox[3] - bbox[1]) * 0.06) + max(0, outline_px)
        bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(w, bbox[2] + pad), min(h, bbox[3] + pad))
        rgba = rgba.crop(bbox)

    side = max(rgba.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(rgba, ((side - rgba.width) // 2, (side - rgba.height) // 2), rgba)
    canvas = canvas.resize((size, size), Image.Resampling.LANCZOS)

    if outline_px > 0:
        a = canvas.getchannel("A")
        dil = a
        for _ in range(max(1, outline_px // 2)):
            dil = dil.filter(ImageFilter.MaxFilter(5))
        dil = dil.filter(ImageFilter.GaussianBlur(0.8))
        sticker = Image.new("RGBA", canvas.size, (255, 253, 247, 255))
        sticker.putalpha(dil)
        canvas = Image.alpha_composite(sticker, canvas)
    return canvas


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def generate_chibi(
    source: Image.Image,
    *,
    traits: str = "",
    subject: str = "person",          # person | pet
    texture: str = "blindbox",        # blindbox | clay | pixar | plush
    style: str = "big",
    similarity: float = 0.85,         # IP-Adapter 权重（或 img2img 时映射为 denoise）
    seed: int = -1,
    outline_px: int = 6,
) -> dict:
    res = probe()                      # 离线会在这里抛 503
    ckpt = pick_checkpoint(res["checkpoints"])
    if not ckpt:
        raise ComfyError("ComfyUI 的 models/checkpoints 里没有底模。请放入一个 SD1.5 底模（如 DreamShaper 8）后重试。", 400)
    lora = pick_lora(res["loras"])
    lora_w = float(os.getenv("COMFY_LORA_WEIGHT", "0.8"))
    use_ip = res["ipadapter_ready"]

    warnings: list[str] = []
    ip_preset = None
    ip_wt = "linear"
    if use_ip:
        prefer = ["PLUS FACE", "PLUS (high", "STANDARD"] if subject != "pet" else ["PLUS (high", "STANDARD", "PLUS FACE"]
        ip_preset = _pick_option(res["ip_presets"], prefer)
        ip_wt = _pick_option(res["ip_weight_types"], ["linear", "standard"]) or "linear"
    else:
        warnings.append("未检测到 ComfyUI_IPAdapter_plus 节点，已退化为 img2img，人物特征还原度和\"全新形象\"效果会明显变弱。")
    if not lora:
        warnings.append("未找到 3D/Chibi 类 LoRA，仅靠提示词控制大头小身子。可在 ComfyUI 的 models/loras 放入 chibi / blindbox 类 LoRA。")

    # 原图：补成正方形（不裁掉主体），仅用作特征参考
    src_rgb = source.convert("RGB")
    src_arr = np.asarray(src_rgb)
    pad_color = tuple(int(c) for c in np.median(np.concatenate([src_arr[0, :], src_arr[-1, :]]), axis=0))
    ref = ImageOps.pad(src_rgb, (512, 512), method=Image.Resampling.LANCZOS, color=pad_color)
    colors = dominant_colors(source)
    positive, negative = build_prompts(traits, colors, texture, subject, style)

    buf = io.BytesIO()
    ref.save(buf, format="PNG")
    name = f"cartoon_studio_ref_{uuid.uuid4().hex[:8]}.png"
    body, hdr = _multipart({"overwrite": "true", "type": "input"}, "image", name, buf.getvalue(), "image/png")
    up = json.loads(_request("/upload/image", data=body, headers=hdr, timeout=60).decode())
    image_name = up.get("name", name)
    if up.get("subfolder"):
        image_name = f"{up['subfolder']}/{image_name}"

    if seed is None or seed < 0:
        seed = random.randint(0, 2**31 - 1)
    sim = max(0.2, min(1.2, float(similarity)))

    wf = build_workflow(
        ckpt=ckpt, lora=lora, lora_weight=lora_w, positive=positive, negative=negative, seed=seed,
        image_name=image_name, use_ipadapter=use_ip, ip_preset=ip_preset, ip_weight_type=ip_wt,
        ip_weight=sim, img2img_denoise=max(0.45, min(0.9, 1.05 - sim * 0.5)),
    )

    client_id = uuid.uuid4().hex
    payload = json.dumps({"prompt": wf, "client_id": client_id}).encode()
    resp = json.loads(_request("/prompt", data=payload, headers={"Content-Type": "application/json"}, timeout=60).decode())
    prompt_id = resp.get("prompt_id")
    if not prompt_id:
        raise ComfyError("ComfyUI 没有返回 prompt_id：" + json.dumps(resp)[:300])

    deadline = time.time() + TIMEOUT
    outputs = None
    while time.time() < deadline:
        hist = _get_json(f"/history/{prompt_id}", timeout=20)
        item = hist.get(prompt_id)
        if item:
            st = (item.get("status") or {})
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
                detail = ""
                if msgs:
                    d = msgs[-1][1]
                    detail = f"{d.get('node_type', '')}: {d.get('exception_message', '')}".strip()
                raise ComfyError("ComfyUI 执行出错：" + (detail or "未知错误，请看 ComfyUI 控制台"))
            outputs = item.get("outputs")
            if outputs:
                break
        time.sleep(0.8)
    if not outputs:
        raise ComfyError(f"等待 ComfyUI 出图超时（>{TIMEOUT}s）。显存不足时可用 --lowvram 启动 ComfyUI。", 504)

    images = outputs.get("12", {}).get("images") or next((o["images"] for o in outputs.values() if o.get("images")), [])
    if not images:
        raise ComfyError("ComfyUI 完成了任务但没有输出图片。")
    im0 = images[0]
    q = urllib.parse.urlencode({"filename": im0["filename"], "subfolder": im0.get("subfolder", ""), "type": im0.get("type", "output")})
    raw = Image.open(io.BytesIO(_request(f"/view?{q}", timeout=60))).convert("RGB")

    sprite = finish_sprite(raw, outline_px=outline_px)
    return {
        "sprite": sprite,
        "raw": raw,
        "seed": seed,
        "checkpoint": ckpt,
        "lora": lora,
        "ipadapter": use_ip,
        "positive": positive,
        "warnings": warnings,
    }

