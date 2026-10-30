"""Аккаунты Codwe AI: вход по почте (код из письма), роли, баны, условия использования, проверка целостности.

Как это устроено
  * Пользователи, роли и баны живут в облаке (Supabase). Приложение только проверяет, пустит ли облако этого человека.
  * Секретных ключей облака в программе НЕТ: используется публичный (publishable) ключ. Админ-действия выполняются
    от имени самого администратора, а права проверяет база данных (RLS и функции в supabase/schema.sql).
  * Сервер приложения не пускает к /api/* без входа, без принятых условий и если файлы программы изменены.

Честно о границах: программа работает на компьютере пользователя, а исходный код открыт. Человек, который сам
правит свою копию, может убрать проверку у себя. Бан и вход гарантированно работают только там, где решает облако.
"""
import hashlib
import json
import os
import random
import re
import secrets
import threading
import time
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
CFG_FILE = HERE / "cloud_config.json"
SESSION_FILE = HERE / "auth_session.json"
MANIFEST_FILE = HERE / "integrity.json"
DEV = (HERE / "dev.flag").exists() or os.environ.get("CODWE_DEV") == "1"
AUTH_OFF = os.environ.get("CODWE_AUTH") == "0"
TOS_VERSION = 1
TOS_MIN_SECONDS = 20
PROFILE_TTL = 90          # как часто перепроверяем профиль (бан срабатывает не позже чем через столько секунд)
OFFLINE_GRACE = 12 * 3600  # сколько часов можно работать без связи с облаком

router = APIRouter(prefix="/api/auth")
admin_router = APIRouter(prefix="/api/admin")

# файлы, чьё изменение считается вмешательством
GUARDED = ["server.py", "auth_api.py", "safety.py", "policy.enc", "safety.sig", "engine.py", "workspace_api.py",
           "codwecode_api.py", "zimage_engine.py", "klein_engine.py", "launcher.py",
           "web/index.html", "web/app.js", "web/workspace.js", "web/auth.js", "web/style.css"]


# ───────── целостность ─────────
def _sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()  # CRLF/LF не мешает
    except OSError:
        return ""


def build_manifest():
    data = {f: _sha(HERE / f) for f in GUARDED}
    MANIFEST_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


_integ = {"t": 0.0, "ok": True, "reason": ""}


def integrity():
    """-> (ok, причина). В режиме разработчика (файл dev.flag) не проверяется. Результат кешируется на минуту."""
    if DEV:
        return True, ""
    if time.time() - _integ["t"] < 60:
        return _integ["ok"], _integ["reason"]
    ok, reason = True, ""
    try:
        exp = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        exp = None
    if not exp:
        ok, reason = False, "Не найден файл проверки целостности (integrity.json)."
    else:
        bad = [f for f in GUARDED if exp.get(f) != _sha(HERE / f)]
        if bad:
            ok, reason = False, "Изменены файлы программы: " + ", ".join(bad[:4]) + ("…" if len(bad) > 4 else "")
    _integ.update(t=time.time(), ok=ok, reason=reason)
    return ok, reason


# ───────── облако ─────────
def cfg():
    try:
        d = json.loads(CFG_FILE.read_text(encoding="utf-8"))
        return d.get("url", "").rstrip("/"), d.get("anon_key", "")
    except Exception:
        return "", ""


def configured():
    u, k = cfg()
    return bool(u and k)


def required():
    """Вход обязателен всегда (кроме режима разработчика CODWE_AUTH=0). Нет облака -> нужен «код подключения»."""
    return not AUTH_OFF


def make_connect_code():
    import base64
    u, k = cfg()
    return "CODWE1." + base64.urlsafe_b64encode(json.dumps({"u": u, "k": k}).encode()).decode().rstrip("=")


def parse_connect_code(code):
    import base64
    code = (code or "").strip()
    if not code.startswith("CODWE1."):
        raise ValueError("Код должен начинаться с CODWE1.")
    b = code[7:]
    d = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)).decode())
    u, k = str(d["u"]).rstrip("/"), str(d["k"])
    if not re.fullmatch(r"https://[a-z0-9]{10,30}\.supabase\.co", u) or len(k) < 20 or len(k) > 400:
        raise ValueError("Код подключения неверный.")
    return u, k


