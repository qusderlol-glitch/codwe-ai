"""Каталог того, что может установить Codwe AI, и объяснения простыми словами для окна-мастера."""

# размеры в ГБ (приблизительно; точные считаются при скачивании, если есть интернет)
# kind: pip | ollama | ollama_model | hf | task
SMALL_IGNORE = ["*.msgpack", "*.h5", "*.ot", "tf_*", "flax_*", "*.onnx", "optimizer.pt", "*.md", ".gitattributes"]

COMPONENTS = [
    {
        "id": "core", "kind": "pip", "required": True, "size_gb": 4.0,
        "title": "Основа программы",
        "simple": "Набор программ на Python, на которых всё работает. Без него Codwe AI не запустится.",
        "details": "Скачиваются библиотеки: PyTorch (для видеокарты NVIDIA), diffusers, transformers и другие. Это самая долгая часть установки.",
        "needs": [], "license": "",
    },
    {
        "id": "ollama", "kind": "ollama", "required": True, "size_gb": 1.6,
        "title": "Ollama — «мотор» умного чата",
        "simple": "Маленькая программа, которая запускает умный чат. Устанавливается как обычная программа Windows.",
        "details": "Устанавливается через официальный установщик Windows (winget). Если он не найден, мастер откроет страницу ollama.com.",
        "needs": [], "license": "MIT (Ollama)",
    },
    {
        "id": "chat", "kind": "ollama_model", "required": True, "size_gb": 6.6, "model": "qwen3.5:9b",
        "title": "Умный помощник Qwen3.5 (чат, код, зрение)",
        "simple": "Главный мозг программы: отвечает в чате, пишет код, смотрит на картинки и проверяет игры в Codwecode.",
        "details": "Модель qwen3.5:9b от Alibaba. Работает на вашем компьютере, ничего никуда не отправляет.",
        "needs": ["ollama"], "license": "лицензия Qwen — смотрите страницу модели на ollama.com",
    },
    {
        "id": "safety", "kind": "hf", "required": True, "size_gb": 1.0,
        "title": "Защитные проверки",
        "simple": "Две небольшие модели, которые не дают создавать откровенные и жестокие картинки. Отключить их нельзя.",
        "details": "Проверяют готовые картинки на вашем компьютере (на процессоре).",
        "needs": [], "license": "Apache-2.0 / MIT",
        "repos": [
            {"repo": "Falconsai/nsfw_image_detection", "min_gb": 0.3, "ignore": SMALL_IGNORE + ["pytorch_model.bin", "optimizer.pt"]},
            {"repo": "openai/clip-vit-base-patch32", "min_gb": 0.5, "allow": ["config.json", "preprocessor_config.json", "tokenizer*",
                                                                "vocab.json", "merges.txt", "special_tokens_map.json", "pytorch_model.bin"]},
        ],
    },
    {
        "id": "image", "kind": "hf", "required": False, "recommended": True, "size_gb": 15.5, "vram_gb": 8,
        "title": "Фото-генератор Z-Image Turbo",
        "simple": "Рисует очень реалистичные картинки по описанию: людей, животных, предметы. Главная функция программы.",
        "details": "Скачивается ~13 ГБ, потом один раз готовится (3–5 минут) — это нормально. Нужна видеокарта NVIDIA с 8 ГБ памяти.",
        "needs": ["core", "safety"], "license": "Apache-2.0 (Tongyi-MAI)", "post": "zimage_te",
        "repos": [
            {"repo": "Tongyi-MAI/Z-Image-Turbo", "min_gb": 8.0, "allow": ["model_index.json", "text_encoder/*", "tokenizer/*", "vae/*",
                                                          "scheduler/*", "transformer/config.json"]},
            {"repo": "unsloth/Z-Image-Turbo-GGUF", "min_gb": 4.5, "allow": ["z-image-turbo-Q5_K_M.gguf"]},
        ],
    },
    {
        "id": "editor", "kind": "hf", "required": False, "recommended": True, "size_gb": 4.5, "vram_gb": 8,
        "title": "Редактор картинок FLUX.2 klein",
        "simple": "Меняет готовую картинку по просьбе: «сделай ночь», «добавь очки». Как редактор в Gemini.",
        "details": "Использует часть файлов фото-генератора, поэтому без него не работает (мастер включит его сам).",
        "needs": ["core", "safety", "image"], "license": "Apache-2.0 (Black Forest Labs)",
        "repos": [
            {"repo": "black-forest-labs/FLUX.2-klein-4B", "min_gb": 0.15, "allow": ["model_index.json", "vae/*", "scheduler/*", "tokenizer/*",
                                                                   "transformer/config.json"]},
            {"repo": "unsloth/FLUX.2-klein-4B-GGUF", "min_gb": 4.2, "allow": ["flux-2-klein-4b-Q8_0.gguf"]},
        ],
    },
    {
        "id": "live", "kind": "hf", "required": False, "recommended": True, "size_gb": 3.2, "vram_gb": 6,
        "title": "Live — картинка рисуется, пока вы печатаете",
        "simple": "Очень быстрый набросок: печатаете слово — картинка появляется почти сразу. Качество проще.",
        "details": "Модель SD-Turbo и маленький переводчик с русского на английский, чтобы можно было писать по-русски.",
        "needs": ["core", "safety"], "license": "НЕкоммерческая лицензия Stability AI (SD-Turbo)",
        "repos": [
            {"repo": "stabilityai/sd-turbo", "min_gb": 2.4, "allow": ["model_index.json", "scheduler/*", "tokenizer/*", "*/config.json",
                                                      "text_encoder/model.fp16.safetensors",
                                                      "unet/diffusion_pytorch_model.fp16.safetensors",
                                                      "vae/diffusion_pytorch_model.fp16.safetensors"]},
            {"repo": "madebyollin/taesd", "min_gb": 0.005, "ignore": SMALL_IGNORE},
            {"repo": "Helsinki-NLP/opus-mt-ru-en", "min_gb": 0.25, "ignore": SMALL_IGNORE},
        ],
    },
    {
        "id": "anime", "kind": "hf", "required": False, "recommended": False, "size_gb": 7.4, "vram_gb": 8,
        "title": "Аниме-генератор Animagine XL",
        "simple": "Рисует аниме и арт. Если вам нужны только реалистичные картинки, эту часть можно пропустить.",
        "details": "Скачивается ~7 ГБ. Фото-генератор для аниме не нужен, это отдельная модель.",
        "needs": ["core", "safety"], "license": "OpenRAIL++ (Cagliostro Lab)",
        "repos": [
            {"repo": "cagliostrolab/animagine-xl-3.1", "min_gb": 6.6, "allow": [
                "model_index.json", "scheduler/*", "tokenizer/*", "tokenizer_2/*", "text_encoder/config.json",
                "text_encoder/model.safetensors", "text_encoder_2/config.json", "text_encoder_2/model.safetensors",
                "unet/config.json", "unet/diffusion_pytorch_model.safetensors", "vae/config.json",
                "vae/diffusion_pytorch_model.safetensors"]},
            {"repo": "madebyollin/sdxl-vae-fp16-fix", "min_gb": 0.3, "allow": ["config.json", "diffusion_pytorch_model.safetensors"]},
        ],
    },
    {
        "id": "video", "kind": "hf", "required": False, "recommended": False, "size_gb": 4.6, "vram_gb": 8,
        "title": "Видео из картинки (Stable Video Diffusion)",
        "simple": "Оживляет картинку: делает из неё короткое видео. Одно видео делается 3–10 минут.",
        "details": "Качество среднее: руки и лица могут «плыть». Если видео вам не нужно, пропустите.",
        "needs": ["core", "safety"], "license": "НЕкоммерческая лицензия Stability AI",
        "repos": [
            {"repo": "stabilityai/stable-video-diffusion-img2vid-xt", "min_gb": 4.4, "allow": [
                "model_index.json", "scheduler/*", "feature_extractor/*", "*/config.json",
                "image_encoder/model.fp16.safetensors", "unet/diffusion_pytorch_model.fp16.safetensors",
                "vae/diffusion_pytorch_model.fp16.safetensors"]},
        ],
    },
]

PRESETS = {
    "min": {"title": "Только чат и код", "desc": "Самое маленькое. Картинок и видео не будет.",
            "ids": []},
    "rec": {"title": "Рекомендуемый", "desc": "Чат, код, фото-генератор, редактор и Live. То, что нужно большинству.",
            "ids": ["image", "editor", "live"]},
    "all": {"title": "Всё", "desc": "Вообще всё, включая аниме и видео. Нужно много места.",
            "ids": ["image", "editor", "live", "anime", "video"]},
}

BY_ID = {c["id"]: c for c in COMPONENTS}


def with_dependencies(ids):
    """Выбранные компоненты + всё, без чего они не работают (в порядке установки)."""
    want = set(ids) | {c["id"] for c in COMPONENTS if c["required"]}
    changed = True
    while changed:
        changed = False
        for cid in list(want):
            for d in BY_ID[cid]["needs"]:
                if d not in want:
                    want.add(d)
                    changed = True
    return [c["id"] for c in COMPONENTS if c["id"] in want]
