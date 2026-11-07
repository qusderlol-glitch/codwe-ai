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

from urllib.parse import quote

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import devlog

HERE = Path(__file__).resolve().parent
CFG_FILE = HERE / "cloud_config.json"
SESSION_FILE = HERE / "auth_session.json"
MANIFEST_FILE = HERE / "integrity.json"
DEV = (HERE / "dev.flag").exists() or os.environ.get("CODWE_DEV") == "1"
AUTH_OFF = os.environ.get("CODWE_AUTH") == "0"
TOS_VERSION = 2
TOS_MIN_SECONDS = 20
PROFILE_TTL = 90          # как часто перепроверяем профиль (бан срабатывает не позже чем через столько секунд)
OFFLINE_GRACE = 12 * 3600  # сколько часов можно работать без связи с облаком

router = APIRouter(prefix="/api/auth")
admin_router = APIRouter(prefix="/api/admin")

# файлы, чьё изменение считается вмешательством
GUARDED = ["server.py", "auth_api.py", "safety.py", "policy.enc", "safety.sig", "engine.py", "workspace_api.py",
           "codwecode_api.py", "zimage_engine.py", "klein_engine.py", "launcher.py", "dev_api.py", "devlog.py",
           "web/index.html", "web/app.js", "web/workspace.js", "web/auth.js", "web/account.js", "web/admin.js", "web/aurora.js",
           "web/dev.js", "web/style.css", "web/guard.js", "guard_api.py", "updater.py"]


def guarded():
    """Список защищаемых файлов + вся «зашифрованная» сборка интерфейса (web_dist), если она есть."""
    extra = sorted(p.relative_to(HERE).as_posix() for p in (HERE / "web_dist").glob("*") if p.is_file()) if (HERE / "web_dist").is_dir() else []
    return GUARDED + extra


# ───────── целостность ─────────
def _sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()  # CRLF/LF не мешает
    except OSError:
        return ""


def build_manifest():
    data = {f: _sha(HERE / f) for f in guarded()}
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
        bad = [f for f in guarded() if exp.get(f) != _sha(HERE / f)]
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


# Файлы с входом (токены) хранятся зашифрованными ключом вашей учётной записи Windows (DPAPI): скопированный файл
# на другом компьютере или под другим пользователем Windows ничего не даст. Старые открытые файлы читаются и перезаписываются.
_DP = b"DPAPI1:"


def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    src = BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("dpapi")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _write_secret(path, obj):
    raw = json.dumps(obj).encode("utf-8")
    if os.name == "nt":
        try:
            import base64
            raw = _DP + base64.b64encode(_dpapi(raw, True))
        except Exception:
            pass  # без шифрования лучше, чем без входа
    Path(path).write_bytes(raw)


def _read_secret(path):
    raw = Path(path).read_bytes()
    if raw.startswith(_DP):
        import base64
        raw = _dpapi(base64.b64decode(raw[len(_DP):]), False)
    return json.loads(raw.decode("utf-8"))


try:
    SESS.update(_read_secret(SESSION_FILE))
except Exception:
    pass


ACCOUNTS_FILE = HERE / "auth_accounts.json"


def _acc_load():
    try:
        return _read_secret(ACCOUNTS_FILE)
    except Exception:
        return {}


def _acc_write(d):
    try:
        _write_secret(ACCOUNTS_FILE, d)
    except OSError:
        pass


def _save():
    try:
        _write_secret(SESSION_FILE, SESS)
    except OSError:
        pass
    if SESS.get("email") and SESS.get("refresh"):  # запоминаем аккаунт для быстрого переключения
        d = _acc_load()
        d[SESS["email"]] = {"refresh": SESS["refresh"], "ts": int(time.time())}
        _acc_write(d)


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


