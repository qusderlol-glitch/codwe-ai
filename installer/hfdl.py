"""Скачивание файлов модели с Hugging Face с прогрессом. Запускается отдельным процессом, чтобы его можно было остановить.
    python hfdl.py <репозиторий> '<json: allow>' '<json: ignore>'
Печатает строки JSON: {"total": байт} / {"done": байт, "total": байт, "file": имя} / {"ok": true} / {"error": "..."}.
Докачивает с места остановки: неоконченные файлы лежат как *.incomplete."""
import fnmatch
import json
import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def main():
    repo = sys.argv[1]
    allow = json.loads(sys.argv[2]) if len(sys.argv) > 2 else []
    ignore = json.loads(sys.argv[3]) if len(sys.argv) > 3 else []
    from huggingface_hub import HfApi, hf_hub_download, try_to_load_from_cache

    info = HfApi().model_info(repo, files_metadata=True)
    files = []
    for s in info.siblings:
        f = s.rfilename
        if allow and not any(fnmatch.fnmatch(f, p) for p in allow):
            continue
        if any(fnmatch.fnmatch(f, p) for p in ignore):
            continue
        files.append((f, s.size or 0))
    total = sum(sz for _f, sz in files)
    out(total=total, files=len(files))

    blobs = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub" / ("models--" + repo.replace("/", "--")) / "blobs"
    state = {"done": 0, "file": "", "stop": False}

    def watcher():
        while not state["stop"]:
            part = 0
            try:
                for p in blobs.glob("*.incomplete"):
                    if not (blobs / p.name.split(".")[0]).exists():  # старые остатки уже скачанных файлов не считаем
                        part += p.stat().st_size
            except OSError:
                pass
            out(done=min(total, state["done"] + part), total=total, file=state["file"])
            time.sleep(1.0)

    threading.Thread(target=watcher, daemon=True).start()
    try:
        for f, sz in files:
            state["file"] = f
            cached = try_to_load_from_cache(repo, f)
            if isinstance(cached, str) and os.path.exists(cached):
                state["done"] += sz
                continue
            hf_hub_download(repo, f)
            state["done"] += sz
    except Exception as e:  # noqa
        state["stop"] = True
        out(error=f"{type(e).__name__}: {str(e)[:300]}")
        sys.exit(2)
    state["stop"] = True
    out(done=total, total=total, file="")
    out(ok=True)


if __name__ == "__main__":
    main()
