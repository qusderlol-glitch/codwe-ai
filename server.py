"""Codwe AI — локальный приватный сервер.

Слушает ТОЛЬКО 127.0.0.1 (доступен только с этого компьютера).
Запуск: start_codwe.bat  (или venv\\Scripts\\python server.py)
"""
import gc
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

HOME = Path(__file__).resolve().parent

# Если запустили системным Python — перезапускаемся в окружении venv.
_venv_dir = HOME / "venv"
if (_venv_dir / "Scripts" / "python.exe").exists() and \
        os.path.normcase(os.path.abspath(sys.prefix)) != os.path.normcase(str(_venv_dir)):
    sys.exit(subprocess.call([str(_venv_dir / "Scripts" / "python.exe"), str(Path(__file__).resolve())]
                             + sys.argv[1:], cwd=str(HOME)))

os.environ.setdefault("HF_HOME", str(HOME / "models"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import requests  # noqa: E402
from fastapi import FastAPI, File, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import safety  # noqa: E402  (безопасные лимиты, зашифрованные правила)
import workspace_api  # noqa: E402  (рабочая папка и терминал для вкладки «Код»)
import auth_api  # noqa: E402  (вход по почте, роли, баны, условия, целостность)
import codwecode_api  # noqa: E402  (агент codwecode: снимки экрана, зрение, поиск)
import engine  # noqa: E402  (effort, режим «фото с камеры», выбор лучшей попытки)

PORT = int(os.environ.get("CODWE_PORT", "8765"))
OUT = HOME / "outputs"
UPLOADS = HOME / "uploads"
WEB = HOME / "web"
for d in (OUT, UPLOADS, WEB):
    d.mkdir(exist_ok=True)

OLLAMA = "http://127.0.0.1:11434"
CTX = 8192
KEEP = "30m"
DEFAULT_CHAT_MODEL = "qwen3.5:9b"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
THINK_KEYS = ("qwen3", "gemma4", "deepseek-r1", "gpt-oss", "magistral", "phi4-reasoning")


def no_think(model):
    """У служебных запросов (промпты, переводы, названия) размышления не нужны — только скорость."""
    return {"think": False} if any(k in model.lower() for k in THINK_KEYS) else {}

CYR = re.compile(r"[а-яёА-ЯЁ]")
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

NEG_ANIME = ("nsfw, lowres, bad anatomy, bad hands, mutated hands, extra fingers, fewer fingers, fused fingers, "
             "missing fingers, too many fingers, extra limbs, deformed face, asymmetrical eyes, cross-eyed, "
             "uneven eyes, ugly, worst quality, low quality, jpeg artifacts, blurry, text, error, watermark, "
             "signature, username, extra digits, artistic error, scan, displeasing")
NEG_PHOTO = ("worst quality, low quality, blurry, out of focus, deformed, disfigured, bad anatomy, bad hands, "
             "mutated hands, poorly drawn hands, extra fingers, fewer fingers, fused fingers, missing fingers, "
             "too many fingers, extra limbs, malformed limbs, asymmetrical eyes, cross-eyed, plastic skin, "
             "cartoon, anime, 3d render, painting, illustration, text, watermark, signature, jpeg artifacts")

ASPECT_SIZES = {
    "photo": {"portrait": (832, 1216), "landscape": (1216, 832), "square": (1024, 1024)},
    "anime": {"portrait": (832, 1216), "landscape": (1216, 832), "square": (1024, 1024)},
    "draft": {"portrait": (512, 768), "landscape": (768, 512), "square": (768, 768)},
}

IMAGE_MODELS = {
    "real": dict(
        title="Фото · Z-Image Turbo", kind="photo", engine="zimage",
        desc="Самая реалистичная: кожа, шерсть, ткань, мелкие детали. Уровень effort меняет качество и время.",
        repo="Tongyi-MAI/Z-Image-Turbo", steps=8, steps_max=16, cfg=0.0, uses_negative=False,
        style="desc", negative="", suffix="",
        sizes=[(832, 1216), (1216, 832), (1024, 1024), (896, 1152)]),
    "anime": dict(
        title="Аниме · Animagine XL 3.1", kind="anime",
        desc="Аниме и арт с нормальными лицами. Около минуты.",
        repo="cagliostrolab/animagine-xl-3.1", steps=28, steps_max=50, cfg=7.0, uses_negative=True,
        style="tags", negative=NEG_ANIME, suffix=", masterpiece, best quality, very aesthetic, absurdres",
        sizes=[(832, 1216), (1216, 832), (1024, 1024), (896, 1152)]),
}
VIDEO_REPO = "stabilityai/stable-video-diffusion-img2vid-xt"
VIDEO_FORMATS = {"landscape": (768, 448), "portrait": (448, 768), "square": (576, 576)}


def _klein_ready():
    try:
        import klein_engine
        return klein_engine.ready()
    except Exception:
        return False


def model_ready(key):
    cfg = IMAGE_MODELS[key]
    if cfg.get("engine") == "zimage":
        try:
            import zimage_engine
            return zimage_engine.ready()
        except Exception:
            return False
    return repo_ready(cfg["repo"])


def repo_ready(repo):
    snap = Path(os.environ["HF_HOME"]) / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots"
    return snap.exists() and any(snap.iterdir())


# ───────────────────────── Ollama ─────────────────────────
def ollama_up():
    try:
        requests.get(OLLAMA + "/api/tags", timeout=2)
        return True
    except Exception:
        return False


def release_live():
    """Live-модель держит видеопамять — освобождаем её, когда нужна языковая модель."""
    if S.pipe_key and S.pipe_key[0] == "live" and S.lock.acquire(blocking=False):
        try:
            free_pipe()
        finally:
            S.lock.release()


def ensure_ollama():
    release_live()
    if ollama_up():
        return True
    try:
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    except Exception:
        return False
    for _ in range(40):
        time.sleep(0.5)
        if ollama_up():
            return True
    return False


def ollama_models():
    try:
        return [m["name"] for m in requests.get(OLLAMA + "/api/tags", timeout=4).json().get("models", [])]
    except Exception:
        return []


def ollama_loaded():
    try:
        return [m["name"] for m in requests.get(OLLAMA + "/api/ps", timeout=3).json().get("models", [])]
    except Exception:
        return []


def ollama_unload_all():
    for name in ollama_loaded():
        try:
            requests.post(OLLAMA + "/api/generate", json={"model": name, "keep_alive": 0}, timeout=30)
        except Exception:
            pass


def ollama_warm(model):
    requests.post(OLLAMA + "/api/generate",
                  json={"model": model, "prompt": "", "keep_alive": KEEP, "options": {"num_ctx": CTX}},
                  timeout=300)


# ───────────────────────── Состояние ─────────────────────────
class State:
    on = False
    stopping = False
    warming = False
    chat_model = DEFAULT_CHAT_MODEL
    gpu_busy = False
    last_gpu = 0.0
    pipe = None
    pipe_key = None
    lock = threading.RLock()  # держится на время задачи генерации


S = State()
JOBS = {}
JOBQ = queue.Queue()


class Cancelled(Exception):
    pass


class Blocked(Exception):
    """Запрос или результат остановлен безопасными лимитами; info объясняет причину (категория, что сработало, совет)."""

    def __init__(self, msg, info=None):
        super().__init__(msg)
        self.info = info or {}


def guard_api(*texts):
    for t in texts:
        i = safety.explain(t)
        if i:
            raise HTTPException(403, f"{i['message']} Сработало на: «{i['trigger']}». {i['advice']}")


def guard_job(*texts):
    for n, t in enumerate(texts):
        i = safety.explain(t, where="английский промпт" if n else "запрос")
        if i:
            raise Blocked(i["message"], i)


def guard_image(img, what=""):
    ok, cat, msg, sc = safety.check_image(img)
    if not ok:
        raise Blocked(msg + (f" ({what})" if what else ""), safety.image_info(cat, sc))


def free_pipe():
    if S.pipe is not None and hasattr(S.pipe, "unload"):
        try:
            S.pipe.unload()
        except Exception:
            traceback.print_exc()
    S.pipe = None
    S.pipe_key = None
    gc.collect()
    if "torch" in sys.modules:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


_gpu_cache = {"t": 0, "v": None}


def gpu_info():
    if time.time() - _gpu_cache["t"] < 1.5:
        return _gpu_cache["v"]
    v = None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, creationflags=NO_WINDOW).stdout.strip()
        name, used, total = [x.strip() for x in out.splitlines()[0].split(",")]
        v = {"name": name, "used": int(used), "total": int(total)}
    except Exception:
        pass
    _gpu_cache.update(t=time.time(), v=v)
    return v


def pick_translate_model():
    ms = ollama_models()
    if S.chat_model in ms:
        return S.chat_model
    for m in ms:
        if "coder" not in m:
            return m
    return ms[0] if ms else None


def translate_prompt(text, style):
    """Русское описание -> английский промпт для модели картинок (через локальную LLM)."""
    if not CYR.search(text):
        return text
    model = pick_translate_model()
    if not model:
        return text
    if style == "tags":
        sysmsg = ("You convert an image description (any language) into an English prompt for an anime "
                  "Stable Diffusion model using Danbooru-style tags. Output ONLY one line: a comma-separated "
                  "list of English tags, e.g. '1girl, long hair, blue eyes, school uniform, smiling, "
                  "cherry blossoms, outdoors'. Keep everything the user described. No explanations, no quotes.")
    elif len(text) > 250:  # длинное подробное описание: переводим ПОЛНОСТЬЮ, ничего не сокращая
        sysmsg = ("Translate the user's detailed image description into English as faithfully as possible. Keep EVERY "
                  "object, number, colour, material, position and style word; do not shorten, summarize or reorder. "
                  "The translation must be about as long as the original. Output ONLY the translation.")
    else:
        sysmsg = ("Translate the user's image description into a vivid English prompt (1-2 sentences) for a "
                  "photorealistic text-to-image model. Keep everything the user described. "
                  "Output ONLY the prompt, no quotes, no explanations.")
    long_mode = len(text) > 250 and style != "tags"
    try:
        r = requests.post(OLLAMA + "/api/chat", timeout=240, json={
            "model": model, "stream": False, "keep_alive": KEEP, **no_think(model),
            "options": {"temperature": 0.2, "num_ctx": CTX},
            "messages": [{"role": "system", "content": sysmsg}, {"role": "user", "content": text}]})
        out = r.json()["message"]["content"].strip().strip('"').strip()
        if not long_mode:
            out = out.splitlines()[0].strip() if out else ""
        if out and not CYR.search(out):
            return out if long_mode else out[:600]
    except Exception:
        pass
    return text


PROMPT_DIRECTOR = r"""You are the Prompt Director of a local Stable Diffusion XL studio. The user writes a short request in any language (usually Russian). You rewrite it into an excellent ENGLISH prompt plus a NEGATIVE prompt so the image/video has no typical AI artifacts (bad hands, extra fingers, uneven eyes, broken faces, merged limbs, text).

Answer with ONE JSON object and nothing else:
{"style": "anime" | "photo", "prompt": "...", "negative": "...", "motion": 40-180, "notes": "..."}
- "negative": only extra things specific to THIS request (max 25 words). Basic anti-artifact words are added automatically, do not repeat them.
- "motion": only used in MODE video (otherwise put 100).
- "notes": ONE short sentence in RUSSIAN telling the user what you improved (e.g. which style you chose and how you protected the hands).

HARD RULES
1. KEEP EVERYTHING the user asked for: subject, number of people, colours, clothes, place, mood, action. Never replace the subject. Do not add sexual or violent content the user did not ask for.
2. Meta-requests such as "no errors", "no bugs", "no glitches", "perfect", "high quality", "без ошибок", "ноль глюков", "идеально", "красиво" are NOT part of the picture. NEVER put them into "prompt". They are already covered by the automatic quality tags and the negative prompt.
3. STYLE is always "anime" or "photo": obey it.
4. HANDS ARE THE #1 SOURCE OF ERRORS. If the user did not ask for visible hands, compose the shot so the hands are hidden or trivial (hands behind back, in pockets, out of frame, upper-body or portrait framing). If hands matter (waving, holding, eating, typing), describe ONE simple, clear pose, e.g. "right hand raised, open palm facing the viewer, five fingers clearly separated". Never ask for more than two hands in the picture, never complicated finger poses, never hands overlapping the face. Keep every visible hand FULLY inside the frame, away from the image edges (a hand cut by the border looks broken, and slides out of the frame in video).
5. FACES. One clear main subject unless the user asks for more. For people add "symmetrical face, detailed eyes". Keep the face large enough and unobstructed.
6. Never use brackets, weights like (word:1.2), or the words "no"/"without" inside "prompt" (diffusion models ignore negation, so put those things in "negative" instead).
7. LITERAL MEANING FIRST. If the request is a single word, a greeting or very short, depict the most obvious literal scene: "привет"/"hello"/"hi" = a friendly person waving hello (the waving hand is REQUIRED, so rule 4 applies as "describe one simple clear pose"); "спасибо" = a person smiling with a hand on the chest; "кот" = a cat. The hand-hiding advice of rule 4 never overrides what the user asked to see.
8. You may add small neutral details (lighting, background, camera) but never contradict, replace or ignore the user's subject, action, count or colours.
9. STYLE in the request is already decided by the caller: write the prompt in the format of that style and set "style" to the same value.
10. If a CAMERA STYLE line is given, the picture must look like an ordinary person's real camera photo, NOT a polished studio or cinematic render: use the camera-style wording INSTEAD of the usual photographic ending, a plain everyday scene and framing, natural imperfect light, no "cinematic", "dramatic", "studio", "bokeh", "85mm", "perfect", "masterpiece", "stunning". Skin, fur and fabric must have visible natural texture and small imperfections.

PROMPT FORMAT
- STYLE anime: Danbooru-style tags separated by commas, 20-35 tags, in this order: subject count (1girl, 1boy, 2girls, no humans), hair colour and length, eye colour, clothing, expression, pose or action, place, lighting, framing (portrait, upper body, cowboy shot, full body). Do NOT add masterpiece/best quality tags (added automatically).
- STYLE photo: ONE to TWO natural English sentences (max 70 words): subject and action, place, lighting, camera (for example "shot on 85mm lens, f/1.8, soft natural light"), ending with "photorealistic, natural skin texture, sharp focus, highly detailed". For animals, objects and landscapes drop "natural skin texture" and use a fitting camera/lighting/fur/material description instead.
- EXTRA STYLE HINTS (if given) must be woven in naturally (for example cinematic lighting, watercolor, neon cyberpunk).

MODE video: the prompt describes the FIRST FRAME of a short clip that will be animated from this still image. Make it calm and animation-friendly: one centred subject, simple uncluttered background, eyes open, mouth closed or a gentle smile, hands relaxed or out of frame, no text, plain solid-colour clothes without patterns or small textures (they shimmer in video). Set "motion": 40-70 for subtle movement (portrait, breathing, hair and clothes in the wind), 90-127 for moderate (walking, waving), 140-180 only for clear action. Prefer lower values, they produce fewer distortions.

EXAMPLES
REQUEST: привет   (MODE image, STYLE photo)
{"style":"photo","prompt":"A friendly young man in a casual shirt waving hello with his right hand, open palm facing the viewer with five clearly separated fingers, warm smile, looking at the camera, upper body, sunny street in the background, soft natural light, shot on 85mm lens, f/1.8, photorealistic, natural skin texture, sharp focus, highly detailed","negative":"extra hands, extra arms, hand covering the face, extra people, crowd","motion":100,"notes":"Выбрал реализм, сделал простую чёткую позу руки с открытой ладонью и защитил от лишних пальцев."}

REQUEST: аниме девочка в школьной форме под сакурой, без глюков   (MODE image, STYLE anime)
{"style":"anime","prompt":"1girl, solo, long silver hair, blue eyes, school uniform, serafuku, red ribbon, gentle smile, looking at viewer, standing, cherry blossoms, falling petals, outdoors, spring, soft sunlight, depth of field, upper body, hands behind back, symmetrical face, detailed eyes","negative":"extra eyes, multiple girls, hands, extra arms","motion":100,"notes":"Аниме-стиль в виде тегов, руки спрятаны за спину, лицо крупным планом."}

REQUEST: кот спит на подоконнике   (MODE video, STYLE photo)
{"style":"photo","prompt":"A fluffy ginger cat sleeping curled up on a sunlit windowsill, eyes closed, calm pose, simple blurred room in the background, soft morning light, shot on 50mm lens, photorealistic, sharp focus, highly detailed","negative":"extra legs, extra tails, two heads, people","motion":50,"notes":"Спокойный первый кадр и маленькая сила движения, чтобы видео не 'плыло'."}
"""

STYLE_WORDS = re.compile(r"аниме|anime|манг|manga|\b2d\b|мультяш|мультик|cartoon|chibi|чиби|вайфу|waifu|ghibli|гибли|kawaii|кавай", re.I)


DETAILED_MIN, DETAILED_LIMIT = 260, 2100   # знаков: с какой длины промпт считается «подробным» / его предел


def clean_prompt(s, limit=800):
    s = re.sub(r"\s+", " ", str(s or "")).strip().strip('"').strip()
    s = re.sub(r"\(([^()]*?):\s*[0-9.]+\)", r"\1", s)
    s = s.replace("(", "").replace(")", "").replace("[", "").replace("]", "")
    return s[:limit].strip(" ,")


def split_negations(prompt):
    """'no hands, no text' в положительном промпте не работает — переносим в негативный."""
    keep, neg = [], []
    for tok in prompt.split(","):
        t = tok.strip()
        m = re.match(r"^(?:no|without|not)\s+(.+)$", t, re.I)
        if m:
            neg.append(m.group(1).strip())
        elif t:
            keep.append(t)
    return ", ".join(keep), ", ".join(neg)


def merge_negative(*parts):
    seen, out = set(), []
    for part in parts:
        for t in str(part or "").split(","):
            t = t.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return ", ".join(out)


def enhance_prompt(text, mode="image", forced=None, hints=None, camera=None):
    """ИИ-помощник: короткая просьба (на любом языке) -> идеальный английский промпт + negative."""
    hints = [clean_prompt(h, 40) for h in (hints or []) if str(h).strip()][:6]
    text = text.strip()
    model = pick_translate_model()
    d = None
    style = forced if forced in ("anime", "photo") else ("anime" if STYLE_WORDS.search(text) else "photo")
    # ПОДРОБНЫЙ промпт (много деталей): модель не переписывает и не сжимает сцену (раньше режим «1-2 предложения,
    # 70 слов» и лимит 800 знаков отрезали хвост описания — лестницу, сову, гирлянды, ковёр). Только перевод
    # (если нужен), перенос «no ...» в негативный промпт и подсказки стиля/камеры.
    if style == "photo" and len(text) >= DETAILED_MIN:
        prompt = translate_prompt(text, "desc") if CYR.search(text) else text
        prompt = clean_prompt(prompt, DETAILED_LIMIT)
        if hints:
            prompt += ", " + ", ".join(hints)
        if camera in engine.CAMERAS:
            prompt += ", " + engine.CAMERAS[camera]["prompt"]
        prompt, moved_neg = split_negations(prompt)
        return {"style": "photo", "prompt": prompt, "negative": merge_negative(moved_neg, NEG_PHOTO), "motion": 100,
                "notes": f"Подробный промпт ({len(text)} знаков) передан модели целиком, без сжатия и переписывания.",
                "fallback": False}
    if model:
        user = (f"MODE: {mode}\nSTYLE: {style}\nEXTRA STYLE HINTS: {', '.join(hints) or 'none'}\n"
                + (f"CAMERA STYLE: {engine.CAMERAS[camera]['prompt']}\n" if camera in engine.CAMERAS and style == "photo" else "")
                + f"REQUEST: {text}")
        try:
            r = requests.post(OLLAMA + "/api/chat", timeout=240, json={
                "model": model, "stream": False, "keep_alive": KEEP, **no_think(model), "format": "json",
                "options": {"temperature": 0.25, "num_ctx": CTX},
                "messages": [{"role": "system", "content": PROMPT_DIRECTOR}, {"role": "user", "content": user}]})
            d = json.loads(r.json()["message"]["content"])
            if not isinstance(d, dict):
                d = None
        except Exception:
            traceback.print_exc()
            d = None
    prompt = clean_prompt(d.get("prompt")) if d else ""
    fallback = False
    if not prompt or CYR.search(prompt) or len(prompt) < 8:
        fallback = True
        prompt = clean_prompt(translate_prompt(text, "tags" if style == "anime" else "desc"))
        if hints:
            prompt += ", " + ", ".join(hints)
    prompt, moved_neg = split_negations(prompt)
    extra_neg = clean_prompt(d.get("negative"), 400) if d and not fallback else ""
    extra_neg = merge_negative(extra_neg, moved_neg)
    if CYR.search(extra_neg):
        extra_neg = ""
    try:
        motion = int(d.get("motion")) if d and not fallback else 100
    except (TypeError, ValueError):
        motion = 100
    notes = clean_prompt(d.get("notes"), 220) if d and not fallback else "ИИ-помощник не ответил, использован простой перевод."
    return {"style": style, "prompt": prompt,
            "negative": merge_negative(extra_neg, NEG_ANIME if style == "anime" else NEG_PHOTO),
            "motion": max(30, min(200, motion)), "notes": notes, "fallback": fallback}


# ───────────────────────── Генерация ─────────────────────────
def friendly(e):
    name = type(e).__name__
    msg = str(e)
    if "out of memory" in msg.lower() or name == "OutOfMemoryError":
        return ("Не хватило видеопамяти. Уменьшите размер картинки / число кадров или закройте другие "
                "программы, использующие видеокарту.")
    return msg or name


def set_stage(job, text, progress=None):
    job["stage"] = text
    if progress is not None:
        job["progress"] = round(progress, 3)
    if job.get("cancel"):
        raise Cancelled()


def load_image_pipe(key, job, i2i=False):
    import torch
    from diffusers import (AutoencoderKL, DPMSolverMultistepScheduler, EulerAncestralDiscreteScheduler,
                           StableDiffusionXLImg2ImgPipeline, StableDiffusionXLPipeline)
    if S.pipe_key == ("img", key, i2i):
        return S.pipe
    free_pipe()
    cfg = IMAGE_MODELS[key]
    set_stage(job, "Загружаю модель" + ("" if repo_ready(cfg["repo"]) else " (первый раз — скачивание, подождите)") + "…")
    if not torch.cuda.is_available():
        raise RuntimeError("Не найдена видеокарта NVIDIA с CUDA.")
    kw = dict(torch_dtype=torch.float16)
    if key != "turbo":
        kw["vae"] = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16)
    if key in ("turbo", "real"):
        kw["variant"] = "fp16"
    else:
        kw["use_safetensors"] = True
    pipe = (StableDiffusionXLImg2ImgPipeline if i2i else StableDiffusionXLPipeline).from_pretrained(cfg["repo"], **kw)
    if key == "anime":
        pipe.scheduler = EulerAncestralDiscreteScheduler.from_config(pipe.scheduler.config)
    elif key == "real":
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(
            pipe.scheduler.config, use_karras_sigmas=True, algorithm_type="sde-dpmsolver++")
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_slicing()
    pipe.set_progress_bar_config(disable=True)
    S.pipe, S.pipe_key = pipe, ("img", key, i2i)
    return pipe