# ───────── ранги и права (как роли в Discord) ─────────
# Названия прав на понятном языке: они же показываются в админ-панели и в DEV-консоли.
PERMS = {
    "users.view": "Видеть список пользователей и журнал",
    "users.ban": "Блокировать и разблокировать (только тех, кто ниже рангом)",
    "users.rank": "Выдавать ранги (только ниже своего)",
    "access.manage": "Пароль доступа, сброс доступа, код подключения",
    "models.manage": "Блокировать и разблокировать модели для всех",
    "safety.toggle": "Включать и выключать режим безопасности для всех",
    "dev.console": "Открывать вкладку DEV-консоль",
}
OWNER_KEY = "owner"
# если облако ещё не обновлено файлом supabase/ranks.sql, работаем по-старому: admin / user
LEGACY_RANKS = [
    {"key": "admin", "name": "Администратор", "level": 80, "color": "#ef4444",
     "perms": ["users.view", "users.ban", "users.rank", "access.manage", "dev.console"]},
    {"key": "user", "name": "Пользователь", "level": 10, "color": "#8d95aa", "perms": []},
]
_ranks = {"t": 0.0, "list": None, "legacy": False}


def _ranks_list(force=False):
    """Список рангов из облака (кеш 5 минут). Если таблицы рангов в облаке нет, возвращает запасной список."""
    if not force and _ranks["list"] and time.time() - _ranks["t"] < 300:
        return _ranks["list"]
    r = _sb("GET", "/rest/v1/ranks?select=*&order=level.desc", SESS["access"])
    if r.status_code == 200 and isinstance(r.json(), list) and r.json():
        _ranks.update(t=time.time(), list=r.json(), legacy=False)
    elif r.status_code in (404, 400) or (r.status_code == 200 and not r.json()):
        _ranks.update(t=time.time(), list=LEGACY_RANKS, legacy=True)
    elif not _ranks["list"]:
        raise HTTPException(503, _err(r, "Не удалось получить ранги")[0])
    return _ranks["list"]


def _rank_of(role):
    lst = _ranks["list"] or LEGACY_RANKS  # без сети: берём то, что уже загружено
    return next((x for x in lst if x["key"] == role), None) or next((x for x in lst if x["key"] == "user"), LEGACY_RANKS[-1])


def perms_of(role):
    r = _rank_of(role)
    if r["key"] == OWNER_KEY:
        return list(PERMS)
    return [x for x in (r.get("perms") or []) if x in PERMS]


def my_perms():
    p = SESS.get("profile") or {}
    if p.get("banned"):
        return []
    return perms_of(p.get("role", "user")) if SESS.get("access") else []


def my_level():
    p = SESS.get("profile") or {}
    if p.get("banned"):
        return 0
    return int(_rank_of(p.get("role", "user"))["level"]) if SESS.get("access") else 0


def has_perm(perm):
    return perm in my_perms()


# ───────── политика (общие настройки для всех): заблокированные модели и режим безопасности ─────────
def _load_policy():
    """Забирает из облака список заблокированных моделей и режим безопасности. Нет связи — остаётся прошлое значение."""
    if _ranks["legacy"]:
        return
    r = _sb("POST", "/rest/v1/rpc/get_policy", SESS["access"], data="{}")
    if r.status_code == 200 and isinstance(r.json(), dict):
        j = r.json()
        SESS["policy"] = {"blocked_models": [str(x) for x in (j.get("blocked_models") or [])][:200],
                          "safety_soft": j.get("safety_soft") is not False}


def policy():
    p = SESS.get("policy") or {}
    return {"blocked_models": list(p.get("blocked_models") or []), "safety_soft": p.get("safety_soft", True) is not False}


def model_blocked(model_id):
    if not required():
        return False
    return model_id in (SESS.get("policy") or {}).get("blocked_models", [])


def soft_safety():
    """True — откровенный 18+ и gore блокируются. Выключить может только ранг с правом safety.toggle (через облако)."""
    if not required():
        return True
    return policy()["safety_soft"]


