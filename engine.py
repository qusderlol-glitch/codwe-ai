"""Движок качества: effort (10 уровней / проценты), режим «фото с камеры», выбор лучшей из нескольких попыток.

Всё, что здесь, не зависит от конкретной модели картинок — модели подключаются в server.py.
"""
import io
import math
import random

import numpy as np
from PIL import Image, ImageChops, ImageFilter, PngImagePlugin

# ───────────────────────── effort ─────────────────────────
LEVELS = ["Easy", "Normal", "Hard", "Expert", "Ultra", "Mega", "Giga", "Extreme", "Insane", "Legend"]
# для каждого уровня: шаги, размер базовой картинки (мегапикселей), число попыток (лучшая из N),
# увеличение «второй проход» (1.0 = без него), сила второго прохода
_TABLE = [
    #  steps  mp    n   hires  hstr
    (6,   0.70, 1, 1.00, 0.00),   # Easy      ~ 20 с
    (8,   1.00, 1, 1.00, 0.00),   # Normal    ~ 40 с
    (8,   1.00, 2, 1.00, 0.00),   # Hard      ~ 1,5 мин
    (9,   1.00, 3, 1.00, 0.00),   # Expert    ~ 2 мин
    (9,   1.00, 2, 1.40, 0.33),   # Ultra     ~ 3 мин
    (10,  1.00, 3, 1.40, 0.33),   # Mega      ~ 4 мин
    (10,  1.00, 4, 1.50, 0.35),   # Giga      ~ 6 мин
    (12,  1.00, 4, 1.60, 0.38),   # Extreme   ~ 8 мин
    (12,  1.00, 5, 1.75, 0.38),   # Insane    ~ 10 мин
    (12,  1.00, 6, 2.00, 0.40),   # Legend    ~ 15 мин
]


def effort_params(pct):
    """pct: 0..100 (или None -> Normal). Возвращает параметры генерации и название уровня."""
    if pct is None:
        pct = 100 * 1 / 9
    pct = max(0.0, min(100.0, float(pct)))
    x = pct / 100 * 9          # 0..9
    lo = int(math.floor(x))
    hi = min(9, lo + 1)
    t = x - lo

    def lerp(i):
        return _TABLE[lo][i] + (_TABLE[hi][i] - _TABLE[lo][i]) * t

    level = int(round(x))
    return {
        "level": level, "name": LEVELS[level], "pct": round(pct),
        "steps": int(round(lerp(0))), "mp": round(lerp(1), 2),
        "n": int(round(lerp(2))) if t < 0.5 else _TABLE[hi][2] if t >= 0.5 else _TABLE[lo][2],
        "hires": round(lerp(3), 2), "hires_strength": round(lerp(4), 2),
    }


def level_to_pct(level):
    return round(max(0, min(9, level)) / 9 * 100)


# ───────────────────────── «фото с камеры» ─────────────────────────
CAMERAS = {
    "phone": {
        "title": "Смартфон",
        "prompt": ("candid unedited photo taken casually with a smartphone by an ordinary person, natural uneven daylight, "
                   "slightly off-center everyday framing, deep depth of field with no background blur, no professional "
                   "lighting, no retouching, realistic skin texture with pores, small imperfections, a bit of digital noise"),
    },
    "dslr": {
        "title": "Зеркалка",
        "prompt": ("photo taken on a DSLR camera with a 35mm lens, f/8, natural available light, flat realistic colors, "
                   "documentary snapshot, not posed, sharp but natural, slight lens vignetting"),
    },
    "film": {
        "title": "Плёнка",
        "prompt": ("35mm film photograph, Kodak Gold 200 colors, visible film grain, slightly faded blacks, soft halation "
                   "around highlights, candid family-album snapshot, imperfect framing"),
    },
    "compact": {
        "title": "Старая мыльница",
        "prompt": ("low-quality photo from an old 2006 compact digital camera with on-camera flash, harsh flat lighting, "
                   "red-eye free, slightly soft and noisy, oversaturated colors, JPEG compression artifacts, casual party snapshot"),
    },
    "cctv": {
        "title": "Камера наблюдения",
        "prompt": ("still frame from a security camera, wide-angle lens distortion, low resolution, heavy compression, "
                   "washed-out colors, grainy, high-angle view"),
    },
}


