"""DEV-консоль: командная строка внутри программы (отдельная вкладка) + живая лента того, что программа делает.

Каждая команда имеет понятное описание «что делает» и право, без которого она не выполнится. Права проверяет облако
(supabase/ranks.sql); здесь команды лишь удобная оболочка над теми же действиями, что и в админ-панели.
Тексты ваших запросов, промпты и картинки в консоль и в ленту не попадают."""
import re
import shlex
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import auth_api
import devlog

router = APIRouter(prefix="/api/dev")
CTX = {}   # server.py подставляет сюда catalog(), status(), jobs()


def bind(**kw):
    CTX.update(kw)


class CmdError(Exception):
    pass


class Out:
    def __init__(self):
        self.lines, self.failed = [], False

    def add(self, cls, text):
        self.lines.append({"c": cls, "t": str(text)})

    def ok(self, t):
        self.add("ok", t)

    def err(self, t):
        self.failed = True
        self.add("err", t)

    def dim(self, t):
        self.add("dim", t)

    def head(self, t):
        self.add("head", t)

    def text(self, t):
        self.add("txt", t)

    def table(self, header, rows):
        rows = [[str(c) for c in r] for r in rows]
        w = [max(len(str(h)), *(len(r[i]) for r in rows)) if rows else len(str(h)) for i, h in enumerate(header)]
        self.add("th", "  ".join(str(h).ljust(w[i]) for i, h in enumerate(header)))
        for r in rows:
            self.add("row", "  ".join(c.ljust(w[i]) for i, c in enumerate(r)))


def _need_args(args, n, usage):
    if len(args) < n:
        raise CmdError("Не хватает данных. Пример: " + usage)


def _uid(email):
    return auth_api.find_user(email)["id"]


# ───────── команды ─────────
def c_help(a, o):
    if a:
        c = COMMANDS.get(a[0].lower())
        if not c:
            raise CmdError("Нет такой команды. Напишите help, чтобы увидеть список.")
        o.head(c["usage"])
        o.text(c["desc"])
        o.dim("Нужно право: " + (auth_api.PERMS[c["perm"]] if c["perm"] else "любое (кто может открыть консоль)"))
        return
    o.head("Команды (после команды можно нажать Tab для подсказки, ↑ ↓ — прошлые команды)")
    for name, c in COMMANDS.items():
        mark = "  " if (not c["perm"] or auth_api.has_perm(c["perm"])) else "🔒"
        o.add("row", f"{mark} {c['usage']}")
        o.dim("      " + c["desc"])


def c_whoami(a, o):
    s = auth_api.state()
    o.head(f"{s['email']}  ·  ранг «{s['rank_name']}» (уровень {s['level']})")
    for p in s["perms"]:
        o.ok("✓ " + auth_api.PERMS[p])
    if not s["perms"]:
        o.dim("Особых прав нет.")
    if s.get("legacy"):
        o.err("Облако ещё не обновлено: запустите supabase/ranks.sql, чтобы заработали ранги и новые права.")


def c_status(a, o):
    st = CTX["status"]()
    pol = auth_api.policy()
    o.head("Состояние программы")
    o.text(f"ИИ: {'включён' if st['on'] else 'выключен'}   Ollama: {'работает' if st['ollama'] else 'не запущен'}   "
           f"Видеокарта занята: {'да' if st['gpu_busy'] else 'нет'}")
    if st.get("gpu"):
        g = st["gpu"]
        o.text(f"Видеокарта: {g['name']}, память {g['used']} из {g['total']} МБ")
    o.text(f"Загружены модели: {', '.join(st['loaded']) or 'нет'}")
    o.text(f"Задач в работе: {len(st['jobs'])}")
    o.text(f"Режим безопасности (18+/gore): {'ВКЛЮЧЁН' if pol['safety_soft'] else 'ВЫКЛЮЧЕН'}   "
           f"Заблокировано моделей: {len(pol['blocked_models'])}")
    ok, why = auth_api.integrity()
    o.text("Файлы программы: " + ("целы" if ok else "ИЗМЕНЕНЫ — " + why))


def c_users(a, o):
    q = (a[0].lower() if a else "")
    users = [u for u in auth_api.list_users() if q in u["email"].lower() or q in (u.get("role") or "")]
    ranks = {r["key"]: r for r in auth_api.ranks_now()}
    o.table(["почта", "ранг", "статус", "был(а)"], [
        (u["email"], ranks.get(u.get("role"), {}).get("name", u.get("role")),
         "БАН" if u.get("banned") else ("ок" if u.get("tos_version") else "не принял условия"),
         (u.get("last_seen") or "")[:16].replace("T", " ")) for u in users[:40]])
    o.dim(f"Показано {min(len(users), 40)} из {len(users)}.")