def _sb(method, path, token=None, **kw):
    url, key = cfg()
    headers = {"apikey": key, "Authorization": "Bearer " + (token or key), "Content-Type": "application/json"}
    headers.update(kw.pop("headers", {}))
    try:
        return requests.request(method, url + path, headers=headers, timeout=15, **kw)
    except requests.RequestException as e:
        raise HTTPException(503, "Нет связи с облаком: " + str(e)[:120])


def _err(r, default):
    try:
        j = r.json()
        msg = j.get("msg") or j.get("message") or j.get("error_description") or j.get("error") or default
        code = j.get("error_code") or j.get("code") or ""
    except Exception:
        msg, code = default, ""
    return str(msg), str(code)


_L = threading.RLock()
SESS = {}   # access, refresh, exp, uid, email, profile, profile_ts, ok_ts
try:
    SESS.update(json.loads(SESSION_FILE.read_text(encoding="utf-8")))
except Exception:
    pass


def _save():
    try:
        SESSION_FILE.write_text(json.dumps(SESS), encoding="utf-8")
    except OSError:
        pass


def _clear(reason=""):
    SESS.clear()
    if reason:
        SESS["last_error"] = reason
    _save()


def _store_session(d):
    SESS.update(access=d["access_token"], refresh=d.get("refresh_token", SESS.get("refresh")),
                exp=d.get("expires_at") or int(time.time()) + int(d.get("expires_in", 3600)),
                uid=d["user"]["id"], email=d["user"].get("email", ""))
    SESS.pop("last_error", None)


def _load_profile(force=False):
    """Профиль из облака (роль, бан, условия). Бан немедленно выкидывает пользователя."""
    if not force and SESS.get("profile") and time.time() - SESS.get("profile_ts", 0) < PROFILE_TTL:
        return
    r = _sb("GET", f"/rest/v1/profiles?id=eq.{SESS['uid']}&select=*", SESS["access"])
    if r.status_code == 404 or (r.status_code == 200 and not r.json()):
        raise HTTPException(500, "В облаке не настроена база (запустите supabase/schema.sql в SQL Editor).")
    if r.status_code != 200:
        raise HTTPException(503, _err(r, "Не удалось получить профиль")[0])
    p = r.json()[0]
    if "access_ok" in p and not p["access_ok"] and p.get("role") != "admin":
        # если пароль доступа не задан, облако пропускает пустой пароль и отмечает доступ открытым
        r2 = _sb("POST", "/rest/v1/rpc/check_access_password", SESS["access"], data=json.dumps({"pw": ""}))
        if r2.status_code == 200 and r2.json() is True:
            r3 = _sb("GET", f"/rest/v1/profiles?id=eq.{SESS['uid']}&select=*", SESS["access"])
            if r3.status_code == 200 and r3.json():
                p = r3.json()[0]
    SESS.update(profile=p, profile_ts=time.time(), ok_ts=time.time())
    if p.get("banned"):
        why = p.get("ban_reason") or "нарушение правил"
        _clear("banned:" + why)
        raise HTTPException(403, f"Аккаунт заблокирован: {why}.")
    try:  # «я здесь»
        _sb("POST", "/rest/v1/rpc/touch_seen", SESS["access"], data="{}")
    except HTTPException:
        pass
    _save()


def _refresh_if_needed():
    if SESS.get("exp", 0) - time.time() > 90:
        return
    r = _sb("POST", "/auth/v1/token?grant_type=refresh_token", None, data=json.dumps({"refresh_token": SESS.get("refresh", "")}))
    if r.status_code == 200:
        _store_session(r.json())
        _save()
    elif 400 <= r.status_code < 500:
        _clear("Сессия истекла, войдите снова." if r.status_code != 403 else "banned:доступ закрыт администратором")
        raise HTTPException(401, "Сессия истекла. Войдите снова.")
    else:
        raise HTTPException(503, "Облако недоступно.")