def load_video_pipe(job):
    import torch
    from diffusers import StableVideoDiffusionPipeline
    if S.pipe_key == ("vid", "svd"):
        return S.pipe
    free_pipe()
    set_stage(job, "Загружаю модель видео" + ("" if repo_ready(VIDEO_REPO) else " (первый раз — скачивание)") + "…")
    if not torch.cuda.is_available():
        raise RuntimeError("Не найдена видеокарта NVIDIA с CUDA.")
    pipe = StableVideoDiffusionPipeline.from_pretrained(VIDEO_REPO, torch_dtype=torch.float16, variant="fp16")
    pipe.enable_model_cpu_offload()
    pipe.unet.enable_forward_chunking()
    pipe.set_progress_bar_config(disable=True)
    S.pipe, S.pipe_key = pipe, ("vid", "svd")
    return pipe


def save_meta(path, meta):
    meta["created"] = time.time()
    path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def resolve_request(job, key, prompt, negative, enhance, translate, styles, mode, p0, camera=None):
    """С проверкой безопасных лимитов: исходный запрос, итоговый английский промпт и негативные подсказки."""
    guard_job(prompt, *(styles or []))
    key, prompt_en, neg = _resolve_request(job, key, prompt, negative, enhance, translate, styles, mode, p0, camera)
    guard_job(prompt_en)  # после перевода и ИИ-помощника — чтобы нельзя было обойти через перевод
    sneg = safety.negative_terms()
    if sneg:
        neg = merge_negative(neg, sneg)
    return key, prompt_en, neg


