import base64
import hashlib
import hmac
import io
import json
import math
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

import cairosvg
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFont, ImageFilter

CANVAS_SIZE = 1024
SAFE_MARGIN = 60
OUTPUT_DIR = Path('/app/outputs')
WEBHOOK_LOG_FILE = OUTPUT_DIR / "deapi_webhooks.jsonl"
LOGO_SVG_URL = 'https://class.veterinarioemprendedor.com/images/logo.svg'

COLOR_BLUE = (0, 57, 199)
COLOR_PINK = (233, 30, 99)
COLOR_TEXT = (17, 24, 39)
COLOR_WHITE = (255, 255, 255)

app = FastAPI(title='ve-python-api')
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/outputs', StaticFiles(directory=str(OUTPUT_DIR)), name='outputs')


class GeneratePostRequest(BaseModel):
    titular: str = Field(min_length=1)
    textocorto: str = Field(min_length=1)
    cta: str = Field(min_length=1)
    layout_type: str = Field(
        pattern='^(left_text_right_image|top_text_bottom_image|image_background_card_text|hero_top_card|circle_center_info|text_image_circle_bottom|layout4|layout5|layout6|canva_v1|canva_v2|canva_v3|hero_top_card_v1|hero_top_card_v2|hero_top_card_v3|circle_center_info_v1|circle_center_info_v2|circle_center_info_v3|text_image_circle_bottom_v1|text_image_circle_bottom_v2|text_image_circle_bottom_v3|friend_bg_v1)$'
    )
    image_base64: str = Field(min_length=16)
    seed: str | None = Field(default=None)
    color1: str = Field(default="#E32160")
    color2: str = Field(default="#4521E3")


@app.get('/')
def root() -> dict[str, str]:
    return {'status': 'ok', 'service': 'python-api'}


@app.get('/health')
def health() -> dict[str, str]:
    return {'status': 'healthy'}


@app.get('/outputs-index')
def outputs_index() -> dict[str, list[dict[str, str]]]:
    items: list[dict[str, str]] = []
    for p in sorted(OUTPUT_DIR.glob("post_*.png"), key=lambda x: x.stat().st_mtime, reverse=True):
        name = p.name
        date_group = "sin_fecha"
        m = re.match(r"^post_(\d{8})_(\d{6})_\d+\.png$", name)
        if m:
            d = m.group(1)
            date_group = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
        items.append(
            {
                "filename": name,
                "public_path": f"/outputs/{name}",
                "date": date_group,
                "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
            }
        )
    return {"items": items}


def _verify_deapi_webhook(headers: dict[str, str], raw_body: bytes) -> bool:
    secret = os.getenv("DEAPI_WEBHOOK_SECRET", "").strip()
    if not secret:
        # Modo pruebas: si no hay secreto configurado, aceptar webhook sin verificación.
        return True

    signature = headers.get("x-deapi-signature", "")
    timestamp = headers.get("x-deapi-timestamp", "")
    if not signature or not timestamp:
        return False

    try:
        now = int(time.time())
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(now - ts) > 300:
        return False

    message = f"{timestamp}.{raw_body.decode('utf-8', errors='strict')}"
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


@app.post('/webhook/deapi')
async def deapi_webhook(request: Request) -> dict[str, str]:
    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    if not _verify_deapi_webhook(headers, raw_body):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    try:
        payload = await request.json()
    except Exception:
        payload = {"raw": raw_body.decode("utf-8", errors="replace")}

    entry = {
        "received_at": datetime.utcnow().isoformat() + "Z",
        "headers": {
            "x-deapi-event": headers.get("x-deapi-event", ""),
            "x-deapi-delivery-id": headers.get("x-deapi-delivery-id", ""),
            "x-deapi-timestamp": headers.get("x-deapi-timestamp", ""),
        },
        "payload": payload,
    }
    with WEBHOOK_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {"status": "ok"}


def _font_path(bold: bool) -> list[str]:
    if bold:
        return [
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            'C:/Windows/Fonts/arialbd.ttf',
            'DejaVuSans-Bold.ttf',
        ]
    return [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        'C:/Windows/Fonts/arial.ttf',
        'DejaVuSans.ttf',
    ]


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in _font_path(bold):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    raise RuntimeError('No se encontr? una fuente TrueType escalable (DejaVu/Arial).')


def _decode_image(image_base64: str) -> Image.Image:
    try:
        payload = image_base64.strip()
        if ',' in payload and payload.lower().startswith('data:image'):
            payload = payload.split(',', 1)[1]
        image_bytes = base64.b64decode(payload, validate=True)
        return Image.open(io.BytesIO(image_bytes)).convert('RGB')
    except Exception as exc:
        raise HTTPException(status_code=400, detail='image_base64 invalido') from exc