def _load_profile(force=False):
    """Профиль из облака (роль, бан, условия). Бан немедленно выкидывает пользователя."""
    if not force and SESS.get("profile") and _ranks["list"] and time.time() - SESS.get("profile_ts", 0) < PROFILE_TTL:
        return
    r = _sb("GET", f"/rest/v1/profiles?id=eq.{SESS['uid']}&select=*", SESS["access"])
    if r.status_code == 404 or (r.status_code == 200 and not r.json()):
        raise HTTPException(500, "В облаке не настроена база (запустите supabase/schema.sql в SQL Editor).")
    if r.status_code != 200:
        raise HTTPException(503, _err(r, "Не удалось получить профиль")[0])
    p = r.json()[0]
    _ranks_list(force)
    if "access_ok" in p and not p["access_ok"] and "access.manage" not in perms_of(p.get("role", "user")):
        # если пароль доступа не задан, облако пропускает пустой пароль и отмечает доступ открытым
        r2 = _sb("POST", "/rest/v1/rpc/check_access_password", SESS["access"], data=json.dumps({"pw": ""}))
        if r2.status_code == 200 and r2.json() is True:
            r3 = _sb("GET", f"/rest/v1/profiles?id=eq.{SESS['uid']}&select=*", SESS["access"])
            if r3.status_code == 200 and r3.json():
                p = r3.json()[0]
    try:
        _load_policy()
    except HTTPException:
        pass  # нет связи: действует прошлое значение
    SESS.update(profile=p, profile_ts=time.time(), ok_ts=time.time())
    if p.get("banned"):
        _save()  # вход разрешён, чтобы человек увидел причину; работать в программе он не сможет (см. gate)
        return
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
           "banned": False, "ban_reason": "", "notice": "", "access_ok": False, "needs_connect": False,
           "rank_name": "Пользователь", "rank_color": "#8d95aa", "level": 10, "perms": [], "legacy": False,
           "policy": {"blocked_models": [], "safety_soft": True}}
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
            rk = _rank_of(p.get("role", "user"))
            perms = my_perms()
            if p.get("banned"):
                out.update(logged_in=True, email=SESS.get("email", ""), banned=True, ban_reason=p.get("ban_reason") or "причина не указана",
                           notice="ВЫ БЫЛИ ЗАБАНЕНЫ", rank_name="—", level=0, perms=[])
                return out
            out.update(logged_in=True, email=SESS.get("email", ""), role=p.get("role", "user"),
                       rank_name=rk["name"], rank_color=rk.get("color", "#8d95aa"), level=int(rk["level"]), perms=perms,
                       legacy=_ranks["legacy"], policy=policy(),
                       tos_ok=p.get("tos_version") == TOS_VERSION,
                       access_ok=("access_ok" not in p) or bool(p.get("access_ok")) or "access.manage" in perms)
        else:
            le = SESS.get("last_error", "")
            if le.startswith("banned:"):
                out.update(banned=True, ban_reason=le[7:], notice="ВЫ БЫЛИ ЗАБАНЕНЫ")
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


def app_version():
    try:
        return int((HERE / "version.txt").read_text(encoding="utf-8").strip())
    except Exception:
        return 0


@router.get("/whoami")
def api_whoami():
    """Лаунчер по этому ответу убеждается, что на порту именно Codwe AI, а не чужая программа."""
    return {"app": "Codwe AI", "version": app_version()}


@router.get("/state")
def api_state():
    return state()


@router.post("/recheck")
def api_recheck():
    """Кнопка «Проверить снова» на экране бана: сразу спрашивает облако, не сняли ли блокировку."""
    with _L:
        if SESS.get("access"):
            try:
                _refresh_if_needed()
                _load_profile(force=True)
            except HTTPException:
                pass
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
            raise HTTPException(403, "Вход закрыт старой блокировкой облака. Обновите базу файлом supabase/ranks.sql.")
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
    devlog.log("auth", f"Вход выполнен: {email}")
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
    """Выход из текущего аккаунта: сессия закрывается и в облаке, и на этом компьютере, аккаунт забывается."""
    with _L:
        tok, mail = SESS.get("access"), SESS.get("email")
        if tok:
            try:
                _sb("POST", "/auth/v1/logout", tok, data="{}")
            except HTTPException:
                pass
        if mail:
            d = _acc_load()
            d.pop(mail, None)
            _acc_write(d)
        _clear()
    devlog.log("auth", f"Выход из аккаунта {mail or ''}".strip())
    return state()


@router.get("/accounts")
def api_accounts():
    d = _acc_load()
    return {"current": SESS.get("email", ""), "accounts": sorted(d.keys())}


@router.post("/add")
def api_add_account():
    """«Добавить аккаунт»: текущий остаётся сохранённым, показывается экран входа."""
    with _L:
        _save()
        keep = {k: v for k, v in SESS.items() if k in ("email",)}
        SESS.clear()
        _save_empty()
    return state()


def _save_empty():
    try:
        SESSION_FILE.write_text("{}", encoding="utf-8")
    except OSError:
        pass