def _resolve_request(job, key, prompt, negative, enhance, translate, styles, mode, p0, camera=None):
    """-> (ключ модели, английский промпт, negative). Для key='auto' выбирает модель сам."""
    styles = [s for s in (styles or []) if str(s).strip()]
    if enhance:
        set_stage(job, "ИИ-помощник пишет идеальный промпт…", p0)
        forced = None
        if key != "auto":
            forced = "anime" if IMAGE_MODELS[key]["kind"] == "anime" else "photo"
        r = enhance_prompt(prompt, mode, forced, styles, camera)
        if key == "auto":
            key = "anime" if r["style"] == "anime" else "real"
        job["notes"], job["motion"] = r["notes"], r["motion"]
        return key, r["prompt"], merge_negative(negative, r["negative"])
    if key == "auto":
        key = "anime" if STYLE_WORDS.search(prompt) else "real"
    prompt_en = prompt
    if translate and CYR.search(prompt):
        set_stage(job, "Перевожу описание на английский…", p0)
        prompt_en = translate_prompt(prompt, IMAGE_MODELS[key]["style"])
    if styles:
        prompt_en += ", " + ", ".join(styles)
    if camera in engine.CAMERAS and IMAGE_MODELS[key]["kind"] == "photo":
        prompt_en += ", " + engine.CAMERAS[camera]["prompt"]
    neg_en = negative.strip() or IMAGE_MODELS[key]["negative"]
    if CYR.search(neg_en):
        neg_en = translate_prompt(neg_en, "tags")
    return key, prompt_en, neg_en


_KL = None


def activate_klein(job):
    global _KL
    if _KL is None:
        import klein_engine
        _KL = klein_engine.Klein()
    if S.pipe_key != ("klein",):
        free_pipe()
        S.pipe, S.pipe_key = _KL, ("klein",)
    set_stage(job, "Загружаю редактор FLUX.2 klein…")
    _KL.load()
    return _KL


_ZI = None


def get_zi():
    global _ZI
    if _ZI is None:
        import zimage_engine
        _ZI = zimage_engine.ZImage()
    return _ZI


