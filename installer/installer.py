"""Мастер установки Codwe AI: проверяет компьютер, даёт выбрать, что ставить, и ставит с понятным прогрессом.
Работает на 127.0.0.1:8766 и показывает окно-мастер. Запускается через "Install Codwe AI.bat".
    python installer\\installer.py            — обычный запуск (открывает окно)
    python installer\\installer.py --check    — только проверка компьютера (текстом)
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
MODELS = ROOT / "models"
MODELS.mkdir(exist_ok=True)
os.environ["HF_HOME"] = str(MODELS)
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

from catalog import BY_ID, COMPONENTS, PRESETS, with_dependencies  # noqa: E402

PORT = 8766
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PY = sys.executable
PYW = str(Path(PY).with_name("pythonw.exe")) if Path(PY).with_name("pythonw.exe").exists() else PY
OLLAMA_URL = "http://127.0.0.1:11434"
MARK = MODELS / ".installed"
MARK.mkdir(exist_ok=True)


# ───────────────────────── проверка компьютера ─────────────────────────
def sh(cmd, timeout=12):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa
        return 1, str(e)


def gpu_info():
    code, out = sh(["nvidia-smi", "--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv,noheader,nounits"])
    if code != 0 or not out.strip():
        return None
    try:
        name, mem, drv, cc = [x.strip() for x in out.strip().splitlines()[0].split(",")]
        return {"name": name, "vram_gb": round(int(mem) / 1024, 1), "driver": drv, "cc": float(cc)}
    except Exception:
        return None


def ollama_exe():
    p = shutil.which("ollama")
    if p:
        return p
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
        q = Path(base) / "Programs" / "Ollama" / "ollama.exe" if base else None
        if q and q.exists():
            return str(q)
        q = Path(base) / "Ollama" / "ollama.exe" if base else None
        if q and q.exists():
            return str(q)
    return ""


def edge_exe():
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", "")):
        q = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe" if base else None
        if q and q.exists():
            return str(q)
    return ""


def net_ok(url):
    try:
        import requests
        requests.head(url, timeout=6, allow_redirects=True)
        return True
    except Exception:
        return False


def row(id_, title, value, level, simple, fix=""):
    return {"id": id_, "title": title, "value": value, "level": level, "simple": simple, "fix": fix}


def check():
    import platform
    rows = []
    # Windows
    if os.name != "nt":
        rows.append(row("os", "Система", platform.platform(), "bad", "Мастер сделан для Windows 10 и 11.", "Запустите на компьютере с Windows."))
    else:
        build = int(platform.version().split(".")[-1]) if platform.version().split(".")[-1].isdigit() else 0
        rows.append(row("os", "Система", f"Windows {platform.release()} (сборка {build})", "ok" if build >= 19041 else "warn",
                        "Windows 10 или 11 подходят." if build >= 19041 else "У вас старая Windows — что-то может не заработать.",
                        "" if build >= 19041 else "Обновите Windows через «Центр обновления»."))
    # Python
    v = sys.version_info
    ok = (3, 10) <= (v.major, v.minor) <= (3, 13)
    rows.append(row("python", "Python", f"{v.major}.{v.minor}.{v.micro}", "ok" if ok else "warn",
                    "Python — язык, на котором написана программа. Подходит." if ok else "Эта версия Python слишком новая или старая — нужная библиотека может не установиться.",
                    "" if ok else "Поставьте Python 3.12 (python.org) и запустите мастер заново."))
    # Ollama
    ox = ollama_exe()
    rows.append(row("ollama", "Ollama (мотор умного чата)", "установлена" if ox else "не установлена", "ok" if ox else "warn",
                    "Ollama запускает умный чат. Она уже есть." if ox else "Ollama запускает умный чат. Её пока нет на этом компьютере.",
                    "" if ox else "Мастер скачает и поставит её сам (около 1,7 ГБ). Можно поставить заранее вручную по кнопке."))
    # видеокарта
    g = gpu_info()
    if not g:
        rows.append(row("gpu", "Видеокарта NVIDIA", "не найдена", "bad",
                        "Видеокарта нужна для картинок и видео. Без NVIDIA картинки и видео работать НЕ будут, а чат будет очень медленным.",
                        "Можно поставить только чат, но лучше запускать на компьютере с видеокартой NVIDIA (8 ГБ и больше)."))
    else:
        vr = g["vram_gb"]
        lvl = "ok" if vr >= 7.5 else "warn" if vr >= 5.5 else "bad"
        simple = {"ok": "Видеопамяти достаточно для всех функций.",
                  "warn": "Видеопамяти маловато: фото-генератор и редактор могут не работать. Чат и Live обычно работают.",
                  "bad": "Видеопамяти мало. Картинки и видео работать не будут, только чат."}[lvl]
        rows.append(row("gpu", "Видеокарта NVIDIA", f"{g['name']} ({vr} ГБ памяти)", lvl, simple,
                        "" if lvl == "ok" else "Закройте игры и браузеры во время работы. Для картинок нужно 8 ГБ."))
        try:
            major = int(g["driver"].split(".")[0])
        except Exception:
            major = 0
        if major and major < 551:
            rows.append(row("driver", "Драйвер видеокарты", g["driver"], "warn",
                            "Драйвер старый — программа может не запуститься на видеокарте.",
                            "Скачайте новый драйвер на nvidia.com/drivers и перезагрузите компьютер."))
        else:
            rows.append(row("driver", "Драйвер видеокарты", g["driver"], "ok", "Драйвер подходит."))
        if g["cc"] >= 12.0:
            rows.append(row("rtx50", "Видеокарта RTX 50-й серии", f"архитектура {g['cc']}", "warn",
                            "Для таких видеокарт мы ставим более новую версию PyTorch. Мы её не проверяли — возможны сбои.",
                            "Если после установки картинки не создаются, напишите администратору."))
    # память
    try:
        import psutil
        ram = psutil.virtual_memory().total / 1024 ** 3
    except Exception:
        ram = 0
    lvl = "ok" if ram >= 14 else "warn" if ram >= 11 else "bad"
    rows.append(row("ram", "Оперативная память", f"{ram:.0f} ГБ", lvl,
                    {"ok": "Памяти достаточно.", "warn": "Памяти впритык: во время картинок закройте лишние программы.",
                     "bad": "Памяти мало: программа может вылетать."}[lvl],
                    "" if lvl == "ok" else "Закрывайте браузер и игры, пока работает Codwe AI."))
    # диск
    free = shutil.disk_usage(ROOT).free / 1024 ** 3
    rows.append(row("disk", "Свободное место на диске", f"{free:.0f} ГБ свободно", "ok" if free >= 25 else "warn" if free >= 15 else "bad",
                    "Модели большие: чат занимает ~13 ГБ, всё вместе до ~48 ГБ.",
                    "" if free >= 25 else "Освободите место на диске или поставьте программу на другой диск."))
    # интернет
    hf, ol = net_ok("https://huggingface.co"), net_ok("https://ollama.com")
    rows.append(row("net", "Интернет", "есть" if hf and ol else "нет или частично", "ok" if hf and ol else "bad",
                    "Интернет нужен, чтобы скачать модели." if hf and ol else "Мастер не достучался до сайтов с моделями.",
                    "" if hf and ol else "Проверьте интернет и отключите VPN, если он мешает. Потом нажмите «Проверить заново»."))
    # прочее
    code, _o = sh(["winget", "--version"])
    rows.append(row("winget", "Установщик программ winget", "есть" if code == 0 else "нет", "ok" if code == 0 else "warn",
                    "Есть. Для Codwe AI он не обязателен." if code == 0 else "Не обязателен: Ollama мастер скачивает напрямую с официального сайта.",
                    ""))
    rows.append(row("edge", "Браузер Microsoft Edge", "есть" if edge_exe() else "нет", "ok" if edge_exe() else "warn",
                    "Нужен, чтобы окно программы открывалось и чтобы Codwecode проверял игры." if edge_exe() else "Без Edge окно программы не откроется красиво, а Codwecode не сможет проверять игры.",
                    "" if edge_exe() else "Скачайте Microsoft Edge с microsoft.com/edge."))
    return rows, g, free


# ───────────────────────── что уже установлено ─────────────────────────
def repo_dir(repo):
    return MODELS / "hub" / ("models--" + repo.replace("/", "--"))


def repo_ok(spec):
    d = repo_dir(spec["repo"])
    blobs = d / "blobs"
    if not (d / "snapshots").exists() or not blobs.exists():
        return False
    try:
        # недокачанный файл — это *.incomplete, у которого ещё нет готового «близнеца» (старые остатки не считаются)
        if any(not (blobs / q.name.split(".")[0]).exists() for q in blobs.glob("*.incomplete")):
            return False
        total = sum(p.stat().st_size for p in blobs.iterdir() if p.is_file())
    except OSError:
        return False
    return total >= spec.get("min_gb", 0) * 1e9 * 0.95


def ollama_has(model):
    try:
        import requests
        r = requests.get(OLLAMA_URL + "/api/tags", timeout=3).json()
        return any(m["name"] == model for m in r.get("models", []))
    except Exception:
        return False


def installed():
    import importlib.util as iu
    res = {}
    res["core"] = all(iu.find_spec(m) for m in ("torch", "diffusers", "transformers", "fastapi", "playwright"))
    res["ollama"] = bool(ollama_exe())
    res["chat"] = ollama_has(BY_ID["chat"]["model"])
    for c in COMPONENTS:
        if c["kind"] == "hf":
            ok = all(repo_ok(r) for r in c["repos"])
            if c["id"] == "image":
                ok = ok and (MODELS / "zimage-te-nf4").exists()
            res[c["id"]] = ok
    return res


# ───────────────────────── установка ─────────────────────────
ST = {"status": "idle", "steps": [], "log": [], "cancel": False, "error": "", "started": 0, "finished": 0, "summary": ""}
LOCK = threading.RLock()
PROC = {"p": None}


def log(msg):
    with LOCK:
        ST["log"].append(time.strftime("%H:%M:%S ") + str(msg)[:400])
        del ST["log"][:-300]


def step(id_):
    return next(s for s in ST["steps"] if s["id"] == id_)


def upd(id_, **kw):
    with LOCK:
        step(id_).update(kw)


class Cancelled(Exception):
    pass


def check_cancel():
    if ST["cancel"]:
        raise Cancelled()


def run_proc(cmd, id_=None, parse=None, env=None, cwd=None, timeout=None):
    """Запускает команду, читает вывод построчно (в журнал и в parse). Можно остановить кнопкой «Остановить»."""
    e = dict(os.environ, **(env or {}))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         cwd=str(cwd or ROOT), env=e, creationflags=NO_WINDOW, bufsize=1)
    PROC["p"] = p
    t0 = time.time()
    try:
        for line in p.stdout:
            line = line.rstrip()
            if line:
                if parse:
                    parse(line)
                elif len(line) < 300:
                    log(line)
            if ST["cancel"]:
                p.terminate()
                raise Cancelled()
            if timeout and time.time() - t0 > timeout:
                p.terminate()
                raise RuntimeError("слишком долго, остановлено")
        p.wait()
    finally:
        PROC["p"] = None
        if p.poll() is None:
            p.terminate()
    return p.returncode


class Speed:
    """Скорость и оставшееся время по росту скачанных байт."""
    def __init__(self):
        self.t, self.b, self.v = time.time(), 0, 0.0

    def feed(self, done, total):
        now = time.time()
        if now - self.t >= 1.0:
            inst = (done - self.b) / (now - self.t)
            self.v = inst if self.v == 0 else self.v * 0.7 + inst * 0.3
            self.t, self.b = now, done
        eta = int((total - done) / self.v) if self.v > 1000 else 0
        return self.v, eta


def torch_spec(g):
    if not g:
        return ("torch==2.6.0", "torchvision==0.21.0", "https://download.pytorch.org/whl/cpu")
    if g["cc"] >= 12.0:
        return ("torch==2.7.1", "torchvision==0.22.1", "https://download.pytorch.org/whl/cu128")
    return ("torch==2.6.0", "torchvision==0.21.0", "https://download.pytorch.org/whl/cu124")


def do_core():
    id_ = "core"
    g = gpu_info()
    t, tv, idx = torch_spec(g)
    sp = Speed()
    cur = {"name": ""}

    def parse(line):
        if line.startswith("Progress "):
            try:
                a, b = line.split()[1], line.split()[3]
                done, total = int(a), int(b)
                v, eta = sp.feed(done, total)
                upd(id_, pct=round(done / max(total, 1) * 100, 1), note=f"Скачивается {cur['name']}", speed=v, eta=eta)
            except Exception:
                pass
            return
        if line.startswith("Downloading "):
            cur["name"] = line.split()[1].split("/")[-1][:40]
        if len(line) < 300 and not line.startswith("Progress"):
            log(line)

    upd(id_, note="Скачиваю PyTorch (самая большая часть)…", pct=1)
    cmd = [PY, "-m", "pip", "install", "--progress-bar", "raw", "--disable-pip-version-check", t, tv, "--index-url", idx]
    if run_proc(cmd, id_, parse) != 0:
        raise RuntimeError("не удалось поставить PyTorch")
    upd(id_, note="Ставлю остальные библиотеки…", pct=60, speed=0, eta=0)
    cmd = [PY, "-m", "pip", "install", "--progress-bar", "raw", "--disable-pip-version-check", "-r", str(ROOT / "requirements.txt")]
    if run_proc(cmd, id_, parse) != 0:
        raise RuntimeError("не удалось поставить библиотеки из requirements.txt")
    upd(id_, pct=100, note="Готово", speed=0, eta=0)


OLLAMA_SETUP = "https://ollama.com/download/OllamaSetup.exe"


def download_file(url, dest, id_, label, stall=45, tries=6):
    """Скачивает файл с живым прогрессом. Если данные перестали идти (stall секунд) или связь оборвалась,
    докачивает с того же места (до tries раз), а не висит молча."""
    import requests
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    sp = Speed()
    for attempt in range(1, tries + 1):
        have = part.stat().st_size if part.exists() else 0
        try:
            hdr = {"Range": f"bytes={have}-"} if have else {}
            with requests.get(url, headers=hdr, stream=True, timeout=(15, stall), allow_redirects=True) as r:
                if r.status_code == 416:  # файл уже докачан целиком
                    break
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"сервер ответил {r.status_code}")
                if r.status_code == 200 and have:  # сервер не поддержал докачку: начинаем сначала
                    have = 0
                    part.unlink(missing_ok=True)
                total = have + int(r.headers.get("Content-Length") or 0)
                done = have
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        check_cancel()
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        v, eta = sp.feed(done, total or done)
                        upd(id_, pct=round(min(done / max(total, 1), 1) * 88, 1), speed=v, eta=eta,
                            note=f"{label}: {done / 1e9:.2f} из {total / 1e9:.2f} ГБ" + (f" · попытка {attempt}" if attempt > 1 else ""))
                if total and part.stat().st_size < total:
                    raise RuntimeError("файл скачан не полностью")
            break
        except Cancelled:
            raise
        except Exception as e:  # noqa
            log(f"{label}: обрыв ({str(e)[:120]}), попытка {attempt} из {tries}. Докачиваю с того же места…")
            upd(id_, note=f"{label}: связь оборвалась, повторяю ({attempt} из {tries})…", speed=0, eta=0)
            if attempt == tries:
                raise RuntimeError("не удалось скачать: " + str(e)[:150])
            for _ in range(4 * attempt):
                check_cancel()
                time.sleep(1)
    part.replace(dest)
    return dest


def is_signed_by(path, name):
    """Проверяем цифровую подпись скачанного установщика: запускаем только файл, подписанный разработчиком."""
    ps = (f"$s=Get-AuthenticodeSignature -LiteralPath '{path}'; if($s.Status -eq 'Valid'){{$s.SignerCertificate.Subject}}else{{'INVALID '+$s.Status}}")
    code, out = sh(["powershell", "-NoProfile", "-Command", ps], timeout=90)
    return code == 0 and name.lower() in out.lower()


def do_ollama():
    id_ = "ollama"
    if ollama_exe():
        return
    setup = MODELS / "_downloads" / "OllamaSetup.exe"
    ok = False
    try:
        upd(id_, note="Скачиваю Ollama с официального сайта ollama.com…", pct=1)
        if not (setup.exists() and setup.stat().st_size > 500_000_000):
            download_file(OLLAMA_SETUP, setup, id_, "Ollama")
        if not is_signed_by(setup, "Ollama"):
            setup.unlink(missing_ok=True)
            raise RuntimeError("у скачанного файла нет подписи Ollama, запускать его небезопасно")
        upd(id_, note="Устанавливаю Ollama (небольшое окно установки закроется само)…", pct=90, speed=0, eta=0)
        p = subprocess.Popen([str(setup), "/SILENT", "/NORESTART"], creationflags=NO_WINDOW)
        t0 = time.time()
        while p.poll() is None and time.time() - t0 < 900:
            check_cancel()
            time.sleep(1)
        for _ in range(30):
            if ollama_exe():
                break
            time.sleep(1)
        ok = bool(ollama_exe())
    except Cancelled:
        raise
    except Exception as e:  # noqa
        log(f"Автоустановка Ollama не получилась: {str(e)[:160]}")
    if not ok and not ollama_exe():
        webbrowser.open("https://ollama.com/download/windows")
        upd(id_, note="Автоматически не вышло. Откройте страницу ollama.com (мы её открыли), скачайте и установите Ollama. Мастер продолжит сам.", pct=10, speed=0, eta=0)
        for _ in range(900):  # ждём до 15 минут
            check_cancel()
            if ollama_exe():
                break
            time.sleep(1)
    if not ollama_exe():
        raise RuntimeError("Ollama так и не появилась")
    upd(id_, pct=100, note="Готово", speed=0, eta=0)


def ollama_up():
    try:
        import requests
        requests.get(OLLAMA_URL + "/api/tags", timeout=2)
        return True
    except Exception:
        return False


def ensure_ollama_running():
    if ollama_up():
        return True
    exe = ollama_exe()
    if not exe:
        return False
    subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    for _ in range(60):
        if ollama_up():
            return True
        time.sleep(1)
    return False


def do_chat():
    import requests
    id_ = "chat"
    model = BY_ID[id_]["model"]
    if not ensure_ollama_running():
        raise RuntimeError("Ollama не запускается")
    if ollama_has(model):
        return
    sp = Speed()
    layers = {}
    for attempt in range(1, 9):
        try:
            with requests.post(OLLAMA_URL + "/api/pull", json={"model": model, "stream": True}, stream=True, timeout=(10, 60)) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    check_cancel()
                    if not line:
                        continue
                    d = json.loads(line)
                    if "error" in d:
                        raise RuntimeError(str(d["error"])[:200])
                    if d.get("digest") and d.get("total"):
                        layers[d["digest"]] = (d.get("completed", 0), d["total"])
                    done = sum(a for a, _b in layers.values())
                    total = sum(b for _a, b in layers.values()) or 1
                    v, eta = sp.feed(done, total)
                    note = f"{done / 1e9:.1f} из {total / 1e9:.1f} ГБ · {d.get('status', '')[:40]}" + (f" · попытка {attempt}" if attempt > 1 else "")
                    upd(id_, pct=round(done / total * 100, 1), note=note, speed=v, eta=eta)
            break
        except Cancelled:
            raise
        except (requests.RequestException, ValueError) as e:
            # данные перестали идти (60 с) или связь оборвалась: Ollama умеет докачивать, просто просим снова
            log(f"Скачивание чата замерло или оборвалось ({str(e)[:100]}). Повторяю, докачка с того же места ({attempt} из 8)…")
            upd(id_, note=f"Связь оборвалась, повторяю ({attempt} из 8)…", speed=0, eta=0)
            if attempt == 8:
                raise RuntimeError("скачивание много раз обрывалось: проверьте интернет и отключите VPN, затем нажмите «Повторить»")
            for _ in range(5 * attempt):
                check_cancel()
                time.sleep(1)
            ensure_ollama_running()
    if not ollama_has(model):
        raise RuntimeError("модель не появилась после скачивания")
    upd(id_, pct=100, note="Готово", speed=0, eta=0)


def do_hf(c):
    id_ = c["id"]
    repos = c["repos"]
    est = [r.get("min_gb", 0.1) * 1024 ** 3 for r in repos]
    finished = 0.0
    sp = Speed()
    for i, r in enumerate(repos):
        check_cancel()
        for attempt in range(1, 4):
            state = {"done": 0, "total": est[i], "file": "", "ok": False, "err": ""}

            def parse(line, state=state, i=i):
                try:
                    d = json.loads(line)
                except Exception:
                    return
                if "total" in d and "done" not in d:
                    state["total"] = d["total"] or state["total"]
                if "done" in d:
                    state["done"], state["total"], state["file"] = d["done"], d.get("total") or state["total"], d.get("file", "")
                if d.get("ok"):
                    state["ok"] = True
                if d.get("error"):
                    state["err"] = d["error"]
                rest = sum(est[i + 1:])
                tot = finished + state["total"] + rest
                done = finished + state["done"]
                v, eta = sp.feed(done, tot)
                upd(id_, pct=round(done / max(tot, 1) * 100, 1), note=f"{r['repo'].split('/')[-1]}: {state['file'][-40:]}", speed=v, eta=eta)

            cmd = [PY, str(Path(__file__).with_name("hfdl.py")), r["repo"], json.dumps(r.get("allow", [])), json.dumps(r.get("ignore", []))]
            rc = run_proc(cmd, id_, parse)
            if state["ok"]:
                break
            log(f"{r['repo']}: попытка {attempt} не удалась ({state['err'] or 'код ' + str(rc)})")
            if attempt == 3:
                raise RuntimeError(f"не удалось скачать {r['repo']}: {state['err'] or 'ошибка сети'}")
            for _ in range(5 * attempt):
                check_cancel()
                time.sleep(1)
        finished += state["total"]
    upd(id_, pct=100, note="Готово", speed=0, eta=0)


def do_post(kind):
    """Доводка после скачивания. Если не вышла — не страшно, программа сделает это при первом запуске."""
    code = {
        "zimage_te": "import zimage_engine as z; z.ZImage()._make_te_nf4()",
        "warm_safety": "import safety; safety._load_models()",
        "warm_marian": "from transformers import MarianMTModel, MarianTokenizer as T; T.from_pretrained('Helsinki-NLP/opus-mt-ru-en'); MarianMTModel.from_pretrained('Helsinki-NLP/opus-mt-ru-en')",
    }[kind]
    rc = run_proc([PY, "-c", code], kind, lambda l: log(l) if len(l) < 200 else None, cwd=ROOT, timeout=1800)
    if rc != 0:
        raise RuntimeError("не получилось сейчас (ничего страшного — программа сделает это при первом запуске)")


def make_shortcut():
    if os.environ.get("CODWE_INSTALLER_TEST"):
        return True  # при проверках ярлык на рабочем столе не трогаем
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    lnk = desktop / "Codwe AI.lnk"
    ico = ROOT / "web" / "codwe.ico"
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');$s.TargetPath='{PYW}';"
          f"$s.Arguments='\"{ROOT / 'launcher.py'}\"';$s.WorkingDirectory='{ROOT}';"
          + (f"$s.IconLocation='{ico}';" if ico.exists() else "") + "$s.Save()")
    sh(["powershell", "-NoProfile", "-Command", ps])
    return lnk.exists()


def title_of(id_):
    return {"zimage_te": "Подготовка фото-генератора (один раз)", "warm_safety": "Подготовка защитных проверок",
            "warm_marian": "Подготовка переводчика", "shortcut": "Ярлык на рабочем столе"}.get(id_) or BY_ID[id_]["title"]


def worker(ids):
    with LOCK:
        ST.update(status="running", cancel=False, error="", started=time.time(), finished=0, log=[], summary="")
    already = installed()
    order = with_dependencies(ids)
    steps = []
    for cid in order:
        steps.append({"id": cid, "title": title_of(cid), "state": "skip" if already.get(cid) else "wait", "pct": 100 if already.get(cid) else 0,
                      "note": "уже установлено" if already.get(cid) else "ждёт очереди", "speed": 0, "eta": 0, "optional": not BY_ID[cid]["required"]})
        if cid == "safety" and not already.get(cid):
            steps.append({"id": "warm_safety", "title": title_of("warm_safety"), "state": "wait", "pct": 0, "note": "ждёт очереди", "speed": 0, "eta": 0, "optional": True})
        if cid == "image" and not already.get(cid):
            steps.append({"id": "zimage_te", "title": title_of("zimage_te"), "state": "wait", "pct": 0, "note": "ждёт очереди", "speed": 0, "eta": 0, "optional": True})
        if cid == "live" and not already.get(cid):
            steps.append({"id": "warm_marian", "title": title_of("warm_marian"), "state": "wait", "pct": 0, "note": "ждёт очереди", "speed": 0, "eta": 0, "optional": True})
    steps.append({"id": "shortcut", "title": title_of("shortcut"), "state": "wait", "pct": 0, "note": "ждёт очереди", "speed": 0, "eta": 0, "optional": True})
    with LOCK:
        ST["steps"] = steps
    failed = []
    try:
        for s in list(steps):
            if s["state"] == "skip":
                continue
            check_cancel()
            sid = s["id"]
            upd(sid, state="run", note="начинаю…")
            try:
                if sid == "core":
                    do_core()
                elif sid == "ollama":
                    do_ollama()
                elif sid == "chat":
                    do_chat()
                elif sid in ("zimage_te", "warm_safety", "warm_marian"):
                    do_post(sid)
                elif sid == "shortcut":
                    ok = make_shortcut()
                    upd(sid, note="Ярлык создан" if ok else "Ярлык не создался (запускайте через Install… или launcher)")
                else:
                    do_hf(BY_ID[sid])
                upd(sid, state="ok", pct=100, speed=0, eta=0)
                if sid in BY_ID and BY_ID[sid]["kind"] == "hf":
                    (MARK / f"{sid}.json").write_text(json.dumps({"t": time.time()}), encoding="utf-8")
            except Cancelled:
                raise
            except Exception as e:  # noqa
                msg = str(e)[:240]
                log(f"ОШИБКА {sid}: {msg}")
                upd(sid, state="fail", note=msg, speed=0, eta=0)
                failed.append(sid)
                if sid in BY_ID and BY_ID[sid]["required"]:
                    raise RuntimeError(f"Не получилось установить «{title_of(sid)}»: {msg}")
        with LOCK:
            hard = [f for f in failed if f in BY_ID]
            ST["status"] = "done" if not hard else "error"
            ST["summary"] = "Всё установлено." if not failed else "Установлено, но кое-что не получилось (см. список). Можно нажать «Повторить»."
            ST["finished"] = time.time()
    except Cancelled:
        with LOCK:
            ST.update(status="cancelled", finished=time.time(), summary="Остановлено. Всё скачанное сохранено — можно продолжить позже с того же места.")
            for s in ST["steps"]:
                if s["state"] in ("run", "wait"):
                    s["state"], s["note"] = "wait", "остановлено"
    except Exception as e:  # noqa
        with LOCK:
            ST.update(status="error", error=str(e)[:300], finished=time.time(), summary=str(e)[:300])


# ───────────────────────── окно-мастер (сервер) ─────────────────────────
def build_app():
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, PlainTextResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    allowed = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if (request.headers.get("host") or "").lower() not in allowed:
            return PlainTextResponse("forbidden", status_code=403)
        if request.method not in ("GET", "HEAD") and request.headers.get("x-codwe") != "1":
            return PlainTextResponse("forbidden", status_code=403)
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    class StartReq(BaseModel):
        ids: list[str] = Field(default_factory=list, max_length=20)

    class IdReq(BaseModel):
        id: str = Field(max_length=40)

    @app.get("/api/check")
    def api_check():
        rows, g, free = check()
        inst = installed()
        vram = g["vram_gb"] if g else 0
        rec = ["image", "editor", "live"] if vram >= 7.5 else ["live"] if vram >= 5.5 else []
        comps = []
        for c in COMPONENTS:
            d = {k: v for k, v in c.items() if k not in ("repos",)}
            d["installed"] = bool(inst.get(c["id"]))
            d["vram_ok"] = (vram >= c.get("vram_gb", 0)) if c.get("vram_gb") else True
            comps.append(d)
        return {"rows": rows, "gpu": g, "free_gb": round(free, 1), "components": comps, "recommended": rec,
                "presets": PRESETS, "root": str(ROOT), "state": ST["status"]}

    @app.post("/api/start")
    def api_start(req: StartReq):
        if ST["status"] == "running":
            raise HTTPException(409, "Установка уже идёт.")
        ids = [i for i in req.ids if i in BY_ID]
        order = with_dependencies(ids)
        inst = installed()
        need = sum(BY_ID[i]["size_gb"] for i in order if not inst.get(i))
        free = shutil.disk_usage(ROOT).free / 1024 ** 3
        if need * 1.05 + 2 > free:
            raise HTTPException(400, f"Не хватает места: нужно около {need:.0f} ГБ, свободно {free:.0f} ГБ. Уберите галочки или освободите место.")
        threading.Thread(target=worker, args=(ids,), daemon=True).start()
        time.sleep(0.3)
        return {"ok": True, "order": order}

    @app.get("/api/progress")
    def api_progress():
        with LOCK:
            return {"status": ST["status"], "steps": ST["steps"], "log": ST["log"][-60:], "error": ST["error"],
                    "summary": ST["summary"], "elapsed": int((ST["finished"] or time.time()) - ST["started"]) if ST["started"] else 0}

    @app.post("/api/cancel")
    def api_cancel():
        ST["cancel"] = True
        p = PROC.get("p")
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
        return {"ok": True}

    @app.post("/api/remove")
    def api_remove(req: IdReq):
        c = BY_ID.get(req.id)
        if not c or c["required"] and c["kind"] != "ollama_model":
            raise HTTPException(400, "Это нельзя удалить: без него программа не работает.")
        if ST["status"] == "running":
            raise HTTPException(409, "Дождитесь конца установки.")
        freed = 0.0
        if c["kind"] == "hf":
            for r in c["repos"]:
                d = repo_dir(r["repo"])
                if d.exists():
                    freed += sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) / 1024 ** 3
                    shutil.rmtree(d, ignore_errors=True)
            if c["id"] == "image":
                shutil.rmtree(MODELS / "zimage-te-nf4", ignore_errors=True)
            (MARK / f"{c['id']}.json").unlink(missing_ok=True)
        elif c["kind"] == "ollama_model":
            raise HTTPException(400, "Умный помощник нужен для всей программы — его удалять нельзя.")
        return {"ok": True, "freed_gb": round(freed, 1)}

    @app.post("/api/launch")
    def api_launch():
        subprocess.Popen([PYW, str(ROOT / "launcher.py")], cwd=str(ROOT), creationflags=NO_WINDOW | 0x00000008)
        return {"ok": True}

    @app.post("/api/open")
    def api_open(req: IdReq):
        urls = {"ollama": "https://ollama.com/download/windows", "driver": "https://www.nvidia.com/Download/index.aspx",
                "winget": "https://apps.microsoft.com/detail/9nblggh4nns1",
                "edge": "https://www.microsoft.com/edge", "python": "https://www.python.org/downloads/"}
        if req.id not in urls:
            raise HTTPException(400, "нельзя")
        webbrowser.open(urls[req.id])
        return {"ok": True}

    @app.post("/api/exit")
    def api_exit():
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(Path(__file__).with_name("web") / "index.html")

    app.mount("/static", StaticFiles(directory=str(Path(__file__).with_name("web"))), name="static")
    return app


def open_window():
    url = f"http://127.0.0.1:{PORT}/"
    for b in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", "")):
        exe = Path(b) / "Microsoft" / "Edge" / "Application" / "msedge.exe" if b else None
        if exe and exe.exists():
            subprocess.Popen([str(exe), f"--app={url}", "--window-size=1100,900"])
            return
    webbrowser.open(url)


if __name__ == "__main__":
    if "--check" in sys.argv:
        rows, g, free = check()
        for r in rows:
            print(f"[{r['level'].upper():4}] {r['title']}: {r['value']} — {r['simple']}")
        print("Установлено:", {k: v for k, v in installed().items()})
        sys.exit(0)
    import uvicorn
    if "--no-window" not in sys.argv:
        threading.Timer(1.5, open_window).start()
    print(f"Мастер установки Codwe AI: http://127.0.0.1:{PORT}  (не закрывайте это окно, пока идёт установка)", flush=True)
    uvicorn.run(build_app(), host="127.0.0.1", port=PORT, log_level="warning")