def refresh_state():
    """Освежает токен и профиль. При обрыве связи работает по кешу до OFFLINE_GRACE."""
    with _L:
        if not SESS.get("access"):
            return
        try:
            _refresh_if_needed()
            _load_profile()
        except HTTPException as e:
            if e.status_code == 503 and SESS.get("access") and time.time() - SESS.get("ok_ts", 0) < OFFLINE_GRACE:
                return
            if e.status_code in (401, 403):
                return
            raise


# ───────── состояние для интерфейса и ворота ─────────
def state():
    ok_i, why_i = integrity()
    out = {"required": required(), "configured": configured(), "dev": DEV, "tampered": not ok_i, "tamper_reason": why_i,
           "logged_in": False, "email": "", "role": "user", "tos_ok": False, "tos_version": TOS_VERSION,
           "banned": False, "notice": "", "access_ok": False, "needs_connect": False}
    if required() and not configured():
        out["needs_connect"] = True
        return out
    if not required():
        out.update(logged_in=True, tos_ok=True, access_ok=True)
        return out
    with _L:
        try:
            refresh_state()
        except HTTPException as e:
            out["notice"] = str(e.detail)
        if SESS.get("access") and SESS.get("profile"):
            p = SESS["profile"]
            out.update(logged_in=True, email=SESS.get("email", ""), role=p.get("role", "user"),
                       tos_ok=p.get("tos_version") == TOS_VERSION,
                       access_ok=("access_ok" not in p) or bool(p.get("access_ok")) or p.get("role") == "admin")
        else:
            le = SESS.get("last_error", "")
            if le.startswith("banned:"):
                out.update(banned=True, notice="Аккаунт заблокирован: " + le[7:] + ".")
            elif le:
                out["notice"] = le
    return out


def gate():
    """None — можно; иначе (код, текст, http-статус). Вызывается для всех /api/* кроме /api/auth/*."""
    ok_i, why_i = integrity()
    if not ok_i:
        return "tamper", why_i, 403
    if not required():
        return None
    if not configured():
        return "connect_required", "Введите код подключения.", 401
    s = state()
    if s["banned"]:
        return "banned", s["notice"], 403
    if not s["logged_in"]:
        return "auth_required", s["notice"] or "Войдите в аккаунт.", 401
    if not s["access_ok"]:
        return "access_required", "Нужен пароль доступа.", 401
    if not s["tos_ok"]:
        return "tos_required", "Примите условия использования.", 401
    return None


# ───────── вход по почте ─────────
EMAIL_RX = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")
DISPOSABLE = {"mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.com", "temp-mail.org", "yopmail.com",
              "trashmail.com", "sharklasers.com", "getnada.com", "dispostable.com", "maildrop.cc", "throwawaymail.com",
              "fakeinbox.com", "mailnesia.com", "tempmailo.com", "emailondeck.com", "mohmal.com", "mintemail.com"}
_sent = {}


class SendReq(BaseModel):
    email: str = Field(min_length=5, max_length=120)


class VerifyReq(BaseModel):
    email: str = Field(min_length=5, max_length=120)
    code: str = Field(min_length=6, max_length=10)


@router.get("/state")
def api_state():
    return state()


@router.post("/send-code")
def api_send_code(req: SendReq):
    if not configured():
        raise HTTPException(400, "Сначала введите код подключения.")
    email = req.email.strip().lower()
    if not EMAIL_RX.match(email):
        raise HTTPException(400, "Похоже, почта написана с ошибкой.")
    if email.split("@")[1] in DISPOSABLE:
        raise HTTPException(400, "Одноразовые почты не поддерживаются. Используйте обычную.")
    now = time.time()
    if now - _sent.get(email, 0) < 45:
        raise HTTPException(429, f"Код уже отправлен. Подождите {int(45 - (now - _sent[email])) + 1} с и попробуйте снова.")
    r = _sb("POST", "/auth/v1/otp", None, data=json.dumps({"email": email, "create_user": True}))
    if r.status_code not in (200, 204):
        msg, code = _err(r, "Не удалось отправить письмо.")
        if r.status_code == 429 or "rate" in code or "rate limit" in msg.lower():
            raise HTTPException(429, "Слишком много писем за короткое время. Подождите несколько минут.")
        if "signups" in msg.lower() and "disabled" in msg.lower():
            raise HTTPException(403, "Регистрация сейчас закрыта.")
        raise HTTPException(400, msg)
    _sent[email] = now
    return {"ok": True}


