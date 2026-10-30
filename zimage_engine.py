"""Z-Image Turbo (Tongyi-MAI, Apache-2.0) для видеокарт на 8 ГБ.

Память (8 ГБ VRAM / 15 ГБ RAM):
  * трансформер — квантованный GGUF (Q5_K_M, ~4,6 ГБ);
  * текстовый энкодер Qwen3-4B — в 4 бита (NF4, ~2,5 ГБ), он и трансформер НЕ лежат в видеопамяти одновременно:
    сначала энкодер считает вектор промпта и выгружается, потом работает трансформер.
"""
import gc
import os
import time
from pathlib import Path

import torch

HOME = Path(__file__).resolve().parent
BASE = "Tongyi-MAI/Z-Image-Turbo"
GGUF_REPO, GGUF_FILE = "unsloth/Z-Image-Turbo-GGUF", "z-image-turbo-Q5_K_M.gguf"
TE_NF4 = HOME / "models" / "zimage-te-nf4"
DEV = "cuda"
BF16 = torch.bfloat16


def _hf(repo, filename=None):
    """Путь к файлу или папке модели в локальном кэше (без интернета и без проверки «полноты» снимка)."""
    from huggingface_hub import hf_hub_download
    if filename:
        return hf_hub_download(repo, filename, local_files_only=True)
    snaps = Path(os.environ.get("HF_HOME", str(HOME / "models"))) / "hub" / ("models--" + repo.replace("/", "--")) / "snapshots"
    dirs = sorted(snaps.iterdir(), key=lambda d: d.stat().st_mtime) if snaps.exists() else []
    if not dirs:
        raise FileNotFoundError(repo)
    return str(dirs[-1])


def ready():
    """Все файлы модели на диске?"""
    try:
        _hf(GGUF_REPO, GGUF_FILE)
        base = Path(_hf(BASE))
        return (base / "text_encoder").exists() and (base / "vae").exists()
    except Exception:
        return False


class ZImage:
    def __init__(self):
        self.pipe = None          # ZImagePipeline без текстового энкодера
        self.img2img = None
        self.embeds_cache = {}
        self.te_ready = TE_NF4.exists()

    # ───── загрузка ─────
    def _make_te_nf4(self):
        """Один раз превращает энкодер (8 ГБ) в компактный 4-битный и сохраняет на диск."""
        from transformers import AutoTokenizer, BitsAndBytesConfig, Qwen3Model
        base = _hf(BASE)
        q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=BF16,
                               bnb_4bit_use_double_quant=True)
        te = Qwen3Model.from_pretrained(base, subfolder="text_encoder", quantization_config=q, torch_dtype=BF16,
                                        device_map={"": 0}, low_cpu_mem_usage=True)
        te.save_pretrained(TE_NF4)
        AutoTokenizer.from_pretrained(base, subfolder="tokenizer").save_pretrained(TE_NF4 / "tokenizer")
        del te
        gc.collect()
        torch.cuda.empty_cache()
        self.te_ready = True

    def _load_te(self):
        from transformers import AutoTokenizer, Qwen3Model
        if not TE_NF4.exists():
            self._make_te_nf4()
        te = Qwen3Model.from_pretrained(TE_NF4, torch_dtype=BF16, device_map={"": 0}, low_cpu_mem_usage=True)
        tok = AutoTokenizer.from_pretrained(TE_NF4 / "tokenizer")
        return te, tok

    def load(self):
        if self.pipe is not None:
            return
        from diffusers import (AutoencoderKL, FlowMatchEulerDiscreteScheduler, GGUFQuantizationConfig,
                               ZImagePipeline, ZImageTransformer2DModel)
        base = _hf(BASE)
        gguf = _hf(GGUF_REPO, GGUF_FILE)
        tr = ZImageTransformer2DModel.from_single_file(
            gguf, quantization_config=GGUFQuantizationConfig(compute_dtype=BF16), torch_dtype=BF16,
            config=base, subfolder="transformer")
        vae = AutoencoderKL.from_pretrained(base, subfolder="vae", torch_dtype=BF16)
        sch = FlowMatchEulerDiscreteScheduler.from_pretrained(base, subfolder="scheduler")
        self.pipe = ZImagePipeline(scheduler=sch, vae=vae, text_encoder=None, tokenizer=None, transformer=tr)
        self.pipe.set_progress_bar_config(disable=True)
        vae.enable_tiling()

    def unload(self):
        self.pipe = None
        self.img2img = None
        self.embeds_cache.clear()
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    # ───── кодирование промпта ─────
    def encode(self, prompts):
        """Список промптов -> список векторов (на CPU). Энкодер временно занимает видеопамять."""
        todo = [p for p in prompts if p not in self.embeds_cache]
        if todo:
            if self.pipe is not None:
                self.pipe.transformer.to("cpu")
                self.pipe.vae.to("cpu")
                gc.collect()
                torch.cuda.empty_cache()
            te, tok = self._load_te()
            from diffusers import ZImagePipeline
            enc = ZImagePipeline(scheduler=None, vae=None, text_encoder=te, tokenizer=tok, transformer=None)
            with torch.inference_mode():
                for p in todo:
                    emb, _ = enc.encode_prompt(p, device=DEV, do_classifier_free_guidance=False)
                    self.embeds_cache[p] = [e.to("cpu") for e in emb]
            del enc, te
            gc.collect()
            torch.cuda.empty_cache()
        return [self.embeds_cache[p] for p in prompts]

    def to_gpu(self):
        self.pipe.transformer.to(DEV)
        self.pipe.vae.to(DEV)

    # ───── генерация ─────
    @torch.inference_mode()
    def generate(self, embeds, width, height, steps, seed, cb=None):
        self.load()
        self.to_gpu()
        emb = [e.to(DEV) for e in embeds]
        out = self.pipe(prompt_embeds=emb, height=height, width=width, num_inference_steps=steps,
                        guidance_scale=0.0, generator=torch.Generator("cpu").manual_seed(seed),
                        callback_on_step_end=cb, output_type="pil")
        return out.images[0]

    @torch.inference_mode()
    def refine(self, image, embeds, steps, strength, seed, cb=None):
        """Второй проход: увеличенная картинка перерисовывается той же моделью — добавляется настоящая детализация."""
        from diffusers import ZImageImg2ImgPipeline
        self.load()
        self.to_gpu()
        if self.img2img is None:
            self.img2img = ZImageImg2ImgPipeline(**self.pipe.components)
            self.img2img.set_progress_bar_config(disable=True)
        emb = [e.to(DEV) for e in embeds]
        out = self.img2img(prompt_embeds=emb, image=image, strength=strength, num_inference_steps=steps,
                           guidance_scale=0.0, generator=torch.Generator("cpu").manual_seed(seed),
                           callback_on_step_end=cb, output_type="pil")
        return out.images[0]