def _noise(shape, sigma, rng):
    return rng.normal(0.0, sigma, shape).astype(np.float32)


def apply_camera(img, preset="phone", seed=0, strength=1.0):
    """Имитирует съёмку на реальную камеру: шум сенсора, виньетка, хроматическая аберрация,
    мягкость объектива, цветовой оттенок, пересветы и JPEG-сжатие."""
    rng = np.random.default_rng(seed & 0xFFFFFFFF)
    s = max(0.0, min(1.5, strength))
    img = img.convert("RGB")
    w, h = img.size
    cfg = {
        #            soft  luma  chroma vign  ca    sat    contrast warm  grain jpeg  halation lift  down
        "phone":   (0.35, 2.4, 1.6, 0.14, 0.7, 1.06, 1.07, 0.020, 0.0, 84, 0.0, 0.0, 1.0),
        "dslr":    (0.20, 1.3, 0.8, 0.10, 0.5, 1.00, 1.04, 0.008, 0.0, 92, 0.0, 0.0, 1.0),
        "film":    (0.55, 0.0, 0.0, 0.16, 0.8, 0.95, 1.00, 0.030, 5.5, 90, 0.35, 10.0, 1.0),
        "compact": (0.80, 4.0, 3.0, 0.22, 1.4, 1.14, 1.12, 0.035, 0.0, 70, 0.0, 0.0, 0.75),
        "cctv":    (1.10, 5.5, 3.5, 0.24, 1.8, 0.70, 0.95, -0.010, 0.0, 52, 0.0, 6.0, 0.5),
    }[preset if preset in CAMERAS else "phone"]
    soft, luma, chroma, vign, ca, sat, contrast, warm, grain, jpeg, halo, lift, down = cfg

    # 1) пониженное «разрешение сенсора» для дешёвых камер
    if down < 1.0 and s > 0:
        k = 1.0 - (1.0 - down) * min(1.0, s)
        small = img.resize((max(64, int(w * k)), max(64, int(h * k))), Image.LANCZOS)
        img = small.resize((w, h), Image.BICUBIC)
    # 2) мягкость объектива
    if soft * s > 0.05:
        img = img.filter(ImageFilter.GaussianBlur(radius=soft * s))
    a = np.asarray(img).astype(np.float32)

    # 3) хроматическая аберрация (смещение R и B к краям)
    if ca * s > 0.05:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        cx, cy = (w - 1) / 2, (h - 1) / 2
        dx, dy = (xx - cx) / cx, (yy - cy) / cy
        r2 = dx * dx + dy * dy
        amt = ca * s

        def shift(ch, sign):
            sx = np.clip(xx - sign * amt * dx * r2, 0, w - 1)
            sy = np.clip(yy - sign * amt * dy * r2, 0, h - 1)
            x0, y0 = sx.astype(np.int32), sy.astype(np.int32)
            x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
            fx, fy = sx - x0, sy - y0
            return (ch[y0, x0] * (1 - fx) * (1 - fy) + ch[y0, x1] * fx * (1 - fy) +
                    ch[y1, x0] * (1 - fx) * fy + ch[y1, x1] * fx * fy)
        a[..., 0] = shift(a[..., 0], 1)
        a[..., 2] = shift(a[..., 2], -1)

    # 4) цвет: контраст, насыщенность, тёплый/холодный оттенок, «поднятые» чёрные (плёнка)
    mid = 128.0
    a = (a - mid) * (1 + (contrast - 1) * s) + mid
    gray = a.mean(axis=2, keepdims=True)
    a = gray + (a - gray) * (1 + (sat - 1) * s)
    a[..., 0] *= 1 + warm * s
    a[..., 2] *= 1 - warm * s
    if lift:
        a = a * (1 - lift * s / 255) + lift * s
    # 5) мягкий «пересвет» светлых мест (как у реальных камер)
    hi = np.clip((a - 215) / 40, 0, 1)
    a = a + hi * 14 * s
    # 6) свечение вокруг светлых мест (halation) — для плёнки
    if halo * s > 0:
        bright = np.clip((a - 200) / 55, 0, 1)
        glow = Image.fromarray((bright * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius=max(3, w / 120)))
        g = np.asarray(glow).astype(np.float32) / 255
        a[..., 0] += g[..., 0] * 70 * halo * s
        a[..., 1] += g[..., 1] * 28 * halo * s
        a[..., 2] += g[..., 2] * 10 * halo * s
    # 7) виньетка
    if vign * s > 0:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
        a *= (1 - vign * s * np.clip(r, 0, 1) ** 2.2)[..., None]
    # 8) шум сенсора: сильнее в тенях, цветной + яркостный
    lum = np.clip(a.mean(axis=2, keepdims=True) / 255, 0, 1)
    shade = 0.35 + 0.65 * (1 - lum)
    if luma * s > 0:
        a += _noise(a.shape[:2] + (1,), luma * s, rng) * shade
    if chroma * s > 0:
        a += _noise(a.shape, chroma * s, rng) * 0.6 * shade
    if grain * s > 0:  # зерно плёнки: монохромное + лёгкое цветное
        g = _noise(a.shape[:2] + (1,), grain * s, rng)
        gi = Image.fromarray(np.clip(g * 20 + 128, 0, 255).astype(np.uint8)[..., 0]).filter(ImageFilter.GaussianBlur(0.6))
        g = (np.asarray(gi).astype(np.float32) - 128) / 20
        a += g[..., None] * (0.55 + 0.45 * shade) + _noise(a.shape, grain * s * 0.25, rng)
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

    # 9) чуть резкости «как на телефоне» и JPEG-сжатие
    if preset == "phone":
        out = out.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(60 * s), threshold=2))
    q = int(round(100 - (100 - jpeg) * min(1.0, s))) if s > 0 else 95
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=max(30, min(95, q)), subsampling=2 if preset in ("compact", "cctv", "phone") else 1)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