class SwitchReq(BaseModel):
    email: str = Field(min_length=5, max_length=120)


@router.post("/switch")
def api_switch(req: SwitchReq):
    mail = req.email.strip().lower()
    d = _acc_load()
    rec = d.get(mail)
    if not rec:
        raise HTTPException(404, "Этот аккаунт не сохранён. Войдите по почте.")
    with _L:
        if SESS.get("email") == mail and SESS.get("access"):
            return state()
        _save()  # текущий аккаунт запоминаем
        r = _sb("POST", "/auth/v1/token?grant_type=refresh_token", None, data=json.dumps({"refresh_token": rec["refresh"]}))
        if r.status_code != 200:
            d.pop(mail, None)
            _acc_write(d)
            raise HTTPException(400, "Сессия этого аккаунта истекла. Войдите по почте ещё раз.")
        SESS.clear()
        _store_session(r.json())
        try:
            _load_profile(force=True)
        except HTTPException:
            SESS.clear()
            _save_empty()
            raise
        _save()
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
    ("🔒 Приватность: никто не видит ваши фото и запросы",
     "Все ваши картинки, видео, чаты и запросы создаются и хранятся ТОЛЬКО на вашем компьютере (папки outputs и uploads, чаты в "
     "вашем браузере). Их не видят ни разработчик, ни владелец, ни администраторы, ни другие пользователи: в облако они не "
     "отправляются, и у программы нет ни одной функции, которая бы их собирала."),
    ("Что хранится в облаке", "Только ваша почта, ранг (например, пользователь или модератор), статус блокировки, время последнего "
                              "входа и записи о действиях администрации (кто кого заблокировал). Ваши картинки, чаты и промпты в облако "
                              "не попадают."),
    ("Когда данные всё же уходят в интернет", "Только когда вы сами используете интернет-функции: поиск в агенте Codwecode (поисковая "
                                              "фраза уходит поисковику) и скачивание моделей при установке. Тексты ваших картинок и чатов при "
                                              "этом никуда не отправляются."),
    ("Что такое ранги", "У каждого аккаунта есть ранг, как роль в Discord. Высший ранг не может быть заблокирован или понижен теми, "
                        "кто ниже. Администрация может блокировать отдельные модели и менять режим безопасности для всех: вы увидите это "
                        "в щите вверху программы."),
    ("Что запрещено", "Сексуальный контент с несовершеннолетними (блокируется всегда и не отключается ни одним рангом), всё, что нарушает "
                      "закон, и попытки обойти защиту. Откровенный 18+ контент и жестокий gore по умолчанию блокируются автоматически; "
                      "администрация может изменить этот режим, это отображается в щите вверху."),
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
    {"id": "banned", "q": "Что запрещено всегда и не отключается?",
     "opts": [("minor", "Сексуальный контент с несовершеннолетними"), ("cats", "Рисовать котов"), ("ru", "Писать по-русски")], "ok": "minor"},
    {"id": "who", "q": "Кто может увидеть ваши фото и запросы?",
     "opts": [("nobody", "Никто: они лежат только на вашем компьютере"), ("admin", "Администраторы"), ("all", "Все пользователи программы")], "ok": "nobody"},
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


# ───────── админ-панель (права и иерархию рангов проверяет база данных) ─────────
# Приложение только заранее отсекает лишнее и показывает понятные сообщения. Настоящая защита — в облаке
# (supabase/ranks.sql): даже если изменить эту программу, облако не даст забанить того, кто выше или равен по рангу.
def _need(perm):
    with _L:
        if not SESS.get("access"):
            raise HTTPException(401, "Войдите в аккаунт.")
        _refresh_if_needed()
        _load_profile(force=True)
        if perm not in my_perms():
            raise HTTPException(403, "Не хватает прав: " + PERMS.get(perm, perm) + ".")


def _need_owner():
    with _L:
        if not SESS.get("access"):
            raise HTTPException(401, "Войдите в аккаунт.")
        _refresh_if_needed()
        _load_profile(force=True)
        if (SESS.get("profile") or {}).get("role") != OWNER_KEY:
            raise HTTPException(403, "Это может делать только владелец.")