def _cover_image(image: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_w, src_h = image.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h

    if src_ratio > target_ratio:
        new_h = target_h
        new_w = int(new_h * src_ratio)
    else:
        new_w = target_w
        new_h = int(new_w / src_ratio)

    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return ['']

    lines: list[str] = []
    current = ''
    for word in words:
        trial = word if not current else f'{current} {word}'
        bbox = draw.textbbox((0, 0), trial, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return _rebalance_orphans(draw, lines, font, max_width)


def _rebalance_orphans(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    orphan_words = {"en", "de", "y", "a", "o"}
    if len(lines) < 2:
        return lines

    for i in range(1, len(lines)):
        words = lines[i].split()
        if len(words) != 1:
            continue
        lone = words[0].lower()
        if len(lone) > 3 and lone not in orphan_words:
            continue

        prev_words = lines[i - 1].split()
        if len(prev_words) < 2:
            continue

        candidate = f"{prev_words[-1]} {lines[i]}"
        cand_w = draw.textbbox((0, 0), candidate, font=font)[2]
        if cand_w <= max_width:
            lines[i] = candidate
            lines[i - 1] = " ".join(prev_words[:-1])

    return [ln for ln in lines if ln.strip()]


def _measure_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    line_spacing: int,
) -> tuple[int, int, int]:
    if not lines:
        return 0, 0, 0
    line_h = draw.textbbox((0, 0), "Ag", font=font)[3]
    max_w = 0
    for ln in lines:
        bb = draw.textbbox((0, 0), ln, font=font)
        max_w = max(max_w, bb[2] - bb[0])
    total_h = len(lines) * line_h + max(0, len(lines) - 1) * line_spacing
    return max_w, total_h, line_h


def _truncate_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    candidate = text.strip()
    if not candidate:
        return candidate
    if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
        return candidate
    suffix = "..."
    words = candidate.split()
    while words:
        trial = " ".join(words).rstrip(" .,") + suffix
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
            return trial
        words.pop()
    return suffix


def _fit_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    bold: bool,
    base_size: int,
    min_size: int,
    max_width: int,
    max_height: int,
    line_spacing: int,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    font_size = base_size
    while font_size >= min_size:
        font = _load_font(font_size, bold=bold)
        lines = _wrap_lines(draw, text, font, max_width)
        line_height = draw.textbbox((0, 0), 'Ag', font=font)[3]
        block_height = len(lines) * line_height + max(0, len(lines) - 1) * line_spacing
        max_line_width = 0
        for ln in lines:
            bb = draw.textbbox((0, 0), ln, font=font)
            max_line_width = max(max_line_width, bb[2] - bb[0])

        if block_height <= max_height and max_line_width <= max_width:
            return font, lines, line_height

        font_size -= 2

    font = _load_font(min_size, bold=bold)
    lines = _wrap_lines(draw, text, font, max_width)
    line_height = draw.textbbox((0, 0), 'Ag', font=font)[3]
    return font, lines, line_height


def _fit_text_block_strict(
    draw: ImageDraw.ImageDraw,
    text: str,
    bold: bool,
    base_size: int,
    min_size: int,
    max_width: int,
    max_height: int,
    line_spacing: int,
    max_lines: int,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    font_size = base_size
    while font_size >= min_size:
        font = _load_font(font_size, bold=bold)
        lines = _wrap_lines(draw, text, font, max_width)
        if len(lines) > max_lines:
            head = lines[: max_lines - 1]
            tail = " ".join(lines[max_lines - 1 :])
            lines = head + [_truncate_to_width(draw, tail, font, max_width)]
        max_w, total_h, line_h = _measure_lines(draw, lines, font, line_spacing)
        if max_w <= max_width and total_h <= max_height and len(lines) <= max_lines:
            return font, lines, line_h
        font_size -= 2

    font = _load_font(min_size, bold=bold)
    lines = _wrap_lines(draw, text, font, max_width)
    if len(lines) > max_lines:
        head = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1 :])
        lines = head + [_truncate_to_width(draw, tail, font, max_width)]
    _, _, line_h = _measure_lines(draw, lines, font, line_spacing)
    return font, lines, line_h


def _cleanup_title(text: str) -> str:
    cleaned = re.sub(r"[¡!¿?]+", "", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _fit_headline_smart(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
) -> tuple[ImageFont.FreeTypeFont, list[str], int, int]:
    max_lines = 3
    min_font_size = 24
    max_font_size = 72

    source = text.strip()
    for font_size in range(58, min_font_size - 1, -2):
        font = _load_font(font_size, bold=True)
        line_spacing = max(8, int(font_size * 0.12))
        lines = _wrap_lines(draw, source, font, max_width)
        max_w, block_h, line_h = _measure_lines(draw, lines, font, line_spacing)
        if 1 <= len(lines) <= max_lines and block_h <= max_height and max_w <= max_width:
            return font, lines, line_h, line_spacing

    source = _cleanup_title(source)
    for font_size in range(56, min_font_size - 1, -2):
        font = _load_font(font_size, bold=True)
        line_spacing = max(8, int(font_size * 0.12))
        lines = _wrap_lines(draw, source, font, max_width)
        max_w, block_h, line_h = _measure_lines(draw, lines, font, line_spacing)
        if 1 <= len(lines) <= max_lines and block_h <= max_height and max_w <= max_width:
            return font, lines, line_h, line_spacing

    font = _load_font(min_font_size, bold=True)
    raw = _wrap_lines(draw, source, font, max_width)
    lines = raw[:max_lines]
    line_spacing = max(8, int(min_font_size * 0.12))
    _, _, line_h = _measure_lines(draw, lines, font, line_spacing)
    return font, lines, line_h, line_spacing


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    x: int,
    y: int,
    color: tuple[int, int, int],
    line_spacing: int,
) -> int:
    cur_y = y
    line_h = draw.textbbox((0, 0), 'Ag', font=font)[3]
    for ln in lines:
        # Subtle shadow for readability on bright backgrounds
        draw.text((x + 1, cur_y + 1), ln, font=font, fill=(0, 0, 0, 45))
        draw.text((x, cur_y), ln, font=font, fill=color)
        cur_y += line_h + line_spacing
    return cur_y


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    val = (hex_color or "").strip().lstrip("#")
    if len(val) != 6:
        return (227, 33, 96)
    try:
        return tuple(int(val[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (227, 33, 96)


def _hash_seed(text: str) -> int:
    h = 0
    for ch in text:
        h = ((h << 5) - h) + ord(ch)
        h &= 0xFFFFFFFF
    return abs(h)


def _resolve_seed(seed: str | None) -> str:
    if seed and seed.strip():
        return seed.strip()
    now = datetime.now()
    # seed única por render, basada en fecha/hora y milisegundos
    return now.strftime("auto_%Y%m%d_%H%M%S_%f")


def _rand_color(rng: random.Random, rgb: tuple[int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return (rgb[0], rgb[1], rgb[2], alpha)


def _draw_mixed_texture(
    d: ImageDraw.ImageDraw,
    rng: random.Random,
    canvas_w: int,
    canvas_h: int,
    rgb: tuple[int, int, int],
    alpha: int,
    max_count: int,
) -> None:
    count = 1 if max_count == 1 else rng.randint(2, max_count)
    for _ in range(count):
        x = rng.uniform(0, canvas_w)
        y = rng.uniform(0, canvas_h)
        size = rng.uniform(45, 180)
        shape_type = rng.random()
        color = _rand_color(rng, rgb, alpha)
        if shape_type < 0.35:
            d.ellipse((x - size / 2, y - size / 2, x + size / 2, y + size / 2), fill=color)
        elif shape_type < 0.7:
            w = size
            h = size * 0.7
            d.rounded_rectangle((x - w / 2, y - h / 2, x + w / 2, y + h / 2), radius=12, fill=color)
        else:
            pts = [
                (x, y - size / 2),
                (x + size / 2, y),
                (x, y + size / 2),
                (x - size / 2, y),
            ]
            d.polygon(pts, fill=color)


def _draw_solid_ribbon(
    d: ImageDraw.ImageDraw,
    rng: random.Random,
    canvas_w: int,
    canvas_h: int,
    rgb: tuple[int, int, int],
    vertical_pos: float,
    offset: int,
) -> None:
    base_h = canvas_h * vertical_pos
    freq = rng.uniform(0.003, 0.007)
    amp = rng.uniform(25, 50)
    phase = rng.uniform(0, 6.28318) + offset
    thick = rng.uniform(15, 35) if rng.random() > 0.5 else rng.uniform(65, 115)
    top_pts: list[tuple[float, float]] = []
    bot_pts: list[tuple[float, float]] = []
    for x in range(-20, canvas_w + 25, 5):
        y1 = base_h + (math.sin(x * freq + phase) * amp)
        y2 = y1 + thick
        top_pts.append((x, y1))
        bot_pts.append((x, y2))
    poly = top_pts + list(reversed(bot_pts))
    d.polygon(poly, fill=(rgb[0], rgb[1], rgb[2], 255))


def _draw_friend_background(
    canvas: Image.Image,
    seed: str | None,
    color1: str,
    color2: str,
) -> None:
    resolved_seed = _resolve_seed(seed)
    rng = random.Random(_hash_seed(resolved_seed))
    vet_rose = _hex_to_rgb(color1)
    vet_blue = _hex_to_rgb(color2)
    vet_blue_soft = (
        min(255, int(vet_blue[0] * 0.55 + 100)),
        min(255, int(vet_blue[1] * 0.55 + 100)),
        min(255, int(vet_blue[2] * 0.55 + 100)),
    )
    overlay = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (255, 255, 255, 255))
    d = ImageDraw.Draw(overlay, "RGBA")

    # 1) Contrast corners
    is_blue_top = rng.random() > 0.5
    corner_a = vet_blue if is_blue_top else vet_rose
    corner_b = vet_rose if is_blue_top else vet_blue
    r1 = rng.uniform(15, 25)
    x1, y1 = rng.uniform(30, 60), rng.uniform(30, 60)
    d.ellipse((x1 - r1, y1 - r1, x1 + r1, y1 + r1), fill=corner_a + (120,))
    r2 = rng.uniform(15, 25)
    x2, y2 = CANVAS_SIZE - rng.uniform(30, 60), CANVAS_SIZE - rng.uniform(30, 60)
    d.ellipse((x2 - r2, y2 - r2, x2 + r2, y2 + r2), fill=corner_b + (120,))

    # 2) Atmospheric blob
    blob_color = vet_blue_soft if rng.random() > 0.5 else vet_rose
    blob_size = CANVAS_SIZE * rng.uniform(0.30, 0.40)
    bx = CANVAS_SIZE * rng.uniform(0.60, 0.88)
    by = CANVAS_SIZE * rng.uniform(0.35, 0.65)
    blob_points: list[tuple[float, float]] = []
    angle = 0.0
    while angle < math.tau:
        r = blob_size * rng.uniform(0.85, 1.15)
        blob_points.append((bx + r * math.cos(angle), by + r * math.sin(angle)))
        angle += 0.7
    d.polygon(blob_points, fill=blob_color + (22,))

    # 3) Accent element + ribbon with same chroma
    accent = vet_blue if rng.random() > 0.5 else vet_rose
    has_bar = rng.random() > 0.7
    has_ribbon = rng.random() > 0.3
    wave_on_top = rng.random() > 0.5
    if has_bar:
        y = CANVAS_SIZE * (0.94 if wave_on_top else 0.06)
        d.rectangle((CANVAS_SIZE * 0.58, y, CANVAS_SIZE, y + CANVAS_SIZE * 0.012), fill=accent + (255,))
        if has_ribbon:
            pts = []
            y0 = CANVAS_SIZE * (0.09 if wave_on_top else 0.91)
            for x in range(int(CANVAS_SIZE * 0.55), CANVAS_SIZE + 1, 12):
                pts.append((x, y0 + math.sin(x * 0.06) * 6))
            if len(pts) > 1:
                d.line(pts, fill=accent + (150,), width=2)
    else:
        x1, y1 = CANVAS_SIZE * rng.uniform(0.5, 0.65), -60
        x2, y2 = CANVAS_SIZE + 60, CANVAS_SIZE * rng.uniform(0.3, 0.7)
        c1 = (bx - blob_size * 1.4, by - blob_size * 0.2)
        c2 = (bx + blob_size * 0.3, by + blob_size * 1.4)
        d.line([ (x1, y1), c1, c2, (x2, y2) ], fill=accent + (255,), width=2, joint="curve")
        if has_ribbon:
            d.line([ (CANVAS_SIZE * 0.5, -20), (bx - blob_size, by + 30), (bx, by + blob_size), (CANVAS_SIZE + 20, CANVAS_SIZE * 0.5) ], fill=accent + (150,), width=2, joint="curve")

    # 4) Dot grid
    if rng.random() > 0.15:
        size = rng.randint(3, 6)
        spacing = rng.uniform(13, 17)
        dot_max_r = rng.uniform(2.5, 4)
        x_start = CANVAS_SIZE * rng.uniform(0.06, 0.14)
        y_start = CANVAS_SIZE * (rng.uniform(0.1, 0.22) if rng.random() > 0.5 else rng.uniform(0.7, 0.82))
        center_idx = (size - 1) / 2
        max_dist = math.dist((0, 0), (center_idx, center_idx))
        for i in range(size):
            for j in range(size):
                dist = math.dist((i, j), (center_idx, center_idx))
                intensity = 1 - (dist / max_dist) if max_dist else 1
                alpha = int(40 + (200 * max(0.2, intensity)))
                r = dot_max_r * max(0.2, intensity)
                cx = x_start + i * spacing
                cy = y_start + j * spacing
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=vet_blue + (min(240, alpha),))

    # 5) Main rose wave anchor
    base_y = CANVAS_SIZE * (0.05 if wave_on_top else 0.95)
    freq = rng.uniform(0.003, 0.005)
    amp = rng.uniform(16, 24)
    rose_dark = (136, 14, 79)
    for i in (1, 0):
        t = i / 2
        fill = (
            int(vet_rose[0] * (1 - t) + rose_dark[0] * t),
            int(vet_rose[1] * (1 - t) + rose_dark[1] * t),
            int(vet_rose[2] * (1 - t) + rose_dark[2] * t),
            255,
        )
        offset = i * 16
        if wave_on_top:
            offset = -offset
        poly: list[tuple[float, float]] = []
        for x in range(-40, CANVAS_SIZE + 41, 60):
            poly.append((x, base_y + offset + math.sin(x * freq) * amp))
        border_y = -100 if wave_on_top else CANVAS_SIZE + 100
        poly.append((CANVAS_SIZE + 40, border_y))
        poly.append((-40, border_y))
        d.polygon(poly, fill=fill)

    canvas.alpha_composite(overlay)


def _create_marketing_background(canvas: Image.Image) -> None:
    overlay = Image.new('RGBA', (CANVAS_SIZE, CANVAS_SIZE), (255, 255, 255, 0))
    d = ImageDraw.Draw(overlay)

    # Strong brand bands
    d.pieslice((-220, -260, CANVAS_SIZE + 220, 360), start=0, end=180, fill=(0, 75, 239, 235))
    d.pieslice((CANVAS_SIZE - 420, 180, CANVAS_SIZE + 300, CANVAS_SIZE + 220), start=240, end=120, fill=(237, 20, 91, 225))
    d.rectangle((0, CANVAS_SIZE - 120, CANVAS_SIZE, CANVAS_SIZE), fill=(0, 75, 239, 228))

    for _ in range(6):
        radius = random.randint(80, 220)
        cx = random.randint(-40, CANVAS_SIZE + 40)
        cy = random.randint(-40, CANVAS_SIZE + 40)
        color = (0, 87, 255, random.randint(22, 55)) if random.random() < 0.5 else (237, 20, 91, random.randint(22, 55))
        d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)

    for _ in range(3):
        x = random.randint(0, CANVAS_SIZE)
        y = random.randint(0, CANVAS_SIZE)
        w = random.randint(140, 240)
        h = random.randint(100, 180)
        color = (30, 78, 216, 24) if random.random() < 0.5 else (233, 30, 99, 24)
        d.rounded_rectangle((x - w, y - h, x + w, y + h), radius=60, fill=color)

    canvas.alpha_composite(overlay)


def _paste_with_radius(base: Image.Image, content: Image.Image, box: tuple[int, int, int, int], radius: int) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    covered = _cover_image(content, w, h).convert('RGBA')
    mask = Image.new('L', (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
    layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    layer.paste(covered, (0, 0), mask)
    base.alpha_composite(layer, (x0, y0))


def _draw_card_with_shadow(
    base: Image.Image,
    box: tuple[int, int, int, int],
    radius: int = 28,
    card_alpha: int = 255,
) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0

    shadow = Image.new('RGBA', (w + 40, h + 40), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((20, 20, w + 20, h + 20), radius=radius, fill=(0, 0, 0, 26))
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    base.alpha_composite(shadow, (x0 - 20, y0 - 16))

    card = Image.new('RGBA', (w, h), (255, 255, 255, card_alpha))
    mask = Image.new('L', (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
    layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    layer.paste(card, (0, 0), mask)
    base.alpha_composite(layer, (x0, y0))


def _draw_cta(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, max_width: int, color: tuple[int, int, int]) -> int:
    text = text.strip()
    max_lines = 1 if len(text) <= 14 else 2
    btn_w = max(260, min(340, max_width))
    content_w = btn_w - 56

    chosen_font = _load_font(30, bold=True)
    chosen_lines = [text]
    chosen_lh = draw.textbbox((0, 0), "Ag", font=chosen_font)[3]
    line_spacing = 6

    for fs in range(36, 17, -2):
        font = _load_font(fs, bold=True)
        lines = _wrap_lines(draw, text, font, content_w)
        mw, _, lh = _measure_lines(draw, lines, font, line_spacing)
        if mw <= content_w and len(lines) <= max_lines:
            chosen_font, chosen_lines, chosen_lh = font, lines, lh
            break
    else:
        fallback_font = _load_font(18, bold=True)
        chosen_lines = _wrap_lines(draw, text, fallback_font, content_w)
        if len(chosen_lines) > 3:
            head = chosen_lines[:2]
            tail = " ".join(chosen_lines[2:])
            chosen_lines = head + _wrap_lines(draw, tail, fallback_font, content_w)
            chosen_lines = chosen_lines[:3]
        chosen_font = fallback_font
        chosen_lh = draw.textbbox((0, 0), "Ag", font=chosen_font)[3]

    text_h = len(chosen_lines) * chosen_lh + max(0, len(chosen_lines) - 1) * line_spacing
    btn_h = max(80, min(108, text_h + 34))

    draw.rounded_rectangle((x, y, x + btn_w, y + btn_h), radius=btn_h // 2, fill=color)

    tx = x + (btn_w // 2)
    ty = y + (btn_h - text_h) // 2
    cur = ty
    for ln in chosen_lines:
        bb = draw.textbbox((0, 0), ln, font=chosen_font)
        tw = bb[2] - bb[0]
        draw.text((tx - tw // 2, cur), ln, font=chosen_font, fill=COLOR_WHITE)
        cur += chosen_lh + line_spacing

    return y + btn_h


def _load_logo() -> Image.Image | None:
    try:
        resp = requests.get(
            LOGO_SVG_URL,
            timeout=12,
            headers={"User-Agent": "ve-post-generator/1.0"},
        )
        resp.raise_for_status()
        svg_text = resp.content.decode("utf-8", errors="ignore")
        inner = re.search(r"(<svg[^>]*id=[\"']ve[\"'][\s\S]*?</svg>)", svg_text, flags=re.IGNORECASE)
        if inner:
            svg_bytes = inner.group(1).encode("utf-8")
        else:
            svg_bytes = resp.content
        png_bytes = cairosvg.svg2png(bytestring=svg_bytes, output_width=int(CANVAS_SIZE * 0.13))
        logo = Image.open(io.BytesIO(png_bytes)).convert('RGBA')
        return logo
    except Exception:
        return None


def _place_logo(canvas: Image.Image, logo: Image.Image | None, position: str = "bottom_left") -> None:
    if logo is None:
        return
    max_w = int(CANVAS_SIZE * 0.13)
    ratio = max_w / logo.width
    nh = int(logo.height * ratio)
    resized = logo.resize((max_w, nh), Image.Resampling.LANCZOS)
    pad_x = 14
    pad_y = 12
    capsule_w = max_w + pad_x * 2
    capsule_h = nh + pad_y * 2
    if position == "bottom_right":
        x = CANVAS_SIZE - 40 - capsule_w
        y = CANVAS_SIZE - 40 - capsule_h
    elif position == "top_right":
        x = CANVAS_SIZE - 40 - capsule_w
        y = 40
    elif position == "top_left":
        x = 40
        y = 40
    else:
        x = 40
        y = CANVAS_SIZE - 40 - capsule_h
    overlay = Image.new("RGBA", (capsule_w, capsule_h), (255, 255, 255, 215))
    mask = Image.new("L", (capsule_w, capsule_h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, capsule_w, capsule_h), radius=20, fill=255)
    layer = Image.new("RGBA", (capsule_w, capsule_h), (0, 0, 0, 0))
    layer.paste(overlay, (0, 0), mask)
    canvas.alpha_composite(layer, (x, y))
    canvas.alpha_composite(resized, (x + pad_x, y + pad_y))


def _draw_badge(draw: ImageDraw.ImageDraw, x: int, y: int, text: str = "Curso") -> int:
    font = _load_font(24, bold=True)
    bb = draw.textbbox((0, 0), text, font=font)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]
    px = 26
    py = 10
    bw = tw + px * 2
    bh = max(44, th + py * 2)
    draw.rounded_rectangle((x, y, x + bw, y + bh), radius=bh // 2, fill=COLOR_PINK)
    draw.text((x + (bw - tw) // 2, y + (bh - th) // 2 - 1), text, font=font, fill=COLOR_WHITE)
    return y + bh


def _draw_waves_background(canvas: Image.Image) -> None:
    ov = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (255, 255, 255, 0))
    d = ImageDraw.Draw(ov)
    d.pieslice((-280, -160, 620, 560), 20, 200, fill=(0, 87, 255, 168))
    d.pieslice((CANVAS_SIZE - 560, -220, CANVAS_SIZE + 260, 500), 350, 170, fill=(233, 30, 99, 168))
    d.pieslice((-220, CANVAS_SIZE - 360, 560, CANVAS_SIZE + 260), 190, 20, fill=(0, 87, 255, 96))
    d.pieslice((CANVAS_SIZE - 520, CANVAS_SIZE - 420, CANVAS_SIZE + 220, CANVAS_SIZE + 180), 160, 330, fill=(233, 30, 99, 96))
    canvas.alpha_composite(ov)


def _draw_icon(draw: ImageDraw.ImageDraw, kind: str, x: int, y: int, size: int, color: tuple[int, int, int]) -> None:
    s = size
    if kind == "book":
        draw.rounded_rectangle((x, y, x + s, y + s), radius=max(4, s // 10), outline=color, width=max(2, s // 12))
        draw.line((x + s // 2, y + 4, x + s // 2, y + s - 4), fill=color, width=max(2, s // 14))
    elif kind == "bell":
        draw.arc((x + s * 0.2, y + s * 0.15, x + s * 0.8, y + s * 0.8), 200, 340, fill=color, width=max(2, s // 10))
        draw.line((x + s * 0.25, y + s * 0.7, x + s * 0.75, y + s * 0.7), fill=color, width=max(2, s // 10))
        draw.ellipse((x + s * 0.45, y + s * 0.74, x + s * 0.55, y + s * 0.84), fill=color)
    elif kind == "share":
        r = max(2, s // 9)
        p1 = (x + s * 0.2, y + s * 0.5)
        p2 = (x + s * 0.52, y + s * 0.25)
        p3 = (x + s * 0.8, y + s * 0.62)
        draw.line((p1, p2), fill=color, width=max(2, s // 12))
        draw.line((p2, p3), fill=color, width=max(2, s // 12))
        for px, py in (p1, p2, p3):
            draw.ellipse((px - r, py - r, px + r, py + r), fill=color)
    elif kind == "arrows":
        font = _load_font(max(18, int(s * 0.75)), bold=True)
        draw.text((x, y - 2), ">>>", font=font, fill=color)


def _paste_circle_image(base: Image.Image, src: Image.Image, center_x: int, center_y: int, diameter: int, border: int = 10) -> None:
    border_color = COLOR_PINK
    draw = ImageDraw.Draw(base)
    draw.ellipse(
        (center_x - diameter // 2 - border, center_y - diameter // 2 - border, center_x + diameter // 2 + border, center_y + diameter // 2 + border),
        fill=border_color,
    )
    covered = _cover_image(src, diameter, diameter).convert("RGBA")
    mask = Image.new("L", (diameter, diameter), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, diameter, diameter), fill=255)
    layer = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    layer.paste(covered, (0, 0), mask)
    base.alpha_composite(layer, (center_x - diameter // 2, center_y - diameter // 2))


def _render_hero_top_card(canvas: Image.Image, draw: ImageDraw.ImageDraw, payload: GeneratePostRequest, src: Image.Image) -> None:
    full = _cover_image(src, CANVAS_SIZE, CANVAS_SIZE).convert("RGBA")
    canvas.alpha_composite(full, (0, 0))
    _apply_blue_tint_in_box(canvas, (0, 0, CANVAS_SIZE, CANVAS_SIZE), radius=0, alpha=42)

    card_box = (SAFE_MARGIN, SAFE_MARGIN, CANVAS_SIZE - SAFE_MARGIN, SAFE_MARGIN + 360)
    _draw_card_with_shadow(canvas, card_box, radius=30)
    draw.rounded_rectangle((card_box[0] + 24, card_box[1] + 34, card_box[0] + 36, card_box[1] + 280), radius=6, fill=COLOR_PINK)

    tx = card_box[0] + 58
    ty = card_box[1] + 42
    tw = card_box[2] - tx - 48
    th = 180
    title_font, title_lines, _, spacing = _fit_headline_smart(draw, payload.titular, tw, th)
    title_lines = title_lines[:3]
    ty = _draw_lines(draw, title_lines, title_font, tx, ty, COLOR_BLUE, spacing) + 18
    _draw_cta(draw, payload.cta, tx, ty, min(340, tw), COLOR_PINK)


def _render_hero_top_card_variant(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    payload: GeneratePostRequest,
    src: Image.Image,
    variant: str,
) -> None:
    full = _cover_image(src, CANVAS_SIZE, CANVAS_SIZE).convert("RGBA")
    canvas.alpha_composite(full, (0, 0))
    _apply_blue_tint_in_box(canvas, (0, 0, CANVAS_SIZE, CANVAS_SIZE), radius=0, alpha=42)

    if variant == "v1":
        card_box = (SAFE_MARGIN, SAFE_MARGIN, CANVAS_SIZE - SAFE_MARGIN, 510)
    elif variant == "v2":
        card_box = (SAFE_MARGIN + 30, SAFE_MARGIN + 20, CANVAS_SIZE - SAFE_MARGIN - 30, 500)
    else:
        card_box = (SAFE_MARGIN, SAFE_MARGIN + 35, CANVAS_SIZE - SAFE_MARGIN, 520)

    _draw_card_with_shadow(canvas, card_box, radius=30)
    draw.rounded_rectangle((card_box[0] + 24, card_box[1] + 34, card_box[0] + 36, card_box[1] + 300), radius=6, fill=COLOR_PINK)
    _draw_canva_text_block(draw, payload, card_box)


def _render_circle_center_info(canvas: Image.Image, draw: ImageDraw.ImageDraw, payload: GeneratePostRequest, src: Image.Image) -> None:
    _draw_waves_background(canvas)
    _draw_badge(draw, SAFE_MARGIN + 8, SAFE_MARGIN + 14, "Comparte")

    circle_d = 320
    _paste_circle_image(canvas, src, CANVAS_SIZE // 2, 420, circle_d, border=10)

    card_box = (SAFE_MARGIN + 70, 590, CANVAS_SIZE - SAFE_MARGIN - 70, 900)
    _draw_card_with_shadow(canvas, card_box, radius=26)
    tx = card_box[0] + 42
    ty = card_box[1] + 34
    tw = card_box[2] - card_box[0] - 84

    title_font, lines, _, sp = _fit_headline_smart(draw, payload.titular, tw, 110)
    lines = lines[:2]
    ty = _draw_lines(draw, lines, title_font, tx, ty, COLOR_BLUE, max(8, sp - 2))
    ty += 18

    sf, slines, _ = _fit_text_block_strict(draw, payload.textocorto, False, 36, 30, tw, 92, 8, 2)
    ty = _draw_lines(draw, slines, sf, tx, ty, COLOR_PINK, 8) + 18

    _draw_cta(draw, payload.cta, tx, ty, min(340, tw), COLOR_PINK)
    _draw_icon(draw, "share", card_box[2] - 54, card_box[1] + 24, 28, COLOR_BLUE)


def _render_circle_center_info_variant(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    payload: GeneratePostRequest,
    src: Image.Image,
    variant: str,
) -> None:
    _draw_friend_background(canvas, payload.seed, payload.color1, payload.color2)
    _draw_badge(draw, SAFE_MARGIN + 12, SAFE_MARGIN + 12, "Comparte")

    if variant == "v1":
        circle_d = 320
        circle_cy = 380
        card_box = (SAFE_MARGIN + 70, 560, CANVAS_SIZE - SAFE_MARGIN - 70, 930)
    elif variant == "v2":
        circle_d = 300
        circle_cy = 340
        card_box = (SAFE_MARGIN + 90, 520, CANVAS_SIZE - SAFE_MARGIN - 90, 900)
    else:
        circle_d = 340
        circle_cy = 400
        card_box = (SAFE_MARGIN + 60, 580, CANVAS_SIZE - SAFE_MARGIN - 60, 940)

    _paste_circle_image(canvas, src, CANVAS_SIZE // 2, circle_cy, circle_d, border=10)
    _draw_card_with_shadow(canvas, card_box, radius=30)
    _draw_canva_text_block(draw, payload, card_box)


def _render_text_image_circle_bottom(canvas: Image.Image, draw: ImageDraw.ImageDraw, payload: GeneratePostRequest, src: Image.Image) -> None:
    _draw_waves_background(canvas)

    card_box = (SAFE_MARGIN, SAFE_MARGIN + 30, 620, 520)
    _draw_card_with_shadow(canvas, card_box, radius=26)
    tx = card_box[0] + 42
    ty = card_box[1] + 36
    tw = card_box[2] - card_box[0] - 84

    _draw_icon(draw, "book", tx - 40, ty + 6, 30, COLOR_PINK)
    title_font, title_lines, _, spacing = _fit_headline_smart(draw, payload.titular, tw, 170)
    title_lines = title_lines[:3]
    ty = _draw_lines(draw, title_lines, title_font, tx, ty, COLOR_BLUE, spacing) + 22

    sf, slines, _ = _fit_text_block_strict(draw, payload.textocorto, False, 36, 30, tw, 118, 9, 3)
    ty = _draw_lines(draw, slines, sf, tx, ty, COLOR_PINK, 9) + 14

    _draw_cta(draw, payload.cta, tx, ty, min(340, tw), COLOR_PINK)
    _draw_icon(draw, "arrows", card_box[2] - 112, card_box[3] - 58, 34, COLOR_BLUE)

    _paste_circle_image(canvas, src, 780, 760, 320, border=10)


def _render_text_image_circle_bottom_variant(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    payload: GeneratePostRequest,
    src: Image.Image,
    variant: str,
) -> None:
    _draw_friend_background(canvas, payload.seed, payload.color1, payload.color2)

    if variant == "v1":
        card_box = (SAFE_MARGIN, SAFE_MARGIN + 10, 640, 520)
        cx, cy, d = 130, 900, 440
    elif variant == "v2":
        # Más alto para dar aire al CTA y evitar que quede pegado al borde inferior.
        card_box = (SAFE_MARGIN, SAFE_MARGIN + 40, 620, 540)
        cx, cy, d = 150, 900, 460
    else:
        card_box = (SAFE_MARGIN + 20, SAFE_MARGIN + 20, 660, 540)
        cx, cy, d = 170, 900, 430

    _draw_card_with_shadow(canvas, card_box, radius=30)
    _draw_icon(draw, "book", card_box[0] + 22, card_box[1] + 18, 30, COLOR_PINK)
    text_box = (card_box[0] + 24, card_box[1] + 8, card_box[2] - 24, card_box[3] - 16)
    _draw_canva_text_block(draw, payload, text_box)
    arrow_font = _load_font(44, bold=True)
    draw.text((SAFE_MARGIN + 8, (CANVAS_SIZE // 2) - 24), ">>>", font=arrow_font, fill=COLOR_BLUE)
    _paste_circle_image(canvas, src, cx, cy, d, border=10)


def _draw_canva_background(canvas: Image.Image) -> None:
    ov = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (255, 255, 255, 0))
    d = ImageDraw.Draw(ov)
    d.pieslice((-250, -180, 520, 480), 15, 205, fill=(0, 87, 255, 190))
    d.pieslice((CANVAS_SIZE - 520, -220, CANVAS_SIZE + 220, 460), 345, 170, fill=(233, 30, 99, 182))
    d.pieslice((-220, CANVAS_SIZE - 360, 580, CANVAS_SIZE + 260), 190, 18, fill=(0, 87, 255, 160))
    d.rounded_rectangle((0, 0, CANVAS_SIZE, 90), radius=0, fill=(0, 87, 255, 210))
    d.pieslice((CANVAS_SIZE - 420, CANVAS_SIZE - 320, CANVAS_SIZE + 180, CANVAS_SIZE + 180), 150, 340, fill=(233, 30, 99, 170))
    canvas.alpha_composite(ov)


def _draw_canva_text_block(
    draw: ImageDraw.ImageDraw,
    payload: GeneratePostRequest,
    box: tuple[int, int, int, int],
) -> int:
    x0, y0, x1, y1 = box
    pad = 48
    tx = x0 + pad
    ty = y0 + pad
    tw = (x1 - x0) - pad * 2
    th = (y1 - y0) - pad * 2

    title_font, title_lines, _, title_spacing = _fit_headline_smart(draw, payload.titular, tw, int(th * 0.34))
    split_idx = max(1, len(title_lines) - 1)
    ty = _draw_lines(draw, title_lines[:split_idx], title_font, tx, ty, COLOR_BLUE, title_spacing)
    ty = _draw_lines(draw, title_lines[split_idx:], title_font, tx, ty, COLOR_PINK, title_spacing)
    ty += 24

    body_font, body_lines, _ = _fit_text_block_strict(
        draw, payload.textocorto, False, 34, 28, tw, int(th * 0.20), 8, 2
    )
    ty = _draw_lines(draw, body_lines, body_font, tx, ty, COLOR_TEXT, 8)
    ty += 26

    cta_max_w = min(340, tw)
    cta_x = x0 + max(0, ((x1 - x0) - cta_max_w) // 2)
    _draw_cta(draw, payload.cta, cta_x, ty, cta_max_w, COLOR_PINK)
    return ty


def _apply_blue_tint_in_box(
    base: Image.Image,
    box: tuple[int, int, int, int],
    radius: int = 24,
    alpha: int = 46,
) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    tint = Image.new("RGBA", (w, h), (37, 99, 235, alpha))
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.paste(tint, (0, 0), mask)
    base.alpha_composite(layer, (x0, y0))


def _render_canva_variant(canvas: Image.Image, draw: ImageDraw.ImageDraw, payload: GeneratePostRequest, src: Image.Image, variant: str) -> None:
    _draw_friend_background(canvas, payload.seed, payload.color1, payload.color2)

    if variant == "v1":
        card_box = (SAFE_MARGIN, 90, CANVAS_SIZE - SAFE_MARGIN, 560)
        image_box = (SAFE_MARGIN, 600, CANVAS_SIZE - SAFE_MARGIN, CANVAS_SIZE - 70)
    elif variant == "v2":
        card_box = (SAFE_MARGIN, 70, CANVAS_SIZE - SAFE_MARGIN, 500)
        image_box = (SAFE_MARGIN + 60, 540, CANVAS_SIZE - SAFE_MARGIN - 60, CANVAS_SIZE - 90)
    else:
        card_box = (SAFE_MARGIN, 120, CANVAS_SIZE - SAFE_MARGIN, 540)
        image_box = (SAFE_MARGIN, 570, CANVAS_SIZE - SAFE_MARGIN, CANVAS_SIZE - 70)

    _draw_card_with_shadow(canvas, card_box, radius=30, card_alpha=236)
    _draw_canva_text_block(draw, payload, card_box)

    border = 8
    draw.rounded_rectangle(
        (image_box[0] - border, image_box[1] - border, image_box[2] + border, image_box[3] + border),
        radius=28,
        fill=COLOR_PINK,
    )
    _paste_with_radius(canvas, src, image_box, radius=24)
    _apply_blue_tint_in_box(canvas, image_box, radius=24, alpha=48)


def _render_friend_bg_v1(canvas: Image.Image, draw: ImageDraw.ImageDraw, payload: GeneratePostRequest, src: Image.Image) -> None:
    _draw_friend_background(canvas, payload.seed, payload.color1, payload.color2)
    card_box = (SAFE_MARGIN, 90, CANVAS_SIZE - SAFE_MARGIN, 560)
    image_box = (SAFE_MARGIN, 600, CANVAS_SIZE - SAFE_MARGIN, CANVAS_SIZE - 70)
    _draw_card_with_shadow(canvas, card_box, radius=30)
    _draw_canva_text_block(draw, payload, card_box)
    border = 8
    draw.rounded_rectangle(
        (image_box[0] - border, image_box[1] - border, image_box[2] + border, image_box[3] + border),
        radius=28,
        fill=COLOR_PINK,
    )
    _paste_with_radius(canvas, src, image_box, radius=24)


def _render_left_text_right_image(canvas: Image.Image, draw: ImageDraw.ImageDraw, payload: GeneratePostRequest, src: Image.Image) -> None:
    # Proporciones estables de marca
    left = SAFE_MARGIN
    top = SAFE_MARGIN
    card_w = 420
    card_h = 860
    gap = 56
    img_w = 420
    img_h = 880
    card_y = (CANVAS_SIZE - card_h) // 2
    img_y = (CANVAS_SIZE - img_h) // 2

    card_box = (left, card_y, left + card_w, card_y + card_h)
    image_zone = (card_box[2] + gap, img_y, card_box[2] + gap + img_w, img_y + img_h)

    border = 10
    draw.rounded_rectangle((image_zone[0] - border, image_zone[1] - border, image_zone[2] + border, image_zone[3] + border), radius=34, fill=COLOR_PINK)
    _paste_with_radius(canvas, src, image_zone, radius=30)

    # Tarjeta de texto + sombra
    _draw_card_with_shadow(canvas, card_box, radius=26)

    inner_pad = 60
    tx = card_box[0] + inner_pad
    ty = card_box[1] + inner_pad + 6
    tw = (card_box[2] - card_box[0]) - inner_pad * 2
    th = (card_box[3] - card_box[1]) - inner_pad * 2

    ty = _draw_badge(draw, tx, ty, "Curso") + 28

    title_font, title_lines, _, title_spacing = _fit_headline_smart(draw, payload.titular, tw, int(th * 0.36))
    split_idx = max(1, len(title_lines) - 1)
    ty = _draw_lines(draw, title_lines[:split_idx], title_font, tx, ty, COLOR_BLUE, title_spacing)
    ty = _draw_lines(draw, title_lines[split_idx:], title_font, tx, ty, COLOR_PINK, title_spacing)
    ty += 36

    body_font, body_lines, _ = _fit_text_block(
        draw,
        payload.textocorto,
        bold=False,
        base_size=38,
        min_size=34,
        max_width=tw,
        max_height=int(th * 0.2),
        line_spacing=10,
    )
    body_lines = body_lines[:2]
    ty = _draw_lines(draw, body_lines, body_font, tx, ty, COLOR_TEXT, 8)
    ty += 38

    cta_y = min(ty, card_box[1] + int(card_h * 0.62))
    _draw_cta(draw, payload.cta, tx + (tw - 300) // 2, cta_y, 300, COLOR_PINK)


def _render_top_text_bottom_image(canvas: Image.Image, draw: ImageDraw.ImageDraw, payload: GeneratePostRequest, src: Image.Image) -> None:
    left = SAFE_MARGIN
    top = SAFE_MARGIN
    right = CANVAS_SIZE - SAFE_MARGIN
    bottom = CANVAS_SIZE - SAFE_MARGIN
    zone_h = bottom - top

    text_zone = (left, top, right, top + int(zone_h * 0.42))
    image_zone = (left, top + int(zone_h * 0.48), right, bottom)

    _draw_card_with_shadow(canvas, text_zone, radius=24)
    border = 10
    draw.rounded_rectangle((image_zone[0] - border, image_zone[1] - border, image_zone[2] + border, image_zone[3] + border), radius=34, fill=COLOR_PINK)
    _paste_with_radius(canvas, src, image_zone, radius=30)

    inner_pad = 60
    tx = text_zone[0] + inner_pad
    ty = text_zone[1] + inner_pad
    tw = (text_zone[2] - text_zone[0]) - inner_pad * 2
    th = (text_zone[3] - text_zone[1]) - inner_pad * 2

    title_font, title_lines, _, title_spacing = _fit_headline_smart(draw, payload.titular, tw, int(th * 0.45))
    title_lines = title_lines[:3]
    split_idx = max(1, len(title_lines) - 1)
    ty = _draw_lines(draw, title_lines[:split_idx], title_font, tx, ty, COLOR_BLUE, title_spacing)
    ty = _draw_lines(draw, title_lines[split_idx:], title_font, tx, ty, COLOR_PINK, title_spacing)
    ty += 22

    body_font, body_lines, _ = _fit_text_block_strict(draw, payload.textocorto, False, 36, 30, tw, int(th * 0.25), 8, 2)
    ty = _draw_lines(draw, body_lines, body_font, tx, ty, COLOR_TEXT, 8)
    ty += 24

    _draw_cta(draw, payload.cta, tx, min(ty, text_zone[3] - 130), tw, COLOR_PINK)


def _render_image_background_card_text(canvas: Image.Image, draw: ImageDraw.ImageDraw, payload: GeneratePostRequest, src: Image.Image) -> None:
    full = _cover_image(src, CANVAS_SIZE, CANVAS_SIZE).convert('RGBA')
    canvas.alpha_composite(full, (0, 0))

    card_box = (
        SAFE_MARGIN,
        int(CANVAS_SIZE * 0.52),
        CANVAS_SIZE - SAFE_MARGIN,
        CANVAS_SIZE - SAFE_MARGIN,
    )
    _draw_card_with_shadow(canvas, card_box, radius=30)

    inner_pad = 60
    tx = card_box[0] + inner_pad
    ty = card_box[1] + inner_pad
    tw = (card_box[2] - card_box[0]) - inner_pad * 2
    th = (card_box[3] - card_box[1]) - inner_pad * 2

    title_font, title_lines, _, title_spacing = _fit_headline_smart(draw, payload.titular, tw, int(th * 0.42))
    title_lines = title_lines[:3]
    split_idx = max(1, len(title_lines) - 1)
    ty = _draw_lines(draw, title_lines[:split_idx], title_font, tx, ty, COLOR_BLUE, title_spacing)
    ty = _draw_lines(draw, title_lines[split_idx:], title_font, tx, ty, COLOR_PINK, title_spacing)
    ty += 22

    body_font, body_lines, _ = _fit_text_block_strict(draw, payload.textocorto, False, 36, 30, tw, int(th * 0.25), 8, 2)
    ty = _draw_lines(draw, body_lines, body_font, tx, ty, COLOR_TEXT, 8)
    ty += 22

    _draw_cta(draw, payload.cta, tx, min(ty, card_box[3] - 130), tw, COLOR_PINK)


@app.post('/generate-post')
def generate_post(payload: GeneratePostRequest, request: Request) -> dict[str, str]:
    src = _decode_image(payload.image_base64)
    effective_seed = _resolve_seed(payload.seed)

    try:
        # valida disponibilidad de fuentes antes de renderizar
        _load_font(64, bold=True)
        _load_font(36, bold=False)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    canvas = Image.new('RGBA', (CANVAS_SIZE, CANVAS_SIZE), COLOR_WHITE + (255,))
    draw = ImageDraw.Draw(canvas)

    chosen_layout = payload.layout_type
    if chosen_layout == "layout4":
        chosen_layout = "hero_top_card"
    elif chosen_layout == "layout5":
        chosen_layout = "circle_center_info"
    elif chosen_layout == "layout6":
        chosen_layout = "text_image_circle_bottom"
    if len(payload.titular.strip()) > 45 and payload.layout_type == 'left_text_right_image':
        chosen_layout = 'top_text_bottom_image'

    hero_fullscreen_variants = {"hero_top_card_v1", "hero_top_card_v2", "hero_top_card_v3"}
    if chosen_layout not in hero_fullscreen_variants:
        _draw_friend_background(canvas, effective_seed, payload.color1, payload.color2)

    if chosen_layout == 'left_text_right_image':
        _render_left_text_right_image(canvas, draw, payload, src)
    elif chosen_layout == 'top_text_bottom_image':
        _render_top_text_bottom_image(canvas, draw, payload, src)
    elif chosen_layout == 'hero_top_card':
        _render_hero_top_card(canvas, draw, payload, src)
    elif chosen_layout == 'circle_center_info':
        _render_circle_center_info(canvas, draw, payload, src)
    elif chosen_layout == 'text_image_circle_bottom':
        _render_text_image_circle_bottom(canvas, draw, payload, src)
    elif chosen_layout == 'hero_top_card_v1':
        _render_hero_top_card_variant(canvas, draw, payload, src, "v1")
    elif chosen_layout == 'hero_top_card_v2':
        _render_hero_top_card_variant(canvas, draw, payload, src, "v2")
    elif chosen_layout == 'hero_top_card_v3':
        _render_hero_top_card_variant(canvas, draw, payload, src, "v3")
    elif chosen_layout == 'circle_center_info_v1':
        _render_circle_center_info_variant(canvas, draw, payload, src, "v1")
    elif chosen_layout == 'circle_center_info_v2':
        _render_circle_center_info_variant(canvas, draw, payload, src, "v2")
    elif chosen_layout == 'circle_center_info_v3':
        _render_circle_center_info_variant(canvas, draw, payload, src, "v3")
    elif chosen_layout == 'text_image_circle_bottom_v1':
        _render_text_image_circle_bottom_variant(canvas, draw, payload, src, "v1")
    elif chosen_layout == 'text_image_circle_bottom_v2':
        _render_text_image_circle_bottom_variant(canvas, draw, payload, src, "v2")
    elif chosen_layout == 'text_image_circle_bottom_v3':
        _render_text_image_circle_bottom_variant(canvas, draw, payload, src, "v3")
    elif chosen_layout == 'canva_v1':
        _render_canva_variant(canvas, draw, payload, src, "v1")
    elif chosen_layout == 'canva_v2':
        _render_canva_variant(canvas, draw, payload, src, "v2")
    elif chosen_layout == 'canva_v3':
        _render_canva_variant(canvas, draw, payload, src, "v3")
    elif chosen_layout == 'friend_bg_v1':
        # this renderer includes its own friend background call
        payload.seed = effective_seed
        _render_friend_bg_v1(canvas, draw, payload, src)
    else:
        _render_image_background_card_text(canvas, draw, payload, src)

    logo = _load_logo()
    if logo is None:
        raise HTTPException(status_code=502, detail="No se pudo cargar el logo SVG de marca")
    logo_position = "bottom_left"
    if chosen_layout in {"text_image_circle_bottom_v1", "text_image_circle_bottom_v2", "text_image_circle_bottom_v3"}:
        logo_position = "bottom_right"
    elif chosen_layout in {"circle_center_info_v1", "circle_center_info_v2", "circle_center_info_v3"}:
        logo_position = "top_right"
    _place_logo(canvas, logo, logo_position)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    filename = f'post_{timestamp}.png'
    out_path = OUTPUT_DIR / filename
    canvas.convert('RGB').save(out_path, format='PNG')

    public_path = f'/outputs/{filename}'
    download_url = f'{request.base_url}outputs/{filename}'

    return {
        'status': 'ok',
        'file': str(out_path),
        'filename': filename,
        'public_path': public_path,
        'download_url': download_url,
    }