@router.post("/verify")
def api_verify(req: VerifyReq):
    email, code = req.email.strip().lower(), re.sub(r"\D", "", req.code)
    if len(code) < 6:
        raise HTTPException(400, "Введите код из письма (6–8 цифр).")
    r = _sb("POST", "/auth/v1/verify", None, data=json.dumps({"type": "email", "email": email, "token": code}))
    if r.status_code != 200:
        msg, _c = _err(r, "Неверный или устаревший код.")
        if r.status_code == 403 and "ban" in msg.lower():
            raise HTTPException(403, "Аккаунт заблокирован.")
        raise HTTPException(400, "Неверный или устаревший код. Запросите новый.")
    with _L:
        _clear()
        _store_session(r.json())
        try:
            _load_profile(force=True)
        except HTTPException:
            _clear()
            raise
        _save()
    return state()


class LinkSession(BaseModel):
    access_token: str = Field(min_length=20, max_length=4000)
    refresh_token: str = Field(default="", max_length=400)


def _jwt_exp(token):
    try:
        import base64
        body = token.split(".")[1]
        return int(json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))).get("exp", 0))
    except Exception:
        return 0


@router.post("/session")
def api_link_session(req: LinkSession):
    """Вход по ссылке из письма: страница получает токен из адреса и передаёт его сюда. Токен проверяется в облаке."""
    if not required():
        raise HTTPException(400, "Облако не настроено.")
    r = _sb("GET", "/auth/v1/user", req.access_token)
    if r.status_code != 200:
        raise HTTPException(400, "Ссылка устарела или недействительна. Запросите новое письмо.")
    u = r.json()
    with _L:
        _clear()
        SESS.update(access=req.access_token, refresh=req.refresh_token, uid=u["id"], email=u.get("email", ""),
                    exp=_jwt_exp(req.access_token) or int(time.time()) + 3000)
        try:
            _load_profile(force=True)
        except HTTPException:
            _clear()
            raise
        _save()
    return state()


class ConnectReq(BaseModel):
    code: str = Field(min_length=20, max_length=800)


@router.post("/connect")
def api_connect(req: ConnectReq):
    """«Код подключения» выдаёт администратор: так адрес и публичный ключ облака не лежат в открытом репозитории."""
    if configured():
        raise HTTPException(400, "Подключение уже настроено.")
    try:
        u, k = parse_connect_code(req.code)
    except Exception as e:  # noqa
        raise HTTPException(400, str(e) if str(e).startswith("Код") else "Код подключения неверный.")
    try:
        r = requests.get(u + "/auth/v1/settings", headers={"apikey": k}, timeout=15)
    except requests.RequestException:
        raise HTTPException(503, "Нет связи с облаком. Проверьте интернет.")
    if r.status_code != 200 or "external" not in r.text:
        raise HTTPException(400, "Код подключения не подошёл (облако его не приняло).")
    CFG_FILE.write_text(json.dumps({"url": u, "anon_key": k}), encoding="utf-8")
    return state()


@router.post("/logout")
def api_logout():
    with _L:
        _clear()
    return state()