def _rpc(name, payload, default_err):
    r = _sb("POST", f"/rest/v1/rpc/{name}", SESS["access"], data=json.dumps(payload))
    if r.status_code not in (200, 204):
        msg = _err(r, default_err)[0]
        if "Could not find the function" in msg or r.status_code == 404:
            msg = "В облаке нет нужных функций. Запустите файл supabase/ranks.sql в Supabase (SQL Editor)."
        raise HTTPException(400, msg)
    return r


def _me():
    return SESS.get("email", "?")


class BanReq(BaseModel):
    id: str = Field(pattern=r"^[0-9a-fA-F\-]{36}$")
    banned: bool
    reason: str = Field(default="", max_length=200)


class IdReq(BaseModel):
    id: str = Field(pattern=r"^[0-9a-fA-F\-]{36}$")


class AccessPwReq(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class RoleReq(BaseModel):
    id: str = Field(pattern=r"^[0-9a-fA-F\-]{36}$")
    role: str = Field(pattern=r"^[a-z][a-z0-9_]{1,23}$")


class ModelBlockReq(BaseModel):
    model: str = Field(pattern=r"^[a-z]{3,8}:[A-Za-z0-9._:/\-]{1,100}$")
    blocked: bool


class SafetyAdminReq(BaseModel):
    enabled: bool
    confirm_adult: bool = False


class RankReq(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,23}$")
    name: str = Field(min_length=1, max_length=24)
    level: int = Field(ge=1, le=99)
    color: str = Field(default="#8d95aa", pattern=r"^#[0-9a-fA-F]{6}$")
    perms: list[str] = Field(default_factory=list, max_length=20)


class RankDelReq(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,23}$")


# — действия (их же вызывает DEV-консоль) —
def do_ban(target, flag, reason="", label=""):
    _need("users.ban")
    _rpc("admin_set_ban", {"target": target, "flag": flag, "reason": reason or None}, "Не удалось изменить блокировку")
    devlog.log("admin", f"{_me()}: {'блокировка' if flag else 'разблокировка'} пользователя {label or target[:8] + '…'}" + (f" ({reason})" if reason else ""))


def do_role(target, role, label=""):
    _need("users.rank")
    _rpc("admin_set_role", {"target": target, "new_role": role}, "Не удалось изменить ранг")
    devlog.log("admin", f"{_me()}: пользователю {label or target[:8] + '…'} выдан ранг «{role}»")


def do_model_block(model, blocked):
    _need("models.manage")
    _rpc("admin_set_model_block", {"model": model, "flag": blocked}, "Не удалось изменить блокировку модели")
    with _L:
        _load_profile(force=True)
    devlog.log("admin", f"{_me()}: модель {model} {'ЗАБЛОКИРОВАНА' if blocked else 'разблокирована'} для всех")


def do_safety(enabled, confirm_adult=False):
    _need("safety.toggle")
    if not enabled and not confirm_adult:
        raise HTTPException(400, "Чтобы выключить режим безопасности, подтвердите, что вам есть 18 лет и вы отвечаете за результат.")
    _rpc("admin_set_safety", {"flag": enabled}, "Не удалось изменить режим безопасности")
    with _L:
        _load_profile(force=True)
    devlog.log("admin", f"{_me()}: режим безопасности (18+/gore) {'ВКЛЮЧЁН' if enabled else 'ВЫКЛЮЧЕН'} для всех")


def do_rank_save(key, name, level, color, perms):
    _need_owner()
    bad = [x for x in perms if x not in PERMS]
    if bad:
        raise HTTPException(400, "Неизвестное право: " + ", ".join(bad))
    _rpc("owner_set_rank", {"k": key, "n": name, "lvl": level, "col": color, "prm": perms}, "Не удалось сохранить ранг")
    with _L:
        _load_profile(force=True)
    devlog.log("admin", f"{_me()}: ранг «{key}» сохранён (уровень {level}, прав: {len(perms)})")


def do_rank_delete(key):
    _need_owner()
    _rpc("owner_delete_rank", {"k": key}, "Не удалось удалить ранг")
    with _L:
        _load_profile(force=True)
    devlog.log("admin", f"{_me()}: ранг «{key}» удалён, его участники стали «Пользователь»")


def do_access_reset(target, label=""):
    _need("access.manage")
    _rpc("admin_reset_access", {"target": target}, "Не удалось сбросить")
    devlog.log("admin", f"{_me()}: сброс доступа пользователя {label or target[:8] + '…'}")


def list_users():
    _need("users.view")
    r = _sb("GET", "/rest/v1/profiles?select=*&order=created_at.desc&limit=500", SESS["access"])
    if r.status_code != 200:
        raise HTTPException(503, _err(r, "Не удалось получить список")[0])
    return r.json()


def find_user(email):
    _need("users.view")
    email = (email or "").strip().lower()
    if not EMAIL_RX.match(email):
        raise HTTPException(400, "Похоже, почта написана с ошибкой: " + email)
    r = _sb("GET", "/rest/v1/profiles?select=*&email=eq." + quote(email, safe=""), SESS["access"])
    if r.status_code != 200:
        raise HTTPException(503, _err(r, "Не удалось найти пользователя")[0])
    rows = r.json()
    if not rows:
        raise HTTPException(404, "Такого пользователя нет: " + email)
    return rows[0]


def get_audit(n=60):
    _need("users.view")
    r = _sb("POST", "/rest/v1/rpc/get_audit", SESS["access"], data=json.dumps({"n": int(n)}))
    if r.status_code != 200:
        msg = _err(r, "Не удалось получить журнал")[0]
        if "Could not find the function" in msg or r.status_code == 404:
            msg = "Журнал появится после запуска supabase/ranks.sql."
        raise HTTPException(400, msg)
    return r.json()


def ranks_now():
    with _L:
        return _ranks_list()


def refresh_all():
    with _L:
        if not SESS.get("access"):
            raise HTTPException(401, "Войдите в аккаунт.")
        _refresh_if_needed()
        _ranks_list(True)
        _load_profile(force=True)


# — маршруты —
@admin_router.post("/access-password")
def admin_access_password(req: AccessPwReq):
    _need("access.manage")
    _rpc("admin_set_access_password", {"pw": req.password}, "Не удалось задать пароль (запущен ли supabase/access_password.sql?)")
    devlog.log("admin", f"{_me()}: пароль доступа изменён")
    return {"ok": True}


@admin_router.post("/access-clear")
def admin_access_clear():
    _need("access.manage")
    _rpc("admin_clear_access_password", {}, "Не удалось снять пароль")
    devlog.log("admin", f"{_me()}: пароль доступа снят")
    return {"ok": True}


@admin_router.post("/access-reset")
def admin_access_reset(req: IdReq):
    do_access_reset(req.id)
    return {"ok": True}


@admin_router.get("/connect-code")
def admin_connect_code():
    _need("access.manage")
    return {"code": make_connect_code()}


def _overview():
    with _L:
        if not SESS.get("access"):
            raise HTTPException(401, "Войдите в аккаунт.")
        _refresh_if_needed()
        _load_profile(force=True)
        return {"me": SESS.get("uid"), "my_level": my_level(), "ranks": _ranks_list(), "perms": my_perms(),
                "perm_names": PERMS, "legacy": _ranks["legacy"],
                "is_owner": (SESS.get("profile") or {}).get("role") == OWNER_KEY, "policy": policy()}


@admin_router.get("/overview")
def admin_overview():
    return _overview()


@admin_router.get("/users")
def admin_users():
    users = list_users()
    return {"users": users, **_overview()}


@admin_router.post("/ban")
def admin_ban(req: BanReq):
    do_ban(req.id, req.banned, req.reason)
    return {"ok": True}


@admin_router.post("/role")
def admin_role(req: RoleReq):
    do_role(req.id, req.role)
    return {"ok": True}


@admin_router.post("/models")
def admin_models(req: ModelBlockReq):
    do_model_block(req.model, req.blocked)
    return {"ok": True, "policy": policy()}


@admin_router.post("/safety")
def admin_safety(req: SafetyAdminReq):
    do_safety(req.enabled, req.confirm_adult)
    return {"ok": True, "policy": policy()}


@admin_router.get("/audit")
def admin_audit():
    return {"log": get_audit(80)}


@admin_router.post("/rank-save")
def admin_rank_save(req: RankReq):
    do_rank_save(req.key, req.name, req.level, req.color, req.perms)
    return {"ok": True}


@admin_router.post("/rank-delete")
def admin_rank_delete(req: RankDelReq):
    do_rank_delete(req.key)
    return {"ok": True}