def c_ranks(a, o):
    rs = auth_api.ranks_now()
    o.table(["уровень", "ключ", "название", "права"], [
        (r["level"], r["key"], r["name"], "ВСЕ" if r["key"] == auth_api.OWNER_KEY else (", ".join(r.get("perms") or []) or "—")) for r in rs])
    o.dim("Действовать можно только на тех, кто ниже вас рангом. Владелец (100) неприкосновенен.")


def c_perms(a, o):
    for k, v in auth_api.PERMS.items():
        o.add("row", f"{k.ljust(15)} {v}")


def c_ban(a, o):
    _need_args(a, 1, "ban почта [причина]")
    auth_api.do_ban(_uid(a[0]), True, " ".join(a[1:])[:200] or "нарушение правил", a[0])
    o.ok(f"Заблокирован: {a[0]}. Выйдет из программы не позже чем через ~1,5 минуты.")


def c_unban(a, o):
    _need_args(a, 1, "unban почта")
    auth_api.do_ban(_uid(a[0]), False, "", a[0])
    o.ok(f"Разблокирован: {a[0]}")


def c_rank(a, o):
    _need_args(a, 2, "rank почта ранг   (список рангов: ranks)")
    auth_api.do_role(_uid(a[0]), a[1].lower(), a[0])
    o.ok(f"{a[0]} теперь «{a[1].lower()}».")


def c_access_reset(a, o):
    _need_args(a, 1, "access-reset почта")
    auth_api.do_access_reset(_uid(a[0]), a[0])
    o.ok("Доступ сброшен: пользователю снова нужно ввести пароль доступа.")


def c_rank_new(a, o):
    _need_args(a, 3, "rank-new ключ уровень название [#цвет]   например: rank-new vip 30 VIP #a855f7")
    key, name = a[0].lower(), a[2]
    if any(r["key"] == key for r in auth_api.ranks_now()):
        raise CmdError("Ранг с таким ключом уже есть. Чтобы изменить права: rank-perm.")
    if not a[1].isdigit():
        raise CmdError("Уровень — число от 1 до 99.")
    color = a[3] if len(a) > 3 else "#8d95aa"
    auth_api.do_rank_save(key, name, int(a[1]), color, [])
    o.ok(f"Ранг «{name}» создан. Права добавьте так: rank-perm {key} +users.view")


def c_rank_perm(a, o):
    _need_args(a, 2, "rank-perm ранг +право -право   (список прав: perms)")
    r = next((x for x in auth_api.ranks_now() if x["key"] == a[0].lower()), None)
    if not r:
        raise CmdError("Такого ранга нет.")
    if r["key"] == auth_api.OWNER_KEY:
        raise CmdError("Права владельца менять нельзя.")
    cur = list(r.get("perms") or [])
    for t in a[1:]:
        if t[:1] not in "+-" or t[1:] not in auth_api.PERMS:
            raise CmdError(f"Не понял «{t}». Пишите +право или -право из списка perms.")
        cur = [p for p in cur if p != t[1:]] + ([t[1:]] if t[0] == "+" else [])
    auth_api.do_rank_save(r["key"], r["name"], int(r["level"]), r.get("color", "#8d95aa"), cur)
    o.ok(f"Права ранга «{r['name']}»: {', '.join(cur) or 'нет'}")


def c_rank_del(a, o):
    _need_args(a, 1, "rank-del ранг")
    auth_api.do_rank_delete(a[0].lower())
    o.ok("Ранг удалён, его участники стали «Пользователь».")


def c_models(a, o):
    items = CTX["catalog"]()
    o.table(["номер модели", "название", "состояние"], [
        (m["id"], m["title"], "🔒 заблокирована" if m["blocked"] else ("готова" if m["ready"] else "не скачана")) for m in items])
    o.dim("Заблокировать: block <номер>   Разблокировать: unblock <номер>")


def _model_arg(a, usage):
    _need_args(a, 1, usage)
    ids = {m["id"] for m in CTX["catalog"]()}
    if a[0] not in ids:
        raise CmdError("Нет такой модели. Номера смотрите командой models.")
    return a[0]


def c_block(a, o):
    mid = _model_arg(a, "block <номер модели>")
    auth_api.do_model_block(mid, True)
    o.ok(f"{mid} заблокирована у всех (в течение ~1,5 минуты).")