def zi_dims(mp, aspect):
    ratio = {"portrait": 2 / 3, "landscape": 3 / 2, "square": 1.0}.get(aspect, 1.0)
    w = (mp * 1_000_000 * ratio) ** 0.5
    return max(512, int(w) // 16 * 16), max(512, int(w / ratio) // 16 * 16)


def activate_zi(job):
    zi = get_zi()
    if S.pipe_key != ("zimage",):
        free_pipe()
        S.pipe, S.pipe_key = zi, ("zimage",)
    set_stage(job, "Загружаю Z-Image Turbo…" + ("" if model_ready("real") else " (файлы модели ещё не скачаны)"))
    zi.load()
    return zi


def clip_pair():
    try:
        safety._load_models()
        return safety._clf["clip"]
    except Exception:
        return None


def make_images_zimage(job, key, prompt, prompt_en, aspect, w, h, steps_override, seed, count, effort, camera,
                       cam_strength, p0, p1):
    from PIL import Image
    ef = engine.effort_params(effort)
    job["effort"] = f"{ef['name']} ({ef['pct']}%)"
    job["camera"] = camera if camera in engine.CAMERAS else None
    W, H = (zi_dims(ef["mp"], aspect or "portrait") if (aspect or not (w and h)) else (w // 16 * 16, h // 16 * 16))
    steps, n = steps_override or ef["steps"], ef["n"]
    hires = ef["hires"] > 1.01
    set_stage(job, "Освобождаю видеопамять…", p0)
    ollama_unload_all()
    zi = activate_zi(job)
    set_stage(job, "Кодирую описание…", p0)
    emb = zi.encode([prompt_en])[0]
    if seed is None:
        seed = int.from_bytes(os.urandom(3), "big")
    gen_share = 0.72 if hires else 0.95
    total = max(1, count * n * steps)
    res = []
    for i in range(count):
        cands = []
        for k in range(n):
            base = (i * n + k) * steps

            def cb(pipe_, step, ts, kw, base=base):
                if job.get("cancel"):
                    raise Cancelled()
                job["progress"] = round(p0 + (p1 - p0) * gen_share * (base + step + 1) / total, 3)
                return kw

            set_stage(job, f"Рисую {i + 1} из {count}" + (f" · попытка {k + 1} из {n}" if n > 1 else "") + "…")
            cands.append(zi.generate(emb, W, H, steps, seed + i * 100 + k, cb))
        if n > 1:
            set_stage(job, "Выбираю лучшую попытку…")
            best, _scores = engine.pick_best(cands, prompt_en, clip_pair())
        else:
            best = 0
        img = cands[best]
        guard_image(img)
        if hires:
            set_stage(job, "Добавляю мелкие детали (второй проход)…")
            tw, th = int(W * ef["hires"]) // 16 * 16, int(H * ef["hires"]) // 16 * 16
            hs = max(10, steps * 2)
            eff = max(1, int(hs * ef["hires_strength"]))
            h0 = p0 + (p1 - p0) * gen_share

            def cb2(pipe_, step, ts, kw, i=i):
                if job.get("cancel"):
                    raise Cancelled()
                job["progress"] = round(h0 + (p1 - h0) * 0.9 * (i + (step + 1) / eff) / count, 3)
                return kw

            img = zi.refine(img.resize((tw, th), Image.LANCZOS), emb, hs, ef["hires_strength"], seed + i, cb2)
            guard_image(img)
        if job["camera"]:
            set_stage(job, "Имитирую съёмку на камеру…")
            img = engine.apply_camera(img, job["camera"], seed=seed + i, strength=cam_strength)
        name = time.strftime("img_%Y%m%d_%H%M%S") + f"_{seed + i}.png"
        path = OUT / name
        img.save(path, pnginfo=engine.png_meta({"Model": "Z-Image Turbo", "Effort": job["effort"]}))
        save_meta(path, {"kind": "image", "prompt": prompt, "prompt_en": prompt_en, "negative": "", "model": key,
                         "seed": seed + i, "w": img.width, "h": img.height, "steps": steps, "cfg": 0,
                         "effort": job["effort"], "camera": job["camera"] or "", "notes": job.get("notes", "")})
        res.append((img, path, seed + i, prompt_en))
        job["results"].append({"url": f"/outputs/{name}", "name": name, "kind": "image", "seed": seed + i})
    return res


def make_images(job, key, prompt, negative, w, h, steps, cfg, seed, count, p0, p1,
                enhance=True, translate=True, styles=None, aspect=None, mode="image",
                effort=None, camera=None, cam_strength=1.0):
    """Возвращает список (PIL.Image, путь, seed, prompt_en). Прогресс — от p0 до p1."""
    import torch
    key, prompt_en, neg_en = resolve_request(job, key, prompt, negative, enhance, translate, styles, mode, p0, camera)
    cfgm = IMAGE_MODELS[key]
    job["prompt_en"], job["model_used"] = prompt_en, key
    if cfgm.get("engine") == "zimage":
        return make_images_zimage(job, key, prompt, prompt_en, aspect, w, h, steps, seed, count, effort, camera,
                                  cam_strength, p0, p1)
    if aspect or not (w and h):
        w, h = ASPECT_SIZES[cfgm["kind"]][aspect or "portrait"]
    steps = steps or cfgm["steps"]
    cfg = cfgm["cfg"] if cfg is None else cfg
    job["prompt_en"], job["negative_en"], job["model_used"] = prompt_en, neg_en, key
    full = prompt_en if "masterpiece" in prompt_en.lower() else prompt_en + cfgm["suffix"]
    set_stage(job, "Освобождаю видеопамять…", p0)
    ollama_unload_all()
    pipe = load_image_pipe(key, job)
    if seed is None:
        seed = int.from_bytes(os.urandom(3), "big")
    res = []
    total = max(1, steps * count)
    for i in range(count):
        s = seed + i
        base = i * steps

        def cb(pipe_, step, ts, kw, base=base):
            if job.get("cancel"):
                raise Cancelled()
            job["progress"] = round(p0 + (p1 - p0) * (base + step + 1) / total, 3)
            return kw

        set_stage(job, f"Рисую {i + 1} из {count}…")
        kwargs = dict(prompt=full, width=w, height=h, num_inference_steps=steps,
                      generator=torch.Generator("cpu").manual_seed(s), callback_on_step_end=cb)
        if cfgm["uses_negative"]:
            kwargs.update(guidance_scale=cfg, negative_prompt=neg_en)
        else:
            kwargs.update(guidance_scale=0.0)
        img = pipe(**kwargs).images[0]
        guard_image(img)
        name = time.strftime("img_%Y%m%d_%H%M%S") + f"_{s}.png"
        path = OUT / name
        img.save(path)
        save_meta(path, {"kind": "image", "prompt": prompt, "prompt_en": prompt_en, "negative": neg_en,
                         "model": key, "seed": s, "w": w, "h": h, "steps": steps, "cfg": cfg,
                         "notes": job.get("notes", "")})
        res.append((img, path, s, prompt_en))
        job["results"].append({"url": f"/outputs/{name}", "name": name, "kind": "image", "seed": s})
    return res


def do_image(job):
    p = job["params"]
    make_images(job, p["model"], p["prompt"], p["negative"], p.get("width"), p.get("height"), p.get("steps"),
                p.get("cfg"), p["seed"], p["count"], 0.02, 0.98, enhance=p["enhance"], translate=p["translate"],
                styles=p["styles"], aspect=None if p.get("width") else p["aspect"],
                effort=p.get("effort"), camera=p.get("camera"), cam_strength=p.get("cam_strength", 1.0))


VIDEO_DIRECTOR = r"""You are the Video Director of a local studio that animates a still image with Stable Video Diffusion (SVD). SVD receives NO text: it only gets the image and a "motion" strength number. Look at the image (and the optional WISH, the user's words in any language about what should move) and decide how strongly this particular image can safely move.

Answer with ONE JSON object and nothing else:
{"description": "...", "subject": "person" | "animal" | "landscape" | "object" | "other", "hands_visible": true | false, "face_visible": true | false, "closeup": true | false, "motion": 30-170, "notes": "...", "risks": "..."}
- description: ONE short English sentence about what the image shows.
- motion = SVD motion_bucket_id. Calm portrait or visible hands: 30-60. Full-body person or animal: 60-90. Landscape, water, clouds, fire, city, objects: 90-140. A simple scene with nothing fragile (no faces, no hands): up to 170. If hands are clearly visible or the face is large, NEVER go above 60.
- WISH: words like "slightly", "gently", "лёгкий", "чуть", "едва" lower the value by 15; "fast", "strong", "бежит", "резко", "сильно" raise it by 20 (still obeying the hand/face limit). Wishes about camera moves or new objects cannot be done: ignore them quietly.
- notes: ONE short sentence in RUSSIAN telling the user what you saw and why you chose this movement strength.
- risks: ONE short sentence in RUSSIAN about what may go wrong (for example "пальцы могут расплываться"), or "" if nothing.
"""


def pick_vision_model():
    ms = ollama_models()
    for m in ms:
        if m.lower().startswith("qwen3.5"):
            return m
    for m in ms:
        if any(k in m.lower() for k in ("qwen3.5", "vl", "llava", "vision", "minicpm-v", "moondream", "gemma4")):
            return m
    return None


def format_from_size(w, h):
    r = w / h
    return "landscape" if r > 1.3 else "portrait" if r < 0.77 else "square"


def analyze_image(path, wish=""):
    """ИИ-режиссёр: смотрит на картинку (vision-модель) и подбирает формат и силу движения."""
    import base64
    import io
    from PIL import Image
    im = Image.open(path)
    fmt = format_from_size(im.width, im.height)
    thumb = im.convert("RGB")
    thumb.thumbnail((896, 896))
    buf = io.BytesIO()
    thumb.save(buf, "JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()
    known = ""
    mf = Path(path).with_suffix(".json")
    if mf.exists():
        try:
            known = json.loads(mf.read_text(encoding="utf-8")).get("prompt_en") or ""
        except Exception:
            known = ""
    model = pick_vision_model()
    d = None
    if model:
        ollama_unload_all()
        try:
            r = requests.post(OLLAMA + "/api/chat", timeout=300, json={
                "model": model, "stream": False, "keep_alive": 0, "format": "json", **no_think(model),
                "options": {"temperature": 0.2, "num_ctx": 4096},
                "messages": [{"role": "system", "content": VIDEO_DIRECTOR},
                             {"role": "user", "content": f"WISH: {wish.strip() or 'none'}\nKNOWN PROMPT OF THIS IMAGE: {known or 'unknown'}",
                              "images": [b64]}]})
            d = json.loads(r.json()["message"]["content"])
            if not isinstance(d, dict):
                d = None
        except Exception:
            traceback.print_exc()
            d = None
    fallback = d is None
    d = d or {}
    try:
        motion = int(d.get("motion", 70))
    except (TypeError, ValueError):
        motion = 70
    hands, face, closeup = bool(d.get("hands_visible")), bool(d.get("face_visible")), bool(d.get("closeup"))
    if hands or (face and closeup):
        motion = min(motion, 60)
    motion = max(30, min(170, motion))
    notes = clean_prompt(d.get("notes"), 260) if not fallback else (
        "Модель-«глаза» не найдена, поставил безопасные настройки." if not model else
        "ИИ-режиссёр не ответил, поставил безопасные настройки.")
    risks = clean_prompt(d.get("risks"), 220)
    if not CYR.search(notes):
        notes = f"Подобрал силу движения {motion} под эту картинку."
    if risks and not CYR.search(risks):
        risks = ""
    return {"format": fmt, "motion": motion, "description": clean_prompt(d.get("description"), 240) or known,
            "subject": str(d.get("subject") or ""), "hands_visible": hands, "face_visible": face,
            "notes": notes, "risks": risks, "vision_model": model, "fallback": fallback}


def resolve_source(name):
    if not NAME_RE.fullmatch(name or ""):
        raise RuntimeError("Неверное имя файла.")
    for d in (OUT, UPLOADS):
        f = d / name
        if f.exists() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return f
    raise RuntimeError("Исходная картинка не найдена.")


def cover_crop(img, w, h):
    from PIL import Image
    img = img.convert("RGB")
    k = max(w / img.width, h / img.height)
    nw, nh = max(w, round(img.width * k)), max(h, round(img.height * k))
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def do_video(job):
    import torch
    from diffusers.utils import export_to_video
    p = job["params"]
    fmt = p["format"] if p["format"] != "auto" else "landscape"
    prompt_for_meta = ""
    if p["source"] == "text":
        res = make_images(job, p["image_model"], p["prompt"], p["negative"], None, None, None, None, p["seed"], 1,
                          0.02, 0.35, enhance=p["enhance"], translate=p["translate"], styles=p["styles"],
                          aspect=fmt, mode="video")
        src, prompt_for_meta = res[0][0], p["prompt"]
        job["results"].clear()
        job["results"].append({"url": f"/outputs/{res[0][1].name}", "name": res[0][1].name, "kind": "image",
                               "seed": res[0][2]})
    else:
        set_stage(job, "Готовлю картинку…", 0.02)
        src_path = resolve_source(p["file"])
        from PIL import Image
        src = Image.open(src_path)
        guard_job(p.get("wish", ""))
        guard_image(src, "исходная картинка")
        fmt = p["format"] if p["format"] != "auto" else format_from_size(src.width, src.height)
        if p["enhance"]:
            set_stage(job, "ИИ-режиссёр смотрит на картинку…", 0.03)
            plan = analyze_image(src_path, p.get("wish", ""))
            job["notes"] = (plan["notes"] + (" " + plan["risks"] if plan["risks"] else "")).strip()
            job["motion"], job["description"] = plan["motion"], plan["description"]
            prompt_for_meta = plan["description"]
    w, h = VIDEO_FORMATS[fmt]
    img = cover_crop(src, w, h)
    set_stage(job, "Освобождаю видеопамять…", 0.36)
    ollama_unload_all()
    pipe = load_video_pipe(job)
    seed = p["seed"] if p["seed"] is not None else int.from_bytes(os.urandom(3), "big")
    steps = p["steps"]
    motion = p["motion"] if p.get("motion") else (job.get("motion") or 100)
    job["motion_used"] = motion
    p0, p1 = (0.4 if p["source"] == "text" else 0.1), 0.93

    def cb(pipe_, step, ts, kw):
        if job.get("cancel"):
            raise Cancelled()
        job["progress"] = round(p0 + (p1 - p0) * (step + 1) / steps, 3)
        return kw

    set_stage(job, "Создаю видео (это самое долгое, ждите)…", p0)
    frames = pipe(img, height=h, width=w, num_frames=p["frames"], num_inference_steps=steps,
                  motion_bucket_id=motion, noise_aug_strength=0.02, fps=max(5, p["fps"] - 1),
                  decode_chunk_size=2, generator=torch.Generator("cpu").manual_seed(seed),
                  callback_on_step_end=cb).frames[0]
    guard_image(frames[len(frames) // 2], "кадр видео")
    set_stage(job, "Сохраняю видео…", 0.96)
    name = time.strftime("video_%Y%m%d_%H%M%S") + f"_{seed}.mp4"
    path = OUT / name
    export_to_video(frames, str(path), fps=p["fps"])
    frames[0].save(OUT / (name[:-4] + ".jpg"), quality=88)
    save_meta(path, {"kind": "video", "prompt": prompt_for_meta or "(из картинки)", "notes": job.get("notes", ""), "seed": seed,
                     "w": w, "h": h, "steps": steps, "frames": p["frames"], "motion": motion})
    job["results"].append({"url": f"/outputs/{name}", "name": name, "kind": "video", "seed": seed,
                           "poster": f"/outputs/{name[:-4]}.jpg"})


EDIT_DIRECTOR = r"""You are the Photo Editor Director of a local image-editing studio. You SEE the user's image and read the user's edit request (any language, usually Russian). Choose the best method and write exact instructions for it.

Answer with ONE JSON object and nothing else:
{"method": "instruct" | "redraw", "instruction": "...", "prompt": "...", "negative": "...", "style": "anime" | "photo", "strength": 0.3-0.8, "notes": "...", "risks": "..."}

METHODS
- "instruct": the SAME picture with something changed: colours, time of day, weather, season, adding / removing / replacing an object or clothing item, expression, age, background. "instruction" = ONE short imperative English sentence, max 12 words, no negations, e.g. "make it night", "put sunglasses on the man", "turn her hair blue", "make the background a beach", "make him smile".
- "redraw": change the artistic STYLE or re-imagine the picture (to anime, oil painting, watercolor, pixel art, cartoon, 3D, sketch, cinematic) or a big scene change. "prompt" = a full English description of the FINAL image, keeping the subject, pose and composition of the original. Style "anime": Danbooru-style tags separated by commas. Style "photo": one or two natural sentences ending with "photorealistic, sharp focus, highly detailed". "strength" = how much to change: 0.35 light, 0.5 medium, 0.65 strong (style changes usually 0.55-0.65).
- ALWAYS fill both "instruction" and "prompt", whichever method you choose.
- "style": "anime" if the FINAL picture should be anime/manga/cartoon-like illustration, otherwise "photo".
- "negative": extra English things to avoid, max 15 words (for example "extra fingers, deformed face").
- "notes": ONE short sentence in RUSSIAN saying what you will do.
- "risks": ONE short sentence in RUSSIAN about what might go wrong (for example "лицо может немного измениться"), or "".

RULES
1. Change ONLY what the user asked for. Keep the same people, pose and composition.
2. Never put "no" or "without" phrases into "instruction" or "prompt" (put such things into "negative").
3. Keep hands simple and fully inside the frame; never ask for complicated finger poses.
4. The STYLE of the picture stays the same as the ORIGINAL unless the user explicitly asks for a style change. A colour, object, clothing, weather or background request is ALWAYS "instruct" and never turns an anime picture into a photo or a photo into anime.
5. If the request cannot be done well by these tools (exact text on the image, exact face swap, removing an object perfectly), choose the closest method and explain in "risks".

EXAMPLES
REQUEST: сделай ночь   (image: man on a sunny street)
{"method":"instruct","instruction":"make it night time","prompt":"a young man standing on a city street at night, streetlights, dark sky, cinematic lighting, photorealistic, sharp focus, highly detailed","negative":"daylight, overexposed","style":"photo","strength":0.5,"notes":"Сделаю ту же картинку, но ночью.","risks":"Освещение лица может немного измениться."}

REQUEST: сделай волосы голубыми   (image: anime girl with pink hair)
{"method":"instruct","instruction":"turn her hair blue","prompt":"1girl, solo, long blue hair, green eyes, smile, looking at viewer, upper body, outdoors, soft sunlight","negative":"pink hair","style":"anime","strength":0.5,"notes":"Перекрашу волосы в голубой цвет, остальное оставлю как есть.","risks":""}

REQUEST: сделай в стиле аниме   (image: photo of a girl)
{"method":"redraw","instruction":"turn this photo into an anime illustration","prompt":"1girl, solo, long brown hair, brown eyes, smile, looking at viewer, upper body, outdoors, soft sunlight, symmetrical face, detailed eyes","negative":"photo, realistic, extra fingers","style":"anime","strength":0.65,"notes":"Перерисую фото в аниме-стиле, сохранив позу и композицию.","risks":"Черты лица станут чуть другими — это рисунок, а не копия."}
"""

STYLE_CHANGE = re.compile(r"в стиле|стил[ьея]|аниме|anime|мульт|рисунок|рисованн|акварел|масл|пиксел|pixel|3d|эскиз|карандаш|комикс|ghibli|гибли", re.I)


def image_b64(path, size=896):
    import base64
    import io
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def normalize_plan(d, request, mode, fallback):
    d = d if isinstance(d, dict) else {}
    # способ выбираем в коде: перерисовка только если просят сменить стиль
    method = "redraw" if STYLE_CHANGE.search(request) else "instruct"
    if mode in ("instruct", "redraw"):
        method = mode
    instruction = clean_prompt(d.get("instruction"), 200)
    if not instruction or CYR.search(instruction):
        instruction = clean_prompt(translate_prompt(request, "desc"), 200) or request
    prompt = clean_prompt(d.get("prompt"), 800)
    if not prompt or CYR.search(prompt):
        prompt = instruction
    prompt, moved = split_negations(prompt)
    style = d.get("style") if d.get("style") in ("anime", "photo") else (
        "anime" if STYLE_WORDS.search(request) else "photo")
    try:
        strength = float(d.get("strength", 0.55))
    except (TypeError, ValueError):
        strength = 0.55
    neg = clean_prompt(d.get("negative"), 300)
    if CYR.search(neg):
        neg = ""
    notes = clean_prompt(d.get("notes"), 260)
    risks = clean_prompt(d.get("risks"), 220)
    if fallback or not CYR.search(notes):
        notes = "Запрос понят без просмотра картинки." if fallback else f"Применю изменение способом «{'по инструкции' if method == 'instruct' else 'перерисовка'}»."
    if risks and not CYR.search(risks):
        risks = ""
    return {"method": method, "instruction": instruction, "prompt": prompt,
            "negative": merge_negative(neg, moved), "style": style,
            "strength": max(0.3, min(0.8, strength)), "notes": notes, "risks": risks, "fallback": fallback}


def edit_plan(path, request, mode="auto"):
    """ИИ-редактор: смотрит на картинку и пишет план изменения (способ, инструкция, промпт)."""
    model = pick_vision_model()
    d = None
    if model:
        ollama_unload_all()
        try:
            r = requests.post(OLLAMA + "/api/chat", timeout=300, json={
                "model": model, "stream": False, "keep_alive": 0, "format": "json", **no_think(model),
                "options": {"temperature": 0.2, "num_ctx": 4096},
                "messages": [{"role": "system", "content": EDIT_DIRECTOR},
                             {"role": "user", "content": f"REQUEST: {request.strip()}\nPREFERRED METHOD: {mode}",
                              "images": [image_b64(path)]}]})
            d = json.loads(r.json()["message"]["content"])
            if not isinstance(d, dict):
                d = None
        except Exception:
            traceback.print_exc()
            d = None
    return normalize_plan(d, request, mode, d is None)


def fit_image(img, max_side, max_pixels=None):
    """Масштабирует с сохранением пропорций; стороны кратны 8."""
    from PIL import Image
    k = max_side / max(img.size)
    if max_pixels:
        k = min(k, (max_pixels / (img.width * img.height)) ** 0.5)
    w, h = max(64, int(img.width * k) // 8 * 8), max(64, int(img.height * k) // 8 * 8)
    return img.resize((w, h), Image.LANCZOS)


def source_url(name):
    return ("/outputs/" if (OUT / name).exists() else "/uploads/") + name


def do_edit(job):
    import torch
    from PIL import Image
    p = job["params"]
    src_path = resolve_source(p["file"])
    src = Image.open(src_path).convert("RGB")
    guard_job(p["request"])
    guard_image(src, "исходная картинка")
    job["source"] = source_url(p["file"])
    set_stage(job, "ИИ смотрит на картинку и решает, как её изменить…", 0.03)
    plan = normalize_plan(p["plan"], p["request"], p["mode"], False) if p.get("plan") else edit_plan(src_path, p["request"], p["mode"])
    guard_job(plan["instruction"], plan["prompt"])
    method = p["mode"] if p["mode"] != "auto" else plan["method"]
    job["method"] = method
    job["notes"] = (plan["notes"] + (" " + plan["risks"] if plan["risks"] else "")).strip()
    job["prompt_en"] = plan["instruction"] if method == "instruct" else plan["prompt"]
    set_stage(job, "Освобождаю видеопамять…", 0.08)
    ollama_unload_all()
    seed = p["seed"] if p["seed"] is not None else int.from_bytes(os.urandom(3), "big")
    count, s_user = p["count"], p["strength"]
    base = []

    if method == "instruct":
        kl = activate_klein(job)
        set_stage(job, "Кодирую команду…", 0.1)
        emb = kl.encode(plan["instruction"])
        img = fit_image(src, 1216, max_pixels=1_100_000)
        img = img.resize((max(64, img.width // 16 * 16), max(64, img.height // 16 * 16)))
        lvl = engine.effort_params(p.get("effort"))["level"]
        steps = 4 + (2 if lvl >= 5 else 0) + (2 if lvl >= 8 else 0)
        total = steps * count
        job["model_used"] = "klein"
        for i in range(count):
            def cb(pipe_, step, ts, kw, i=i):
                if job.get("cancel"):
                    raise Cancelled()
                job["progress"] = round(0.12 + 0.85 * (i * steps + step + 1) / total, 3)
                return kw
            set_stage(job, f"Меняю картинку {i + 1} из {count}…")
            out = kl.edit(img, emb, steps, seed + i, cb)
            base.append((out, seed + i, 0))
    else:
        key = p["model"] if p["model"] != "auto" else ("anime" if plan["style"] == "anime" else "real")
        cfgm = IMAGE_MODELS[key]
        use_zi = cfgm.get("engine") == "zimage"
        if use_zi:
            zi = activate_zi(job)
            emb_edit = zi.encode([plan["prompt"]])[0]
        else:
            pipe = load_image_pipe(key, job, i2i=True)
        init = fit_image(src, 1216, max_pixels=1_100_000)
        if use_zi:
            init = init.resize((max(64, init.width // 16 * 16), max(64, init.height // 16 * 16)))
        st = plan["strength"] if s_user is None else round(0.3 + 0.4 * s_user, 2)
        steps = cfgm["steps"]
        eff = max(1, int((14 if use_zi else steps) * st))
        total = eff * count
        full = plan["prompt"] if "masterpiece" in plan["prompt"].lower() else plan["prompt"] + cfgm["suffix"]
        neg = merge_negative(plan["negative"], NEG_ANIME if cfgm["kind"] == "anime" else NEG_PHOTO, safety.negative_terms())
        job["model_used"] = key
        job["negative_en"] = neg
        for i in range(count):
            def cb(pipe_, step, ts, kw, i=i):
                if job.get("cancel"):
                    raise Cancelled()
                job["progress"] = round(0.12 + 0.85 * (i * eff + step + 1) / total, 3)
                return kw
            set_stage(job, f"Перерисовываю {i + 1} из {count}…")
            if use_zi:
                out = zi.refine(init, emb_edit, 14, st, seed + i, cb)
            else:
                out = pipe(prompt=full, negative_prompt=neg, image=init, strength=st, num_inference_steps=steps,
                           guidance_scale=cfgm["cfg"] or 6.0, generator=torch.Generator("cpu").manual_seed(seed + i),
                           callback_on_step_end=cb).images[0]
            base.append((out, seed + i, st))

    for out, s, val in base:
        guard_image(out)
        name = time.strftime("edit_%Y%m%d_%H%M%S") + f"_{s}.png"
        path = OUT / name
        out.save(path)
        save_meta(path, {"kind": "image", "prompt": p["request"], "prompt_en": job["prompt_en"], "negative": job.get("negative_en", ""),
                         "model": job.get("model_used", ""), "seed": s, "w": out.width, "h": out.height,
                         "notes": job["notes"], "edit_of": p["file"], "method": method})
        job["results"].append({"url": f"/outputs/{name}", "name": name, "kind": "image", "seed": s})


# ───────────────────────── Live-генератор ─────────────────────────
LIVE_MODELS = {
    "fast": dict(repo="stabilityai/sd-turbo", title="Молния · SD-Turbo"),
}
LIVE = {"loading": False, "error": None, "model": None}
_tr = {"tok": None, "model": None}
_tr_cache = {}
_tr_lock = threading.Lock()


def translate_fast(text):
    """Быстрый перевод ru->en на CPU (Marian), без видеокарты и без большой языковой модели."""
    text = text.strip()
    if not text or not CYR.search(text):
        return text
    if text in _tr_cache:
        return _tr_cache[text]
    with _tr_lock:
        import torch
        if _tr["model"] is None:
            from transformers import MarianMTModel, MarianTokenizer
            _tr["tok"] = MarianTokenizer.from_pretrained("Helsinki-NLP/opus-mt-ru-en")
            _tr["model"] = MarianMTModel.from_pretrained("Helsinki-NLP/opus-mt-ru-en").eval()
        with torch.inference_mode():
            batch = _tr["tok"]([text], return_tensors="pt", truncation=True, max_length=96)
            out = _tr["model"].generate(**batch, num_beams=1, max_length=None, max_new_tokens=64)
        res = _tr["tok"].decode(out[0], skip_special_tokens=True)
    if len(_tr_cache) > 300:
        _tr_cache.clear()
    _tr_cache[text] = res
    return res


def _live_load_worker(model):
    LIVE.update(loading=True, error=None)
    try:
        translate_fast("привет")  # прогрев переводчика (первая загрузка ~40 с)
        with S.lock:
            if not S.on:
                raise RuntimeError("ИИ выключен.")
            if S.pipe_key != ("live", model):
                import torch
                from diffusers import AutoPipelineForText2Image
                free_pipe()
                ollama_unload_all()
                if not torch.cuda.is_available():
                    raise RuntimeError("Не найдена видеокарта NVIDIA с CUDA.")
                pipe = AutoPipelineForText2Image.from_pretrained(
                    LIVE_MODELS[model]["repo"], torch_dtype=torch.float16, variant="fp16").to("cuda")
                pipe.set_progress_bar_config(disable=True)
                try:  # крошечный декодер картинки: вдвое быстрее, качество почти то же
                    from diffusers import AutoencoderTiny
                    pipe.vae = AutoencoderTiny.from_pretrained(
                        "madebyollin/taesd" if model == "fast" else "madebyollin/taesdxl",
                        torch_dtype=torch.float16).to("cuda")
                except Exception:
                    traceback.print_exc()
                pipe(prompt="warm up", num_inference_steps=1, guidance_scale=0.0, width=512, height=512)
                S.pipe, S.pipe_key = pipe, ("live", model)
            S.last_gpu = time.time()
            LIVE["model"] = model
    except Exception as e:  # noqa
        traceback.print_exc()
        LIVE["error"] = friendly(e)
    finally:
        LIVE["loading"] = False


def run_job(job):
    job["status"] = "running"
    job["started"] = time.time()
    with S.lock:
        S.gpu_busy = True
        try:
            if not S.on:
                raise RuntimeError("ИИ выключен.")
            if job.get("cancel"):
                raise Cancelled()
            {"image": do_image, "video": do_video, "edit": do_edit}[job["kind"]](job)
            job["status"], job["progress"], job["stage"] = "done", 1.0, "Готово"
        except Cancelled:
            job["status"], job["stage"] = "cancelled", "Отменено"
        except Blocked as b:
            job["status"], job["error"], job["blocked"], job["stage"] = "error", str(b), True, "Заблокировано"
            job["blocked_info"] = b.info
        except Exception as e:  # noqa
            traceback.print_exc()
            job["status"], job["error"] = "error", friendly(e)
            job["stage"] = "Ошибка"
            if "out of memory" in str(e).lower():
                try:
                    free_pipe()
                except Exception:
                    pass
        finally:
            job["finished"] = time.time()
            S.last_gpu = time.time()
            S.gpu_busy = False
            if "torch" in sys.modules:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()


def worker_loop():
    while True:
        jid = JOBQ.get()
        job = JOBS.get(jid)
        if not job or job.get("cancel"):
            if job:
                job["status"], job["stage"] = "cancelled", "Отменено"
            continue
        run_job(job)


threading.Thread(target=worker_loop, daemon=True).start()

PIPE_IDLE = 240  # секунд простоя, после которых модель картинок/видео выгружается из памяти


def idle_loop():
    while True:
        time.sleep(15)
        try:
            if S.pipe is not None and not S.gpu_busy and time.time() - S.last_gpu > PIPE_IDLE:
                if S.lock.acquire(blocking=False):
                    try:
                        free_pipe()
                    finally:
                        S.lock.release()
        except Exception:
            traceback.print_exc()


threading.Thread(target=idle_loop, daemon=True).start()


def new_job(kind, params):
    if not S.on:
        raise HTTPException(409, "ИИ выключен. Включите его переключателем ИИ.")
    if S.stopping:
        raise HTTPException(409, "ИИ ещё выключается, подождите несколько секунд.")
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "kind": kind, "status": "queued", "progress": 0.0, "stage": "В очереди…",
           "results": [], "error": None, "params": params, "created": time.time(), "cancel": False}
    JOBS[jid] = job
    for old in sorted(JOBS.values(), key=lambda j: j["created"])[:-40]:
        if old["status"] in ("done", "error", "cancelled"):
            JOBS.pop(old["id"], None)
    JOBQ.put(jid)
    return job


def public_job(j):
    return {k: j.get(k) for k in ("id", "kind", "status", "progress", "stage", "results", "error",
                                  "prompt_en", "negative_en", "notes", "description", "method", "source", "model_used", "motion_used", "blocked", "blocked_info", "effort", "camera", "created", "started", "finished")}


# ───────────────────────── API ─────────────────────────
app = FastAPI(title="Codwe AI", docs_url=None, redoc_url=None, openapi_url=None)
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}


@app.middleware("http")
async def guard(request: Request, call_next):
    if (request.headers.get("host") or "").lower() not in ALLOWED_HOSTS:
        return PlainTextResponse("forbidden", status_code=403)
    origin = request.headers.get("origin")
    if origin and origin not in ALLOWED_ORIGINS:
        return PlainTextResponse("forbidden", status_code=403)
    if request.method not in ("GET", "HEAD") and request.headers.get("x-codwe") != "1":
        return PlainTextResponse("forbidden", status_code=403)
    p0 = request.url.path
    if p0.startswith("/api/") and not p0.startswith("/api/auth/") and p0 != "/api/status":
        from starlette.concurrency import run_in_threadpool
        g = await run_in_threadpool(auth_api.gate)  # вход, условия, целостность файлов
        if g:
            return JSONResponse({"detail": g[1], "code": g[0]}, status_code=g[2])
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static") or path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-store"
    if path.startswith("/wsfiles"):  # страницы из рабочей папки открываются в «песочнице» без доступа к приложению
        resp.headers["Content-Security-Policy"] = "sandbox allow-scripts allow-forms allow-modals allow-popups"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def status_payload():
    busy = [public_job(j) for j in JOBS.values() if j["status"] in ("queued", "running")]
    return {"on": S.on, "stopping": S.stopping, "warming": S.warming, "gpu_busy": S.gpu_busy,
            "ollama": ollama_up(), "loaded": ollama_loaded() if S.on else [], "gpu": gpu_info(),
            "chat_model": S.chat_model, "jobs": busy,
            "safety": {"enabled": safety.enabled(), "tampered": safety.TAMPERED}}


@app.get("/api/status")
def api_status():
    return status_payload()


class PowerReq(BaseModel):
    on: bool


def _power_on_worker():
    try:
        if not ensure_ollama():
            S.on = False
            return
        ms = ollama_models()
        if ms:
            if S.chat_model not in ms:
                S.chat_model = ms[0]
            ollama_warm(S.chat_model)
    except Exception:
        traceback.print_exc()
    finally:
        S.warming = False


def _power_off_worker():
    try:
        for j in JOBS.values():
            if j["status"] in ("queued", "running"):
                j["cancel"] = True
        ollama_unload_all()
        with S.lock:  # ждём, пока текущая задача остановится
            free_pipe()
        ollama_unload_all()
    finally:
        S.stopping = False


@app.post("/api/power")
def api_power(req: PowerReq):
    if S.stopping:
        raise HTTPException(409, "ИИ ещё выключается, подождите несколько секунд.")
    if req.on and not S.on:
        S.on, S.warming = True, True
        threading.Thread(target=_power_on_worker, daemon=True).start()
    elif not req.on and S.on:
        S.on, S.stopping, S.warming = False, True, False
        threading.Thread(target=_power_off_worker, daemon=True).start()
    return status_payload()


@app.get("/api/models")
def api_models():
    return {
        "chat": ollama_models(), "default_chat": DEFAULT_CHAT_MODEL,
        "image": {k: dict(key=k, title=v["title"], desc=v["desc"], kind=v["kind"], steps=v["steps"],
                          steps_max=v["steps_max"], cfg=v["cfg"], uses_negative=v["uses_negative"],
                          negative=v["negative"], sizes=v["sizes"], ready=model_ready(k))
                  for k, v in IMAGE_MODELS.items()},
        "effort": [engine.effort_params(engine.level_to_pct(i)) for i in range(10)],
        "cameras": {k: v["title"] for k, v in engine.CAMERAS.items()},
        "speed": {"zimage_sec_per_step": 4.6},
        "video": {"ready": repo_ready(VIDEO_REPO), "formats": VIDEO_FORMATS},
        "edit": {"ready": _klein_ready()},
    }


class Msg(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=200_000)


THINK_MODELS = ("qwen3", "gemma4", "deepseek-r1", "gpt-oss", "magistral", "phi4-reasoning")


def chat_think_args(model, effort):
    """Effort в чате = глубина «размышлений» (если модель это умеет)."""
    if effort is None or not any(k in model.lower() for k in THINK_MODELS):
        return {}
    lvl = engine.effort_params(effort)["level"]
    return {"think": lvl >= 2}


class ChatReq(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    messages: list[Msg] = Field(min_length=1, max_length=400)
    system: str = Field(default="", max_length=60_000)
    temperature: float = Field(default=0.7, ge=0, le=2)
    effort: float | None = Field(default=None, ge=0, le=100)
    ctx: int | None = Field(default=None, ge=2048, le=16384)


@app.post("/api/chat")
def api_chat(req: ChatReq):
    if not S.on:
        raise HTTPException(409, "ИИ выключен. Включите его переключателем ИИ.")
    if S.stopping:
        raise HTTPException(409, "ИИ ещё выключается, подождите несколько секунд.")
    if S.gpu_busy:
        raise HTTPException(423, "Видеокарта занята генерацией картинки/видео. Дождитесь окончания.")
    if not ensure_ollama():
        raise HTTPException(503, "Ollama не запускается. Установите Ollama (ollama.com).")
    last_user = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    okc, catc, msgc = safety.check_text(last_user, only_hard=True)
    if not okc:
        raise HTTPException(403, msgc)
    S.chat_model = req.model
    system = (req.system.strip() + safety.chat_guard(last_user)).strip()
    msgs = [{"role": "system", "content": system}] + [m.model_dump() for m in req.messages]

    def gen():
        r = None
        try:
            r = requests.post(OLLAMA + "/api/chat", stream=True, timeout=(10, 900), json={
                "model": req.model, "messages": msgs, "stream": True, "keep_alive": KEEP,
                "options": {"num_ctx": req.ctx or CTX, "temperature": req.temperature},
                **chat_think_args(req.model, req.effort)})
            if r.status_code == 404:
                yield json.dumps({"error": f"Модель «{req.model}» не скачана. В терминале: ollama pull {req.model}"}) + "\n"
                return
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                if "error" in d:
                    yield json.dumps({"error": str(d["error"])}) + "\n"
                    return
                th = d.get("message", {}).get("thinking", "")
                if th:
                    yield json.dumps({"th": th}) + "\n"
                t = d.get("message", {}).get("content", "")
                if t:
                    yield json.dumps({"t": t}) + "\n"
                if d.get("done"):
                    yield json.dumps({"done": True, "tokens": d.get("eval_count"),
                                      "ns": d.get("eval_duration")}) + "\n"
        except Exception as e:  # noqa
            yield json.dumps({"error": f"Ошибка связи с моделью: {e}"}) + "\n"
        finally:
            if r is not None:
                r.close()

    return StreamingResponse(gen(), media_type="application/x-ndjson")


def _styles(v):
    return [str(x)[:40] for x in (v or [])][:6]


class ImageReq(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative: str = Field(default="", max_length=4000)
    model: str = "auto"
    aspect: str = Field(default="portrait", pattern="^(portrait|landscape|square)$")
    width: int | None = Field(default=None, ge=256, le=1536)
    height: int | None = Field(default=None, ge=256, le=1536)
    steps: int | None = Field(default=None, ge=1, le=60)
    cfg: float | None = Field(default=None, ge=0, le=20)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    count: int = Field(default=1, ge=1, le=4)
    translate: bool = True
    enhance: bool = True
    styles: list[str] = Field(default_factory=list, max_length=6)
    effort: float | None = Field(default=None, ge=0, le=100)
    camera: str | None = Field(default=None, pattern="^(phone|dslr|film|compact|cctv)$")
    cam_strength: float = Field(default=1.0, ge=0, le=1.5)


@app.post("/api/image")
def api_image(req: ImageReq):
    guard_api(req.prompt, *req.styles)
    if req.model != "auto" and req.model not in IMAGE_MODELS:
        raise HTTPException(400, "Неизвестная модель.")
    p = req.model_dump()
    p["styles"] = _styles(req.styles)
    if req.width and req.height:
        if req.width * req.height > 1_600_000:
            raise HTTPException(400, "Слишком большой размер картинки для вашей видеокарты (максимум ≈1,6 МП).")
        p["width"], p["height"] = req.width // 64 * 64 or 64, req.height // 64 * 64 or 64
    else:
        p["width"] = p["height"] = None
    return {"id": new_job("image", p)["id"]}


class VideoReq(BaseModel):
    source: str = Field(pattern="^(file|text)$")
    file: str = Field(default="", max_length=200)
    prompt: str = Field(default="", max_length=4000)
    negative: str = Field(default="", max_length=4000)
    image_model: str = "auto"
    format: str = Field(default="auto", pattern="^(auto|landscape|portrait|square)$")
    wish: str = Field(default="", max_length=500)
    frames: int = Field(default=25, ge=8, le=25)
    steps: int = Field(default=20, ge=8, le=40)
    motion: int | None = Field(default=None, ge=1, le=255)
    fps: int = Field(default=8, ge=5, le=14)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    translate: bool = True
    enhance: bool = True
    styles: list[str] = Field(default_factory=list, max_length=6)


@app.post("/api/video")
def api_video(req: VideoReq):
    guard_api(req.prompt, req.wish, *req.styles)
    if req.source == "file":
        resolve_source(req.file)
    else:
        if not req.prompt.strip():
            raise HTTPException(400, "Опишите, что должно быть на видео.")
        if req.image_model != "auto" and req.image_model not in IMAGE_MODELS:
            raise HTTPException(400, "Неизвестная модель картинки.")
    p = req.model_dump()
    p["styles"] = _styles(req.styles)
    return {"id": new_job("video", p)["id"]}


class AnalyzeReq(BaseModel):
    file: str = Field(min_length=1, max_length=200)
    wish: str = Field(default="", max_length=500)


@app.post("/api/analyze")
def api_analyze(req: AnalyzeReq):
    if not S.on:
        raise HTTPException(409, "ИИ выключен. Включите его переключателем ИИ.")
    if S.stopping:
        raise HTTPException(409, "ИИ ещё выключается, подождите несколько секунд.")
    if S.gpu_busy:
        raise HTTPException(423, "Видеокарта занята генерацией. Дождитесь окончания.")
    try:
        path = resolve_source(req.file)
    except RuntimeError as e:
        raise HTTPException(404, str(e))
    if not ensure_ollama():
        raise HTTPException(503, "Ollama не запускается.")
    return analyze_image(path, req.wish)


class EditReq(BaseModel):
    file: str = Field(min_length=1, max_length=200)
    request: str = Field(min_length=1, max_length=1000)
    mode: str = Field(default="auto", pattern="^(auto|instruct|redraw)$")
    strength: float | None = Field(default=None, ge=0, le=1)
    model: str = "auto"
    count: int = Field(default=1, ge=1, le=4)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    plan: dict | None = None
    effort: float | None = Field(default=None, ge=0, le=100)


@app.post("/api/edit")
def api_edit(req: EditReq):
    guard_api(req.request)
    try:
        resolve_source(req.file)
    except RuntimeError as e:
        raise HTTPException(404, str(e))
    if req.model != "auto" and req.model not in ("real", "anime"):
        raise HTTPException(400, "Для перерисовки доступны модели «Реализм» и «Аниме».")
    return {"id": new_job("edit", req.model_dump())["id"]}


class EditPlanReq(BaseModel):
    file: str = Field(min_length=1, max_length=200)
    request: str = Field(min_length=1, max_length=1000)
    mode: str = Field(default="auto", pattern="^(auto|instruct|redraw)$")


@app.post("/api/edit/plan")
def api_edit_plan(req: EditPlanReq):
    guard_api(req.request)
    if not S.on:
        raise HTTPException(409, "ИИ выключен. Включите его переключателем ИИ.")
    if S.stopping:
        raise HTTPException(409, "ИИ ещё выключается, подождите несколько секунд.")
    if S.gpu_busy:
        raise HTTPException(423, "Видеокарта занята генерацией. Дождитесь окончания.")
    try:
        path = resolve_source(req.file)
    except RuntimeError as e:
        raise HTTPException(404, str(e))
    if not ensure_ollama():
        raise HTTPException(503, "Ollama не запускается.")
    return edit_plan(path, req.request, req.mode)


class LiveStartReq(BaseModel):
    model: str = "fast"


def _live_status():
    key = S.pipe_key
    return {"ready": bool(key and key[0] == "live"), "model": key[1] if key and key[0] == "live" else None,
            "loading": LIVE["loading"], "error": LIVE["error"], "busy": S.gpu_busy,
            "ready_files": {k: repo_ready(v["repo"]) for k, v in LIVE_MODELS.items()}}


@app.get("/api/live/status")
def api_live_status():
    return _live_status()


@app.post("/api/live/start")
def api_live_start(req: LiveStartReq):
    if req.model not in LIVE_MODELS:
        raise HTTPException(400, "Неизвестная модель.")
    if not S.on:
        raise HTTPException(409, "ИИ выключен. Включите его переключателем ИИ.")
    if S.stopping:
        raise HTTPException(409, "ИИ ещё выключается, подождите несколько секунд.")
    if S.gpu_busy:
        raise HTTPException(423, "Видеокарта занята генерацией. Дождитесь окончания.")
    if not LIVE["loading"] and S.pipe_key != ("live", req.model):
        threading.Thread(target=_live_load_worker, args=(req.model,), daemon=True).start()
        LIVE.update(loading=True, error=None)
    return _live_status()


class LiveReq(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)
    seed: int = Field(default=1, ge=0, le=2**31 - 1)
    steps: int = Field(default=1, ge=1, le=4)
    size: int = Field(default=512, ge=256, le=768)
    model: str = "fast"
    style: str = Field(default="", max_length=160)
    final: bool = True


@app.post("/api/live")
def api_live(req: LiveReq):
    import io
    guard_api(req.prompt, req.style)
    if not S.on:
        raise HTTPException(409, "ИИ выключен.")
    if S.pipe_key != ("live", req.model):
        raise HTTPException(409, "Live-модель не загружена.")
    if not S.lock.acquire(blocking=False):
        raise HTTPException(423, "Видеокарта занята.")
    try:
        if S.pipe_key != ("live", req.model):
            raise HTTPException(409, "Live-модель не загружена.")
        import torch
        t0 = time.time()
        text = req.prompt
        if not req.final and not re.search(r"[\s,.;:!?]$", text):
            # пока печатаем слово: переводим только законченные слова (они в кэше), хвост пока не трогаем
            head = re.sub(r"\S+$", "", text).strip()
            text = head or text
        en = translate_fast(text)
        ok, _cat, msg = safety.check_text(en)  # и после перевода
        if not ok:
            raise HTTPException(403, msg)
        t1 = time.time()
        full = en + (", " + req.style if req.style.strip() else "")
        size = req.size // 8 * 8
        with torch.inference_mode():
            img = S.pipe(prompt=full, num_inference_steps=req.steps, guidance_scale=0.0, width=size, height=size,
                         generator=torch.Generator("cpu").manual_seed(req.seed)).images[0]
        t2 = time.time()
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        if req.final:  # классификатор ~0,4 с на CPU: проверяем итоговый кадр (после паузы в наборе)
            okimg, _c, msgimg, _s = safety.check_image(img)
            if not okimg:
                raise HTTPException(403, msgimg)
        LIVE["last"] = (img, req.prompt, full, req.seed, req.model)
        S.last_gpu = time.time()
    finally:
        S.lock.release()
    from fastapi.responses import Response
    return Response(content=buf.getvalue(), media_type="image/jpeg", headers={
        "X-Ms-Translate": str(round((t1 - t0) * 1000)), "X-Ms-Image": str(round((t2 - t1) * 1000)),
        "X-Prompt-En": en.encode("ascii", "ignore").decode()[:200], "Cache-Control": "no-store"})


@app.post("/api/live/save")
def api_live_save():
    last = LIVE.get("last")
    if not last:
        raise HTTPException(404, "Пока нечего сохранять.")
    img, prompt, full, seed, model = last
    name = time.strftime("live_%Y%m%d_%H%M%S") + f"_{seed}.png"
    path = OUT / name
    img.save(path)
    save_meta(path, {"kind": "image", "prompt": prompt, "prompt_en": full, "model": "live-" + model, "seed": seed,
                     "w": img.width, "h": img.height})
    return {"name": name, "url": f"/outputs/{name}"}


class SafetyReq(BaseModel):
    enabled: bool
    confirm_adult: bool = False


@app.get("/api/safety")
def api_safety():
    return safety.status()


@app.post("/api/safety")
def api_safety_set(req: SafetyReq):
    if not req.enabled:
        raise HTTPException(403, "Безопасные лимиты нельзя отключить.")
    return safety.status()


class TitleReq(BaseModel):
    user: str = Field(min_length=1, max_length=2000)
    assistant: str = Field(default="", max_length=1500)


@app.post("/api/title")
def api_title(req: TitleReq):
    """Короткое название чата, которое придумывает ИИ."""
    fallback = re.sub(r"\s+", " ", req.user).strip()[:40]
    if not S.on or S.stopping or S.gpu_busy:
        return {"title": fallback, "ai": False}
    model = pick_translate_model()
    if not model or not ensure_ollama():
        return {"title": fallback, "ai": False}
    try:
        r = requests.post(OLLAMA + "/api/chat", timeout=60, json={
            "model": model, "stream": False, "keep_alive": KEEP, **no_think(model), "options": {"temperature": 0.3, "num_ctx": CTX},
            "messages": [{"role": "system", "content":
                          "Придумай короткое название для этого чата: 2-5 слов, на языке пользователя, без кавычек, "
                          "без точки в конце, без слова «чат». Ответь ТОЛЬКО названием."},
                         {"role": "user", "content": f"Пользователь: {req.user[:600]}\nАссистент: {req.assistant[:400]}"}]})
        t = (r.json()["message"]["content"] or "").strip().splitlines()[0].strip(" \"'«»`*#.:!") if r.ok else ""
        t = re.sub(r"^(название|title)\s*[:：-]\s*", "", t, flags=re.I).strip(" \"'«»")
        if 2 <= len(t) <= 60:
            return {"title": t[:48], "ai": True}
    except Exception:
        traceback.print_exc()
    return {"title": fallback, "ai": False}


class EnhanceReq(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    mode: str = Field(default="image", pattern="^(image|video)$")
    model: str = "auto"
    styles: list[str] = Field(default_factory=list, max_length=6)
    camera: str | None = Field(default=None, pattern="^(phone|dslr|film|compact|cctv)$")


@app.post("/api/enhance")
def api_enhance(req: EnhanceReq):
    guard_api(req.text, *req.styles)
    if not S.on:
        raise HTTPException(409, "ИИ выключен. Включите его переключателем ИИ.")
    if S.stopping:
        raise HTTPException(409, "ИИ ещё выключается, подождите несколько секунд.")
    if S.gpu_busy:
        raise HTTPException(423, "Видеокарта занята генерацией. Дождитесь окончания.")
    if req.model != "auto" and req.model not in IMAGE_MODELS:
        raise HTTPException(400, "Неизвестная модель.")
    if not ensure_ollama():
        raise HTTPException(503, "Ollama не запускается.")
    forced = None
    if req.model != "auto":
        forced = "anime" if IMAGE_MODELS[req.model]["kind"] == "anime" else "photo"
    r = enhance_prompt(req.text, req.mode, forced, _styles(req.styles), req.camera)
    key = req.model if req.model != "auto" else ("anime" if r["style"] == "anime" else "real")
    r.update(model=key, model_title=IMAGE_MODELS[key]["title"])
    return r


@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404, "Задача не найдена.")
    return public_job(j)


@app.post("/api/jobs/{jid}/cancel")
def api_cancel(jid: str):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404, "Задача не найдена.")
    j["cancel"] = True
    if j["status"] == "queued":
        j["status"], j["stage"] = "cancelled", "Отменено"
    return public_job(j)


@app.get("/api/gallery")
def api_gallery():
    items = []
    for f in OUT.iterdir():
        if f.suffix.lower() not in (".png", ".mp4"):
            continue
        meta = {}
        mf = f.with_suffix(".json")
        if mf.exists():
            try:
                meta = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        kind = "video" if f.suffix.lower() == ".mp4" else "image"
        item = {"name": f.name, "url": f"/outputs/{f.name}", "kind": kind,
                "created": meta.get("created") or f.stat().st_mtime,
                "prompt": meta.get("prompt", ""), "prompt_en": meta.get("prompt_en", ""),
                "model": meta.get("model", ""), "seed": meta.get("seed"), "w": meta.get("w"), "h": meta.get("h")}
        if kind == "video":
            item["poster"] = f"/outputs/{f.stem}.jpg"
        items.append(item)
    items.sort(key=lambda x: x["created"], reverse=True)
    return {"items": items}


@app.delete("/api/gallery/{name}")
def api_gallery_delete(name: str):
    if not NAME_RE.fullmatch(name):
        raise HTTPException(400, "Неверное имя.")
    f = OUT / name
    if not f.exists() or f.suffix.lower() not in (".png", ".mp4"):
        raise HTTPException(404, "Файл не найден.")
    f.unlink()
    for ext in (".json", ".jpg"):
        side = f.with_suffix(ext)
        if side.exists() and (ext == ".json" or f.suffix.lower() == ".mp4"):
            side.unlink()
    return {"ok": True}


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    from PIL import Image
    data = await file.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Файл больше 20 МБ.")
    try:
        import io
        img = Image.open(io.BytesIO(data))
        img.load()
        img = img.convert("RGB")
    except Exception:
        raise HTTPException(400, "Это не картинка.")
    name = f"up_{uuid.uuid4().hex[:10]}.png"
    img.save(UPLOADS / name)
    return {"name": name, "url": f"/uploads/{name}"}


@app.post("/api/shutdown")
def api_shutdown():
    def bye():
        time.sleep(0.4)
        try:
            ollama_unload_all()
        finally:
            os._exit(0)
    threading.Thread(target=bye, daemon=True).start()
    return {"ok": True}


app.include_router(auth_api.router)
app.include_router(auth_api.admin_router)
app.include_router(workspace_api.router)
codwecode_api.bind(S=S, OLLAMA=OLLAMA, KEEP=KEEP, ensure_ollama=ensure_ollama, pick_vision_model=pick_vision_model,
                   no_think=no_think)
app.include_router(codwecode_api.router)
app.mount("/wsfiles", StaticFiles(directory=str(workspace_api.ROOT), html=True), name="wsfiles")
app.mount("/outputs", StaticFiles(directory=str(OUT)), name="outputs")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS)), name="uploads")
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


if __name__ == "__main__":
    import uvicorn
    if os.environ.get("CODWE_AUTOON", "1") != "0":  # ИИ включается сам при запуске
        S.on, S.warming = True, True
        threading.Thread(target=_power_on_worker, daemon=True).start()
    def _warm_safety():
        try:
            from PIL import Image
            if safety.enabled():
                safety.image_scores(Image.new("RGB", (64, 64), "gray"))
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_warm_safety, daemon=True).start()
    print(f"Codwe AI: http://127.0.0.1:{PORT}  (только этот компьютер)", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
