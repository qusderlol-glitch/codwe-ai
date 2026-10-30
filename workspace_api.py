"""Рабочая папка (вкладка «Код») и терминал.

Безопасность:
  * Все файловые операции ограничены папкой workspace/ (без .., без абсолютных путей, без выхода через ссылки).
  * Терминал выключен по умолчанию и включается только явным согласием пользователя (состояние подписано).
  * Команды выполняются от вашего имени, поэтому им нужен одноразовый токен сессии (его видит только страница,
    открытая с этого же адреса) и они проходят простой фильтр очевидно разрушительных команд.
    Это НЕ песочница: терминал может всё, что можете вы. Включайте его осознанно.
"""
import hmac
import json
import os
import re
import secrets
import signal
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import safety

HERE = Path(__file__).resolve().parent
ROOT = HERE / "workspace"
ROOT.mkdir(exist_ok=True)
(ROOT / "default").mkdir(exist_ok=True)
TERM_STATE = HERE / "terminal_state.json"
SESSION_TOKEN = secrets.token_urlsafe(24)
MAX_FILE = 1_500_000
HIDE = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

router = APIRouter()


def safe_path(rel: str, must_exist=False) -> Path:
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    if re.match(r"^[A-Za-z]:", rel) or ".." in rel.split("/") or "\x00" in rel:
        raise HTTPException(400, "Недопустимый путь.")
    root = ROOT.resolve()
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise HTTPException(400, "Путь вне рабочей папки.")
    if must_exist and not p.exists():
        raise HTTPException(404, "Не найдено.")
    return p


def rel_of(p: Path) -> str:
    return p.resolve().relative_to(ROOT.resolve()).as_posix() if p.resolve() != ROOT.resolve() else ""


@router.get("/api/session")
def api_session():
    return {"token": SESSION_TOKEN}


# ───────── файлы ─────────
@router.get("/api/ws/tree")
def ws_tree(path: str = ""):
    d = safe_path(path, must_exist=True)
    if not d.is_dir():
        raise HTTPException(400, "Это не папка.")
    items = []
    for e in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if e.name in HIDE or e.name.startswith("."):
            continue
        try:
            items.append({"name": e.name, "type": "dir" if e.is_dir() else "file",
                          "size": e.stat().st_size if e.is_file() else 0})
        except OSError:
            continue
        if len(items) >= 600:
            break
    return {"path": rel_of(d), "items": items}