def c_unblock(a, o):
    mid = _model_arg(a, "unblock <номер модели>")
    auth_api.do_model_block(mid, False)
    o.ok(f"{mid} снова доступна.")


def c_safety(a, o):
    pol = auth_api.policy()
    if not a:
        o.head("Режим безопасности (18+ и gore): " + ("ВКЛЮЧЁН" if pol["safety_soft"] else "ВЫКЛЮЧЕН"))
        o.dim("Сексуальный контент с несовершеннолетними блокируется ВСЕГДА, этот переключатель его не затрагивает.")
        o.dim("Изменить: safety on   или   safety off 18+")
        return
    if a[0] == "on":
        auth_api.do_safety(True)
        o.ok("Режим безопасности включён у всех.")
    elif a[0] == "off":
        if len(a) < 2 or a[1] != "18+":
            raise CmdError("Чтобы выключить, подтвердите возраст и ответственность: safety off 18+")
        auth_api.do_safety(False, True)
        o.ok("Режим безопасности ВЫКЛЮЧЕН у всех. Правило про несовершеннолетних остаётся включённым.")
    else:
        raise CmdError("Пишите: safety on  /  safety off 18+  /  safety")


def c_policy(a, o):
    p = auth_api.policy()
    o.head("Общие настройки из облака (действуют у всех)")
    o.text("Режим безопасности: " + ("включён" if p["safety_soft"] else "ВЫКЛЮЧЕН"))
    o.text("Заблокированные модели: " + (", ".join(p["blocked_models"]) or "нет"))


def c_audit(a, o):
    n = int(a[0]) if a and a[0].isdigit() else 20
    rows = auth_api.get_audit(min(n, 100))
    o.table(["когда", "кто", "действие", "над кем", "подробности"], [
        ((r.get("at") or "")[5:16].replace("T", " "), r.get("actor_email") or "?", r.get("action"), r.get("target") or "", r.get("detail") or "")
        for r in rows])


def c_connect(a, o):
    o.text(auth_api.make_connect_code())
    o.dim("Это код подключения для новых пользователей. Отправляйте его вместе с паролем доступа.")


def c_refresh(a, o):
    auth_api.refresh_all()
    o.ok("Профиль, ранги и настройки заново загружены из облака.")


def c_jobs(a, o):
    js = CTX["jobs"]()
    if not js:
        o.dim("Сейчас нет задач.")
    o.table(["номер", "тип", "статус", "этап"], [(j["id"], j["kind"], j["status"], (j.get("stage") or "")[:50]) for j in js])
    o.dim("Тексты запросов здесь не показываются.")


def c_integrity(a, o):
    ok, why = auth_api.integrity()
    (o.ok if ok else o.err)("Файлы программы целы." if ok else why)


COMMANDS = {}


def _reg(name, fn, usage, desc, perm=None):
    COMMANDS[name] = {"name": name, "fn": fn, "usage": usage, "desc": desc, "perm": perm}


_reg("help", c_help, "help [команда]", "Показывает список команд или подробности об одной команде.")
_reg("whoami", c_whoami, "whoami", "Показывает, кто вы, ваш ранг и что вам разрешено.")
_reg("status", c_status, "status", "Сводка: включён ли ИИ, видеокарта, загруженные модели, задачи, режим безопасности, целостность файлов.")
_reg("users", c_users, "users [поиск]", "Список пользователей из облака: ранг, блокировка, когда заходили.", "users.view")
_reg("ranks", c_ranks, "ranks", "Все ранги: уровень, ключ, название и права.")
_reg("perms", c_perms, "perms", "Список всех прав, которые можно выдавать рангам.")
_reg("ban", c_ban, "ban почта [причина]", "Блокирует пользователя (вход закрыт). Только тех, кто ниже вас рангом; владельца забанить нельзя.", "users.ban")
_reg("unban", c_unban, "unban почта", "Снимает блокировку с пользователя.", "users.ban")
_reg("rank", c_rank, "rank почта ранг", "Выдаёт пользователю ранг. Только ниже вашего, и только тому, кто ниже вас.", "users.rank")
_reg("access-reset", c_access_reset, "access-reset почта", "Сбрасывает пользователю доступ: он снова вводит пароль доступа.", "access.manage")
_reg("rank-new", c_rank_new, "rank-new ключ уровень название [#цвет]", "Создаёт новый ранг без прав (только владелец).", "users.rank")
_reg("rank-perm", c_rank_perm, "rank-perm ранг +право -право", "Добавляет или убирает права у ранга (только владелец).", "users.rank")
_reg("rank-del", c_rank_del, "rank-del ранг", "Удаляет ранг; его участники становятся «Пользователь» (только владелец).", "users.rank")
_reg("models", c_models, "models", "Список моделей с номерами и состоянием: готова, не скачана или заблокирована.")
_reg("block", c_block, "block <номер модели>", "Блокирует модель у ВСЕХ пользователей. Номер смотрите в models.", "models.manage")
_reg("unblock", c_unblock, "unblock <номер модели>", "Снова разрешает модель.", "models.manage")
_reg("safety", c_safety, "safety [on | off 18+]", "Показывает или меняет режим безопасности (18+/gore) у всех. Правило про несовершеннолетних не отключается.", "safety.toggle")
_reg("policy", c_policy, "policy", "Показывает общие настройки из облака: режим безопасности и заблокированные модели.")
_reg("audit", c_audit, "audit [сколько]", "Журнал действий администрации: кто, что и кому сделал.", "users.view")
_reg("connect-code", c_connect, "connect-code", "Показывает код подключения для новых пользователей.", "access.manage")
_reg("refresh", c_refresh, "refresh", "Сразу перезагружает ваш профиль, ранги и общие настройки из облака.")
_reg("jobs", c_jobs, "jobs", "Задачи в очереди и в работе (без текстов запросов).")
_reg("integrity", c_integrity, "integrity", "Проверяет, не изменены ли файлы программы.")
_reg("clear", None, "clear", "Очищает экран консоли (лента справа не очищается).")