# ───────────────────────── выбор лучшей попытки ─────────────────────────
def sharpness(img):
    g = np.asarray(img.convert("L").resize((512, int(512 * img.height / img.width)))).astype(np.float32)
    lap = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1]
    return float(lap.var())


def clip_score(img, prompt, clip):
    """clip = (processor, model) из safety._clf['clip']; сходство картинки с английским описанием."""
    import torch
    proc, model = clip
    with torch.inference_mode():
        inp = proc(text=[prompt[:300]], images=img.convert("RGB"), return_tensors="pt", padding=True, truncation=True)
        return float(model(**inp).logits_per_image[0, 0])


def pick_best(images, prompt, clip=None):
    """Возвращает индекс лучшей картинки: соответствие описанию + резкость (без «мыла»)."""
    if len(images) == 1:
        return 0, [0.0]
    sh = np.array([sharpness(i) for i in images])
    sh = (sh - sh.mean()) / (sh.std() + 1e-6)
    if clip is not None:
        try:
            cs = np.array([clip_score(i, prompt, clip) for i in images])
            cs = (cs - cs.mean()) / (cs.std() + 1e-6)
        except Exception:
            cs = np.zeros(len(images))
    else:
        cs = np.zeros(len(images))
    score = 0.6 * cs + 0.4 * sh
    return int(score.argmax()), [round(float(x), 2) for x in score]


# ───────────────────────── метка «создано ИИ» ─────────────────────────
def png_meta(extra=None):
    """Невидимая метка в файле: картинка создана ИИ (Codwe AI). Не удаляется приложением."""
    info = PngImagePlugin.PngInfo()
    info.add_text("Software", "Codwe AI")
    info.add_text("Description", "AI-generated image (Codwe AI)")
    info.add_text("AIGenerated", "true")
    for k, v in (extra or {}).items():
        info.add_text(k, str(v)[:1000])
    return info