@router.get("/api/ws/file")
def ws_read(path: str):
    p = safe_path(path, must_exist=True)
    if not p.is_file():
        raise HTTPException(400, "Это не файл.")
    if p.stat().st_size > MAX_FILE:
        raise HTTPException(413, "Файл слишком большой для редактора (больше 1,5 МБ).")
    data = p.read_bytes()
    try:
        return {"path": rel_of(p), "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        raise HTTPException(415, "Это не текстовый файл.")


class WriteReq(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(max_length=MAX_FILE)


@router.put("/api/ws/file")
def ws_write(req: WriteReq):
    p = safe_path(req.path)
    if p.is_dir():
        raise HTTPException(400, "Это папка.")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.content, encoding="utf-8", newline="")
    return {"ok": True, "path": rel_of(p)}


class PathReq(BaseModel):
    path: str = Field(min_length=1, max_length=300)


@router.post("/api/ws/mkdir")
def ws_mkdir(req: PathReq):
    safe_path(req.path).mkdir(parents=True, exist_ok=True)
    return {"ok": True}


@router.post("/api/ws/delete")
def ws_delete(req: PathReq):
    p = safe_path(req.path, must_exist=True)
    if p == ROOT.resolve() or p == (ROOT / "default").resolve():
        raise HTTPException(400, "Эту папку удалить нельзя.")
    if p.is_dir():
        import shutil
        shutil.rmtree(p)
    else:
        p.unlink()
    return {"ok": True}


class RenameReq(BaseModel):
    src: str = Field(min_length=1, max_length=300)
    dst: str = Field(min_length=1, max_length=300)


@router.post("/api/ws/rename")
def ws_rename(req: RenameReq):
    a, b = safe_path(req.src, must_exist=True), safe_path(req.dst)
    if b.exists():
        raise HTTPException(409, "Такой файл уже есть.")
    b.parent.mkdir(parents=True, exist_ok=True)
    a.rename(b)
    return {"ok": True}


# ───────── терминал ─────────
def _sig(enabled):
    return hmac.new(safety._key(b"term-consent"), json.dumps({"e": bool(enabled)}).encode(), "sha256").hexdigest()


def term_enabled():
    try:
        d = json.loads(TERM_STATE.read_text(encoding="utf-8"))
        return bool(d["enabled"]) and hmac.compare_digest(d.get("sig", ""), _sig(d["enabled"]))
    except Exception:
        return False


class TermState(BaseModel):
    enabled: bool
    confirm: bool = False


@router.get("/api/term/state")
def term_state():
    return {"enabled": term_enabled()}


@router.post("/api/term/state")
def term_set(req: TermState):
    if req.enabled and not req.confirm:
        raise HTTPException(400, "Нужно подтвердить, что вы понимаете риски терминала.")
    TERM_STATE.write_text(json.dumps({"enabled": req.enabled, "sig": _sig(req.enabled)}), encoding="utf-8")
    return {"enabled": term_enabled()}


DENY = re.compile(
    r"(\bformat\s+[a-z]:|\bdiskpart\b|\bbcdedit\b|\bcipher\s+/w|\bmkfs\b|\bdd\s+if=|\bshutdown\b|\brestart-computer\b|"
    r"\bstop-computer\b|\breg\s+(delete|add)\b|\bremove-item\b[^\n]*\b[a-z]:\\\s*(-recurse|\*)|"
    r"\b(rd|rmdir|del|erase)\b[^\n]*\s[a-z]:\\\s*($|/|\*)|\brm\s+-rf?\s+(/|~|\*)|\bnet\s+user\b|\bschtasks\s+/create\b|"
    r"\bvssadmin\b|\bwevtutil\s+cl\b|\bsc\s+(delete|config)\b)", re.I)

_procs = {}
_plock = threading.Lock()


class RunReq(BaseModel):
    cmd: str = Field(min_length=1, max_length=4000)
    cwd: str = Field(default="default", max_length=300)
    timeout: int = Field(default=120, ge=5, le=900)
    rid: str = Field(default="", max_length=40)


def _check(request: Request):
    if request.headers.get("x-codwe-token") != SESSION_TOKEN:
        raise HTTPException(403, "Нет токена сессии. Обновите страницу.")
    if not term_enabled():
        raise HTTPException(403, "Терминал выключен. Включите его в самой вкладке «Код» (с подтверждением).")


@router.post("/api/term/run")
def term_run(req: RunReq, request: Request):
    _check(request)
    if DENY.search(req.cmd):
        raise HTTPException(403, "🛑 Эта команда похожа на разрушительную и заблокирована.")
    cwd = safe_path(req.cwd, must_exist=True)
    if not cwd.is_dir():
        raise HTTPException(400, "Рабочая папка не найдена.")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    # команда пишется во временный .cmd в UTF-8 (с chcp 65001), иначе русские буквы превращаются в «?»
    tmpdir = ROOT / ".codwe_tmp"
    tmpdir.mkdir(exist_ok=True)
    script = tmpdir / f"run_{secrets.token_hex(4)}.cmd"
    script.write_bytes(("@echo off\r\nchcp 65001>nul\r\n" + req.cmd + "\r\n").encode("utf-8"))
    proc = subprocess.Popen(["cmd.exe", "/d", "/c", str(script)], cwd=str(cwd), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            creationflags=NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    rid = req.rid or secrets.token_hex(4)
    with _plock:
        _procs[rid] = proc
    deadline = time.time() + req.timeout

    def gen():
        try:
            yield json.dumps({"rid": rid, "cwd": rel_of(cwd)}) + "\n"
            buf = b""
            timer = threading.Timer(req.timeout, lambda: proc.kill())
            timer.start()
            try:
                while True:
                    chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    # отдаём только законченные UTF-8 последовательности
                    try:
                        text = buf.decode("utf-8")
                        buf = b""
                    except UnicodeDecodeError as e:
                        text = buf[:e.start].decode("utf-8", "replace")
                        buf = buf[e.start:]
                        if len(buf) > 8:
                            text += buf.decode("utf-8", "replace")
                            buf = b""
                    if text:
                        yield json.dumps({"o": text}) + "\n"
            finally:
                timer.cancel()
            code = proc.wait()
            note = " (остановлено по времени)" if time.time() > deadline else ""
            yield json.dumps({"exit": code, "note": note}) + "\n"
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                script.unlink()
            except OSError:
                pass
            with _plock:
                _procs.pop(rid, None)

    return StreamingResponse(gen(), media_type="application/x-ndjson")


class KillReq(BaseModel):
    rid: str = Field(min_length=1, max_length=40)


@router.post("/api/term/kill")
def term_kill(req: KillReq, request: Request):
    _check(request)
    with _plock:
        p = _procs.get(req.rid)
    if p and p.poll() is None:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, creationflags=NO_WINDOW)
        except Exception:
            p.kill()
    return {"ok": True}
