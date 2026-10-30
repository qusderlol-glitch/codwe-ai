"""FLUX.2 [klein] 4B (Black Forest Labs, Apache-2.0) — редактор картинок по инструкции.

Трансформер — квантованный GGUF (Q8_0, ~4,3 ГБ). Текстовый энкодер Qwen3-4B тот же самый, что у Z-Image
(веса совпадают), поэтому используется общий 4-битный энкодер из zimage_engine.
"""
import gc
from pathlib import Path

import torch

import zimage_engine as zi

BASE = "black-forest-labs/FLUX.2-klein-4B"
GGUF_REPO, GGUF_FILE = "unsloth/FLUX.2-klein-4B-GGUF", "flux-2-klein-4b-Q8_0.gguf"
DEV, BF16 = "cuda", torch.bfloat16


def ready():
    try:
        zi._hf(GGUF_REPO, GGUF_FILE)
        return (Path(zi._hf(BASE)) / "vae").exists()
    except Exception:
        return False


class Klein:
    def __init__(self):
        self.pipe = None
        self.embeds_cache = {}
        self._zi = zi.ZImage()  # используем только его загрузчик энкодера

    def load(self):
        if self.pipe is not None:
            return
        from diffusers import (AutoencoderKLFlux2, FlowMatchEulerDiscreteScheduler, Flux2KleinPipeline,
                               Flux2Transformer2DModel, GGUFQuantizationConfig)
        base = zi._hf(BASE)
        tr = Flux2Transformer2DModel.from_single_file(
            zi._hf(GGUF_REPO, GGUF_FILE), quantization_config=GGUFQuantizationConfig(compute_dtype=BF16),
            torch_dtype=BF16, config=base, subfolder="transformer")
        vae = AutoencoderKLFlux2.from_pretrained(base, subfolder="vae", torch_dtype=BF16)
        sch = FlowMatchEulerDiscreteScheduler.from_pretrained(base, subfolder="scheduler")
        self.pipe = Flux2KleinPipeline(scheduler=sch, vae=vae, text_encoder=None, tokenizer=None, transformer=tr,
                                       is_distilled=True)
        self.pipe.set_progress_bar_config(disable=True)
        vae.enable_tiling()

    def unload(self):
        self.pipe = None
        self.embeds_cache.clear()
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def encode(self, prompt):
        if prompt in self.embeds_cache:
            return self.embeds_cache[prompt]
        from diffusers import Flux2KleinPipeline
        if self.pipe is not None:
            self.pipe.transformer.to("cpu")
            self.pipe.vae.to("cpu")
            gc.collect()
            torch.cuda.empty_cache()
        te, tok = self._zi._load_te()
        enc = Flux2KleinPipeline(scheduler=None, vae=None, text_encoder=te, tokenizer=tok, transformer=None,
                                 is_distilled=True)
        with torch.inference_mode():
            emb, _ids = enc.encode_prompt(prompt, device=DEV)
        self.embeds_cache[prompt] = emb.to("cpu")
        del enc, te
        gc.collect()
        torch.cuda.empty_cache()
        return self.embeds_cache[prompt]

    @torch.inference_mode()
    def edit(self, image, emb, steps, seed, cb=None):
        """Правка по инструкции: исходная картинка + вектор команды -> новая картинка того же размера."""
        self.load()
        self.pipe.transformer.to(DEV)
        self.pipe.vae.to(DEV)
        w, h = image.size
        out = self.pipe(image=image, prompt_embeds=emb.to(DEV), height=h, width=w, num_inference_steps=steps,
                        guidance_scale=1.0, generator=torch.Generator("cpu").manual_seed(seed),
                        callback_on_step_end=cb, output_type="pil")
        return out.images[0]
