"""Cloudflare Workers AI adapter: visual feature extraction + fresh text-to-image."""
from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request

from fastapi import HTTPException
from PIL import Image, ImageDraw


VISION_MODEL = os.getenv("CLOUDFLARE_VISION_MODEL", "@cf/moondream/moondream3.1-9B-A2B")
IMAGE_MODEL = os.getenv("CLOUDFLARE_IMAGE_MODEL", "@cf/black-forest-labs/flux-2-klein-4b")
TEXTURES = {
    "blindbox": "MATERIAL: the entire head and body are hard injection-molded vinyl/resin, perfectly smooth and seamless, thick glossy clearcoat, crisp bright specular highlights that visibly reflect the studio lights; unmistakably a shiny collectible toy; absolutely no fuzz, fabric, stitches, fingerprints, or matte clay.",
    "clay": "MATERIAL: the entire head and body are hand-sculpted matte clay, visibly soft and slightly imperfect, subtle fingerprints and sculpting tool marks across the cheeks and body, rounded pressed clay shapes with soft diffuse highlights; absolutely no glossy resin, plastic clearcoat, fur, fabric, or sewn seams.",
    "pixar": "MATERIAL: polished feature-animation CGI shading across the whole character, smooth carefully sculpted forms, soft subsurface light scattering through cheeks and ears, nuanced soft skin/fur shader, gentle cinematic bounce light; clearly rendered animated-film 3D, not a photo, not a vinyl collectible, no fabric stitches.",
    "plush": "MATERIAL: the entire head and body are sewn from visibly fuzzy plush fabric, fine fibers and soft pile catch the light, visible seam stitching around the head and limbs, felt pads and embroidered details; unmistakably soft stuffed plush; absolutely no shiny plastic, resin, hard gloss, or smooth clay.",
}
STYLES = {
    "big": "STYLE DIRECTION: joyful oversized-head chibi collectible, tiny rounded body, huge sparkling eyes, puffed cheeks, bright friendly colors, polished smooth vinyl toy surfaces, cute confident grin. Make this classic cute-toy look unmistakable.",
    "weird": "STYLE DIRECTION: absurd surreal 3D comedy caricature, deliberately lopsided silhouette, mismatched oversized eyes, unexpected warped but charming proportions, strange playful accessories, eccentric color pairings, visual punchline. Make it weird and funny, not conventionally cute or realistic.",
    "geo": "STYLE DIRECTION: three-dimensional geometric cubist sculpture, face and body constructed from bold angular planes, cubes, triangles and faceted color blocks, asymmetric Picasso-like arrangement, crisp hard edges, contrasting primary colors. The geometry must define the whole character, not just the background.",
    "grumpy": "STYLE DIRECTION: cheeky satirical comic caricature as a sculpted 3D editorial-toy figure, strongly arched brows, exaggerated nose and smirk, bold dark ink contours, cel-shaded comic planes and expressive grumpy pose. Witty and mischievous, not mean or photorealistic.",
    "pop": "STYLE DIRECTION: high-energy 3D pop-art sticker collectible, saturated cyan-magenta-yellow-red palette, thick comic outlines, halftone dot patterns on the character, graphic burst accents and glossy enamel finish. Strong poster-like pop-art look, no lettering.",
    "pixel": "STYLE DIRECTION: unmistakable retro 16-bit voxel desktop mascot, chunky square pixels and block-built 3D voxel silhouette, limited arcade palette, crisp stepped edges, pixel-cluster eyes and accessories. No smooth gradients or photorealistic surfaces.",
}


def _run(model: str, payload: dict, timeout: int = 240) -> dict:
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account or not token:
        raise HTTPException(
            503,
            "Cloudflare 尚未配置：请在项目目录的 .env 中填写 Account ID 和 API Token，然后重启服务。",
        )
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
            errors = data.get("errors", [])
            codes = {item.get("code") for item in errors}
            messages = "; ".join(str(item.get("message", item)) for item in errors)
        except Exception:
            codes = set()
            messages = "请求失败"
        if 5016 in codes:
            messages = "视觉模型尚未接受 Meta Llama 3.2 许可。请先阅读许可并在 Cloudflare 明确同意后重试。"
        elif exc.code == 401:
            messages = "API Token 无效。请确认 .env 中使用的是当前有效的新 Token。"
        elif exc.code == 403:
            messages = "Token 缺少 Workers AI 权限或账号范围不正确。请确认该账号已授予 Workers AI Read/Write。"
        elif exc.code == 429:
            messages = "请求过多或额度暂时不足，请检查 Workers AI 用量后重试。"
        raise HTTPException(502, f"Cloudflare AI ({exc.code})：{messages}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(502, f"连接 Cloudflare AI 失败：{exc}")
    if not data.get("success", True):
        detail = "; ".join(str(item.get("message", item)) for item in data.get("errors", []))
        raise HTTPException(502, f"Cloudflare AI 返回错误：{detail or '未知错误'}")
    return data.get("result") or {}


def _run_multipart(model: str, fields: dict, files: dict, timeout: int = 300) -> dict:
    """Call Cloudflare image-reference models using the required multipart REST format."""
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account or not token:
        raise HTTPException(503, "Cloudflare 尚未配置：请填写项目目录 .env 中的账号信息。")
    boundary = "----DesktopSprite" + os.urandom(12).hex()
    chunks = []
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), str(value).encode(), b"\r\n"])
    for name, (filename, mime, content) in files.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), content, b"\r\n"])
    chunks.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}", data=b"".join(chunks),
        headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    data = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            try:
                errors = json.loads(exc.read().decode("utf-8")).get("errors", [])
                detail = "; ".join(str(item.get("message", item)) for item in errors) or "请求失败"
            except Exception:
                detail = "请求失败"
            if exc.code in (500, 502, 503) and attempt < 2:
                time.sleep(2)
                continue
            raise HTTPException(502, f"Cloudflare 图片参考生成失败 ({exc.code})：{detail}")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise HTTPException(502, f"连接 Cloudflare AI 失败：{exc}")
    if not data.get("success", True):
        raise HTTPException(502, f"Cloudflare AI 返回错误：{data.get('errors')}")
    return data.get("result") or {}