class AccessReq(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.post("/access")
def api_access(req: AccessReq):
    """Пароль доступа: проверяется внутри облачной базы (в программе его хеша нет), с паузой и блокировкой перебора."""
    with _L:
        if not SESS.get("access"):
            raise HTTPException(401, "Сначала войдите в аккаунт.")
        _refresh_if_needed()
    r = _sb("POST", "/rest/v1/rpc/check_access_password", SESS["access"], data=json.dumps({"pw": req.password}))
    if r.status_code != 200:
        msg = _err(r, "")[0]
        if "locked" in msg:
            raise HTTPException(403, "Слишком много неверных попыток. Обратитесь к администратору.")
        if "too_fast" in msg:
            raise HTTPException(429, "Слишком часто. Подождите 3 секунды.")
        raise HTTPException(503, "Не удалось проверить пароль: " + msg[:100])
    if r.json() is not True:
        raise HTTPException(400, "Неверный пароль доступа.")
    with _L:
        _load_profile(force=True)
    return state()


# ───────── условия использования + проверка «прочитал ли» ─────────
TOS_SECTIONS = [
    ("Что это", "Codwe AI — программа, которая работает на вашем компьютере. Картинки, видео, чаты и промпты обрабатываются "
                "и хранятся у вас и никуда не отправляются."),
    ("Что хранится в облаке", "Только ваша почта, роль (пользователь или администратор), статус блокировки и время последнего входа. "
                              "Ни ваши картинки, ни чаты, ни промпты в облако не попадают."),
    ("Что запрещено", "Сексуальный контент с несовершеннолетними (блокируется всегда и не обходится), откровенный 18+ контент и "
                      "жестокий gore (блокируются автоматически), всё, что нарушает закон, и попытки обойти защиту."),
    ("Ответственность", "Вы сами отвечаете за то, что создаёте и как используете результат. Программа и модели даются «как есть», без "
                        "гарантий. У моделей свои лицензии (Qwen, Z-Image, FLUX.2 klein, SD-Turbo и др.): некоммерческие модели нельзя "
                        "использовать в коммерческих целях."),
    ("Блокировка", "За нарушение правил или попытку взлома аккаунт может быть заблокирован без предупреждения. Любое изменение файлов "
                   "программы выключает её."),
    ("Ваши данные", "Чтобы удалить аккаунт и почту из облака, напишите администратору. Вход делается кодом из письма."),
]
QUIZ = [
    {"id": "where", "q": "Где обрабатываются ваши картинки и чаты?",
     "opts": [("pc", "На вашем компьютере"), ("srv", "На сервере разработчика"), ("cloud", "В облаке большой компании")], "ok": "pc"},
    {"id": "cloud", "q": "Что хранится в облаке?",
     "opts": [("mail", "Почта, роль и статус блокировки"), ("img", "Ваши картинки"), ("chat", "Ваши чаты и промпты")], "ok": "mail"},
    {"id": "banned", "q": "Что запрещено всегда и не обходится?",
     "opts": [("minor", "Сексуальный контент с несовершеннолетними"), ("cats", "Рисовать котов"), ("ru", "Писать по-русски")], "ok": "minor"},
]
_tos = {}


class TosAccept(BaseModel):
    nonce: str = Field(min_length=10, max_length=80)
    answers: dict[str, str] = Field(default_factory=dict)


@router.get("/tos")
def api_tos():
    """Условия + вопросы. Время чтения считает СЕРВЕР: меньше TOS_MIN_SECONDS с момента показа — ошибка и показ с начала."""
    nonce = secrets.token_urlsafe(18)
    for k in [k for k, v in _tos.items() if time.time() - v["t"] > 3600]:
        _tos.pop(k, None)
    _tos[nonce] = {"t": time.time()}
    quiz = []
    for q in QUIZ:
        opts = [{"id": i, "text": t} for i, t in q["opts"]]
        random.shuffle(opts)
        quiz.append({"id": q["id"], "q": q["q"], "opts": opts})
    random.shuffle(quiz)
    return {"version": TOS_VERSION, "min_seconds": TOS_MIN_SECONDS, "nonce": nonce,
            "sections": [{"title": a, "text": b} for a, b in TOS_SECTIONS], "quiz": quiz}


@router.post("/tos/accept")
def api_tos_accept(req: TosAccept):
    with _L:
        if not SESS.get("access"):
            raise HTTPException(401, "Сначала войдите в аккаунт.")
    t = _tos.pop(req.nonce, None)
    if not t:
        raise HTTPException(400, "restart:Сессия чтения устарела. Условия показаны заново.")
    if time.time() - t["t"] < TOS_MIN_SECONDS:
        raise HTTPException(400, "restart:Слишком быстро. Условия нужно прочитать — они показаны заново с начала.")
    for q in QUIZ:
        if req.answers.get(q["id"]) != q["ok"]:
            raise HTTPException(400, "restart:Есть неверные ответы — прочитайте условия внимательнее. Они показаны заново.")
    r = _sb("POST", "/rest/v1/rpc/accept_tos", SESS["access"], data=json.dumps({"v": TOS_VERSION}))
    if r.status_code not in (200, 204):
        raise HTTPException(503, _err(r, "Не удалось сохранить согласие.")[0])
    with _L:
        _load_profile(force=True)
    return state()


# ───────── админ-панель (права проверяет база данных) ─────────
def _need_admin():
    with _L:
        if not SESS.get("access"):
            raise HTTPException(401, "Войдите в аккаунт.")
        _refresh_if_needed()
        _load_profile(force=True)
        if SESS["profile"].get("role") != "admin":
            raise HTTPException(403, "Нужны права администратора.")


class BanReq(BaseModel):
    id: str = Field(pattern=r"^[0-9a-fA-F\-]{36}$")
    banned: bool
    reason: str = Field(default="", max_length=200)


class IdReq(BaseModel):
    id: str = Field(pattern=r"^[0-9a-fA-F\-]{36}$")


class AccessPwReq(BaseModel):
    password: str = Field(min_length=8, max_length=200)


@admin_router.post("/access-password")
def admin_access_password(req: AccessPwReq):
    _need_admin()
    r = _sb("POST", "/rest/v1/rpc/admin_set_access_password", SESS["access"], data=json.dumps({"pw": req.password}))
    if r.status_code not in (200, 204):
        raise HTTPException(400, _err(r, "Не удалось задать пароль (запущен ли supabase/access_password.sql?)")[0])
    return {"ok": True}


@admin_router.post("/access-clear")
def admin_access_clear():
    _need_admin()
    r = _sb("POST", "/rest/v1/rpc/admin_clear_access_password", SESS["access"], data="{}")
    if r.status_code not in (200, 204):
        raise HTTPException(400, _err(r, "Не удалось снять пароль")[0])
    return {"ok": True}


@admin_router.post("/access-reset")
def admin_access_reset(req: IdReq):
    _need_admin()
    r = _sb("POST", "/rest/v1/rpc/admin_reset_access", SESS["access"], data=json.dumps({"target": req.id}))
    if r.status_code not in (200, 204):
        raise HTTPException(400, _err(r, "Не удалось сбросить")[0])
    return {"ok": True}


class RoleReq(BaseModel):
    id: str = Field(pattern=r"^[0-9a-fA-F\-]{36}$")
    role: str = Field(pattern="^(user|admin)$")


@admin_router.get("/connect-code")
def admin_connect_code():
    _need_admin()
    return {"code": make_connect_code()}


@admin_router.get("/users")
def admin_users():
    _need_admin()
    r = _sb("GET", "/rest/v1/profiles?select=*&order=created_at.desc&limit=500",
            SESS["access"])
    if r.status_code != 200:
        raise HTTPException(503, _err(r, "Не удалось получить список")[0])
    return {"users": r.json(), "me": SESS.get("uid")}


@admin_router.post("/ban")
def admin_ban(req: BanReq):
    _need_admin()
    r = _sb("POST", "/rest/v1/rpc/admin_set_ban", SESS["access"],
            data=json.dumps({"target": req.id, "flag": req.banned, "reason": req.reason or None}))
    if r.status_code not in (200, 204):
        raise HTTPException(400, _err(r, "Не удалось изменить блокировку")[0])
    return {"ok": True}


@admin_router.post("/role")
def admin_role(req: RoleReq):
    _need_admin()
    r = _sb("POST", "/rest/v1/rpc/admin_set_role", SESS["access"], data=json.dumps({"target": req.id, "new_role": req.role}))
    if r.status_code not in (200, 204):
        raise HTTPException(400, _err(r, "Не удалось изменить роль")[0])
    return {"ok": True}