_ALIASES = {"?": "help", "me": "whoami", "ls": "users", "models-list": "models"}


# ───────── маршруты ─────────
def _need_console():
    if "dev.console" not in auth_api.my_perms():
        raise HTTPException(403, "Вкладка DEV доступна только рангам с правом «Открывать вкладку DEV-консоль».")


class ExecReq(BaseModel):
    cmd: str = Field(min_length=1, max_length=400)


@router.get("/commands")
def api_commands():
    _need_console()
    return {"commands": [{"name": n, "usage": c["usage"], "desc": c["desc"], "perm": c["perm"],
                          "allowed": (not c["perm"]) or auth_api.has_perm(c["perm"])} for n, c in COMMANDS.items()]}


@router.get("/feed")
def api_feed(since: int = 0):
    _need_console()
    items, last = devlog.since(max(0, since))
    return {"events": items, "last": last, "now": time.time()}


@router.post("/exec")
def api_exec(req: ExecReq):
    auth_api._need("dev.console")
    try:
        tokens = shlex.split(req.cmd.strip())
    except ValueError:
        return {"explain": "", "lines": [{"c": "err", "t": "Кавычки не закрыты."}], "ok": False}
    if not tokens:
        return {"explain": "", "lines": [], "ok": True}
    name = _ALIASES.get(tokens[0].lower(), tokens[0].lower())
    spec = COMMANDS.get(name)
    o = Out()
    if not spec:
        near = [n for n in COMMANDS if n.startswith(name[:2])][:4]
        o.err(f"Не знаю команду «{tokens[0]}»." + (" Может быть: " + ", ".join(near) if near else " Напишите help."))
        return {"explain": "", "lines": o.lines, "ok": False}
    if spec["perm"] and not auth_api.has_perm(spec["perm"]):
        o.err("Не хватает прав: " + auth_api.PERMS[spec["perm"]] + ".")
        devlog.log("warn", f"{auth_api.state().get('email', '?')}: попытка выполнить «{name}» без права {spec['perm']}")
        return {"explain": spec["desc"], "lines": o.lines, "ok": False}
    shown = name if name not in ("ban", "unban", "rank", "access-reset") else name + " " + (tokens[1] if len(tokens) > 1 else "")
    devlog.log("cmd", f"▶ {shown.strip()} — {spec['desc']}")
    if spec["fn"] is None:  # clear
        return {"explain": spec["desc"], "lines": [], "ok": True, "clear": True}
    try:
        spec["fn"](tokens[1:], o)
    except CmdError as e:
        o.err(str(e))
        o.dim("Подсказка: " + spec["usage"])
    except HTTPException as e:
        o.err(str(e.detail))
    except Exception as e:  # noqa
        o.err("Внутренняя ошибка: " + re.sub(r"\s+", " ", str(e))[:200])
    if o.failed:
        devlog.log("warn", f"команда «{name}» не выполнена")
    return {"explain": spec["desc"], "lines": o.lines, "ok": not o.failed}