def generate_sprite(source: Image.Image, *, traits: str = "", subject: str = "person", texture: str = "blindbox", style: str = "big") -> dict:
    vision = source.convert("RGB")
    vision.thumbnail((768, 768))
    stream = io.BytesIO()
    vision.save(stream, format="JPEG", quality=86, optimize=True)
    image_uri = "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode("ascii")

    analysis = _run(VISION_MODEL, {
        "task": "query",
        "image": image_uri,
        "question": (
            "Describe precise visible character anchors: hair/fur silhouette and color, face outline, eyebrow and eye shape, "
            "glasses, facial hair, accessories, clothing silhouette/colors, and expression. Name the two or three most "
            "distinctive features that must remain recognizable. Do not identify the person or infer sensitive traits."
        ),
        "reasoning": True,
        "max_tokens": 180,
        "stream": False,
    })
    vision_result = analysis.get("result") if isinstance(analysis.get("result"), dict) else analysis
    visual_traits = str(
        vision_result.get("answer") or vision_result.get("caption")
        or analysis.get("answer") or analysis.get("caption") or ""
    ).strip()[:900]
    if not visual_traits:
        raise HTTPException(502, "Cloudflare 视觉模型没有返回可用的人物特征描述。")
    if traits.strip():
        visual_traits += "; user-selected cues: " + traits.strip()[:260]

    subject_word = "person" if subject == "person" else "pet"
    material = TEXTURES.get(texture, TEXTURES["blindbox"])
    art_style = STYLES.get(style, STYLES["big"])
    prompt = (
        "Create a newly illustrated CARTOON desktop mascot from the attached reference image. Do not output a photo, "
        "photorealistic portrait, sticker cutout, collage, or animation of the photo. Preserve the same recognizable "
        "subject: keep its particular hair silhouette/color, face outline, eyes/brows, glasses/accessories, facial hair, "
        "clothing palette, and expression. Do not invent another or generic person. Redraw these exact cues as a funny, "
        "highly exaggerated comic character; the result must clearly look illustrated, not photographic. "
        f"A {subject_word} character with a very large head and tiny body, full body visible, cute 3D toy proportions, "
        f"{art_style}\n\nHIGHEST PRIORITY — SELECTED 3D SURFACE MATERIAL: {material} "
        "Apply this exact material to the complete character, not only the background, clothing, or accessories. "
        "The material must be obvious at thumbnail size and visually different from the other material choices. "
        "The selected material overrides any conflicting material word in the art-style description; do not blend materials. "
        "Use a clear three-dimensional form, visible volume, cast shadow, and directional highlights. "
        "Carefully designed silhouette, centered single character, plain pure white background, no text, no watermark. "
        f"Visible features to preserve from reference: {visual_traits}"
    )[:2048]
    if "flux-2-klein" in IMAGE_MODEL:
        reference = vision.copy()
        reference.thumbnail((480, 480))
        ref_buffer = io.BytesIO()
        reference.save(ref_buffer, format="JPEG", quality=88, optimize=True)
        generated = _run_multipart(IMAGE_MODEL, {"prompt": prompt, "width": 1024, "height": 1024},
                                  {"input_image_0": ("reference.jpg", "image/jpeg", ref_buffer.getvalue())})
    else:
        generated = _run(IMAGE_MODEL, {"prompt": prompt, "steps": 4}, timeout=300)
    encoded = generated.get("image")
    if not encoded:
        raise HTTPException(502, "Cloudflare 生图模型没有返回图片数据。")
    try:
        sprite = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGBA")
    except Exception as exc:
        raise HTTPException(502, f"无法解码 Cloudflare 返回的图片：{exc}")

    # Remove only white pixels connected to the canvas edges; interior eye highlights stay opaque.
    for point in ((0, 0), (sprite.width - 1, 0), (0, sprite.height - 1), (sprite.width - 1, sprite.height - 1)):
        ImageDraw.floodfill(sprite, point, (255, 255, 255, 0), thresh=32)
    return {"sprite": sprite, "traits": visual_traits, "prompt": prompt}

