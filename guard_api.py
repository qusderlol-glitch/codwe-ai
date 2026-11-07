"""Страж страницы: серверная часть («третий замок»).

Что делает
  * Страница (web/guard.js) каждые 15 секунд присылает подписанный «пульс». Подпись (HMAC) выдаётся при загрузке страницы.
  * Если страница сообщила о вмешательстве (правка текста/картинки, открыт F12, подмена кода), сервер закрывает доступ
    к /api/* и к вашим файлам на 2 минуты, потом на 10, потом на час.
  * Если страница была, а пульс пропал (кто-то вырубил таймеры), доступ тоже закрывается, пока страница не оживёт.
  * Владелец и ранги с правом dev.console, а также режим разработчика (файл dev.flag) работают в режиме «наблюдение»:
    всё пишется в журнал DEV, но ничего не блокируется и скример не показывается.

Честно: это защита от случайных и любопытных, а не от человека, который контролирует свой компьютер. Настоящая защита
(вход, бан, ранги, права) находится в облаке и в проверках сервера; браузерная часть лишь делает вмешательство заметным.
"""
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import auth_api
import devlog

router = APIRouter(prefix="/api/guard")
BEAT_EVERY = 15
SILENCE = 150            # столько секунд без пульса от страницы = страница «оглушена»
FORGET = 900             # страницу, молчащую дольше, считаем закрытой
LOCK_STEPS = [120, 600, 3600]
_L = threading.Lock()
SESSIONS = {}            # sid -> {"key": bytes, "seq": int, "last": float, "a": int, "b": int, "stall": int}
G = {"locked_until": 0.0, "strikes": 0, "reason": ""}


def observe():
    """True — только наблюдаем (без блокировок и скримера)."""
    try:
        return bool(auth_api.DEV) or "dev.console" in auth_api.my_perms()
    except Exception:
        return False


def _prune(now):
    for sid in [s for s, v in SESSIONS.items() if now - v["last"] > FORGET]:
        SESSIONS.pop(sid, None)


def locked_for():
    return max(0, int(G["locked_until"] - time.time()))


def gate(sid=None, api=True):
    """None — можно; иначе (код, текст, статус). Вызывается из общих ворот сервера.
    Для запросов к /api/* страница обязана показать пропуск стража (заголовок X-Guard): без живого стража работать нельзя."""
    if observe():
        return None
    now = time.time()
    with _L:
        if api and not (sid and sid in SESSIONS):
            return "guard", "Страж безопасности не запущен. Перезагрузите страницу (Ctrl+R).", 403
        if now < G["locked_until"]:
            return "guard", f"Обнаружено вмешательство в страницу. Доступ закрыт ещё на {int(G['locked_until'] - now) + 1} с.", 403
        _prune(now)
        if SESSIONS and not any(now - v["last"] < SILENCE for v in SESSIONS.values()):
            return "guard", "Страж безопасности не отвечает. Перезагрузите страницу (Ctrl+R).", 403
    return None


def _mac(key, *parts):
    return hmac.new(key, "|".join(str(p) for p in parts).encode(), hashlib.sha256).hexdigest()


def _session(sid):
    with _L:
        v = SESSIONS.get(sid)
    if not v:
        raise HTTPException(409, "Страж: сессия не найдена, перезагрузите страницу.")
    return v


@router.post("/hello")
def hello():
    """Страница при загрузке получает свой ключ подписи."""
    try:
        auth_api.state()   # подгружает профиль: владельцу сразу включается режим «наблюдение»
    except Exception:
        pass
    sid, key = secrets.token_hex(8), secrets.token_bytes(32)
    now = time.time()
    with _L:
        _prune(now)
        SESSIONS[sid] = {"key": key, "seq": 0, "last": now, "a": 0, "b": 0, "stall": 0}
    return {"sid": sid, "key": key.hex(), "every": BEAT_EVERY, "observe": observe(), "locked_for": locked_for(), "reason": G["reason"]}


class Beat(BaseModel):
    sid: str = Field(max_length=32)
    seq: int = Field(ge=1, le=10**9)
    ts: int
    a: int = Field(ge=0, le=10**9)       # сколько проверок сделал страж текста и картинок
    b: int = Field(ge=0, le=10**9)       # сколько проверок сделал страж кода
    dt: int = Field(ge=0, le=1)          # 1 — похоже, открыты инструменты разработчика
    mac: str = Field(max_length=64)


@router.post("/beat")
def beat(req: Beat):
    v = _session(req.sid)
    now = time.time()
    if abs(req.ts / 1000 - now) > 300 or req.seq <= v["seq"]:
        raise HTTPException(400, "Страж: неверный пульс.")
    if not hmac.compare_digest(_mac(v["key"], req.sid, req.seq, req.ts, req.a, req.b, req.dt), req.mac):
        _trip(req.sid, "пульс с неверной подписью", "C")
        raise HTTPException(403, "Страж: подпись не совпала.")
    with _L:
        stalled = req.a <= v["a"] or req.b <= v["b"]
        v["stall"] = v["stall"] + 1 if (stalled and v["seq"] > 0) else 0
        v.update(seq=req.seq, last=now, a=req.a, b=req.b)
        stall = v["stall"]
    if stall >= 3:
        _trip(req.sid, "стражи страницы остановлены (проверки не идут)", "C")
    if req.dt:
        _trip(req.sid, "открыты инструменты разработчика", "C")
    return {"ok": True, "observe": observe(), "locked_for": locked_for(), "reason": G["reason"]}


class Trip(BaseModel):
    sid: str = Field(max_length=32)
    seq: int = Field(ge=0, le=10**9)
    ts: int
    layer: str = Field(pattern="^[ABC]$")
    reason: str = Field(max_length=160)
    mac: str = Field(max_length=64)


def _trip(sid, reason, layer):
    if observe():
        devlog.log("guard", f"[наблюдение] страж {'АБВ'['ABC'.index(layer)]}: {reason}")
        return 0
    now = time.time()
    with _L:
        v = SESSIONS.get(sid)
        if v is not None:
            if v.get("tripped"):   # одна страница = одно нарушение (страж и сервер не должны наказывать дважды за одно и то же)
                return 0
            v["tripped"] = True
        G["strikes"] += 1
        sec = LOCK_STEPS[min(G["strikes"] - 1, len(LOCK_STEPS) - 1)]
        G["locked_until"], G["reason"] = now + sec, reason
    devlog.log("guard", f"ВМЕШАТЕЛЬСТВО (страж {'АБВ'['ABC'.index(layer)]}): {reason}. Доступ закрыт на {sec} с (нарушение №{G['strikes']}).")
    return sec


@router.post("/trip")
def trip(req: Trip):
    v = _session(req.sid)
    if not hmac.compare_digest(_mac(v["key"], req.sid, req.seq, req.ts, req.layer, req.reason), req.mac):
        raise HTTPException(403, "Страж: подпись не совпала.")
    sec = _trip(req.sid, req.reason, req.layer)
    return {"ok": True, "observe": observe(), "locked_for": sec or locked_for()}


class Bye(BaseModel):
    sid: str = Field(max_length=32)


@router.post("/bye")
def bye(req: Bye):
    with _L:
        SESSIONS.pop(req.sid, None)
    return {"ok": True}


@router.get("/state")
def state():
    return {"observe": observe(), "locked_for": locked_for(), "reason": G["reason"], "sessions": len(SESSIONS)}


def status_line():
    """Для DEV-консоли."""
    with _L:
        n = len(SESSIONS)
    return {"observe": observe(), "sessions": n, "strikes": G["strikes"], "locked_for": locked_for(), "reason": G["reason"]}
