"""Codwecode — инструменты агента: запустить страницу, сфотографировать экран, посмотреть картинку моделью,
найти в интернете и прочитать страницу.

Безопасность:
  * Страницы из рабочей папки открываются только по адресу сервера /wsfiles (там действует CSP-песочница).
  * Браузер снимков (Edge headless) не может обращаться к локальной сети и к самому приложению (кроме /wsfiles):
    любой запрос на закрытый адрес отменяется. То же правило действует при чтении сайтов (защита от SSRF).
  * Текст из интернета — это данные: клиент оборачивает его так, чтобы модель не принимала его за команды.
  * Поиск и чтение сайтов отправляют запросы наружу; клиент включает их только если пользователь включил «Интернет».
"""
import base64
import html
import io
import ipaddress
import os
import re
import socket
import threading
import time
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import safety
import workspace_api

router = APIRouter(prefix="/api/cc")
ROOT = workspace_api.ROOT
SHOTS = ROOT / ".shots"
SHOTS.mkdir(exist_ok=True)
BASE = "http://127.0.0.1:" + (os.environ.get("CODWE_PORT") or "38765")
UA = {"User-Agent": "CodweAI/1.0 (personal local assistant; https://localhost) python-requests", "Accept-Language": "ru,en;q=0.8"}
_deps = {}
_shot_lock = threading.Lock()


def bind(**kw):
    """server.py передаёт сюда ссылки на свои функции (без циклического импорта)."""
    _deps.update(kw)


# ───────── защита от обращений во внутреннюю сеть ─────────
_host_cache = {}


def public_host(host):
    host = (host or "").strip("[]").lower()
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local") \
            or host.endswith(".internal"):
        return False
    if host in _host_cache and time.time() - _host_cache[host][1] < 300:
        return _host_cache[host][0]
    ok = False
    try:
        infos = socket.getaddrinfo(host, None)
        ok = bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)
    except Exception:
        ok = False
    _host_cache[host] = (ok, time.time())
    return ok


def check_url(url):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise HTTPException(400, "Нужна ссылка http:// или https://")
    if u.username or u.password:
        raise HTTPException(400, "Ссылки с логином и паролем не поддерживаются.")
    if not public_host(u.hostname):
        raise HTTPException(400, "Адреса внутри компьютера и локальной сети закрыты.")
    return url


def _allowed_request(url):
    if url.startswith("data:") or url.startswith("blob:") or url.startswith("about:"):
        return True
    if url.startswith(BASE + "/wsfiles/"):
        return True
    u = urllib.parse.urlparse(url)
    return u.scheme in ("http", "https") and public_host(u.hostname)


# ───────── снимки экрана ─────────
class Act(BaseModel):
    t: str = Field(pattern="^(wait|key|click|shot|scroll|type|btn)$")
    ms: int | None = Field(default=None, ge=0, le=5000)
    k: str | None = Field(default=None, max_length=120)
    x: int | None = Field(default=None, ge=0, le=4000)
    y: int | None = Field(default=None, ge=0, le=4000)
    text: str | None = Field(default=None, max_length=60)


class ShotReq(BaseModel):
    target: str = Field(min_length=1, max_length=600)   # путь в рабочей папке или http(s)-ссылка
    actions: list[Act] = Field(default_factory=list, max_length=40)
    auto: bool = True                                    # стандартный сценарий «поиграть»
    w: int = Field(default=1100, ge=320, le=1920)
    h: int = Field(default=700, ge=240, le=1200)


def auto_script(w, h):
    """Сценарий «поиграть»: нажать кнопку старта (найденную по тексту), потом по очереди другие видимые кнопки,
    клавиши управления и мышь; после каждого шага — снимок."""
    cx, cy = w // 2, h // 2
    A = lambda **k: Act(**k)  # noqa: E731
    return [A(t="wait", ms=900), A(t="shot"),
            A(t="btn", k="start"), A(t="wait", ms=900), A(t="shot"),
            A(t="key", k="Enter"), A(t="key", k="Space"), A(t="key", k="ArrowRight"), A(t="key", k="d"),
            A(t="key", k="ArrowUp"), A(t="key", k="w"), A(t="click", x=cx, y=cy), A(t="key", k="e"),
            A(t="wait", ms=700), A(t="btn", k="any0"), A(t="wait", ms=600), A(t="shot"),
            A(t="btn", k="re:убить|убей|kill|attack|shoot|fire|выстрел|атак|удар"), A(t="wait", ms=500), A(t="shot"),
            A(t="btn", k="re:впустить|пустить|allow|accept|принять|да|yes|ok"), A(t="wait", ms=700),
            A(t="btn", k="any1"), A(t="key", k="ArrowLeft"), A(t="key", k="a"), A(t="key", k="Space"),
            A(t="click", x=int(w * .3), y=int(h * .55)), A(t="wait", ms=1000), A(t="btn", k="any2"),
            A(t="wait", ms=800), A(t="shot")]


# какие кнопки видны на странице; start = похожие на «играть/начать/старт»
FIND_BTN = """(mode) => {
  const vis = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 6 && r.height > 6 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'
      && r.bottom > 0 && r.right > 0 && r.left < innerWidth && r.top < innerHeight; };
  const els = [...document.querySelectorAll('button,[role=button],input[type=button],input[type=submit],a[href],[onclick],.btn,.button')].filter(vis);
  const txt = (e) => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim();
  let pick = null;
  if (mode === 'start') pick = els.find((e) => /играть|начать|старт|start|play|begin|смен|new game|запуск|run|далее|next/i.test(txt(e)));
  else if (mode.startsWith('re:')) { const rx = new RegExp(mode.slice(3), 'i'); pick = els.find((e) => rx.test(txt(e))); }
  else pick = els[parseInt(mode.slice(3), 10)];
  if (!pick) return null;
  const r = pick.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, t: txt(pick).slice(0, 40) };
}"""


KEYS = {"space": "Space", "enter": "Enter", "esc": "Escape", "escape": "Escape", "up": "ArrowUp", "down": "ArrowDown",
        "left": "ArrowLeft", "right": "ArrowRight", "tab": "Tab"}


def _clean_old():
    files = sorted(SHOTS.glob("*.png"), key=lambda p: p.stat().st_mtime)
    for p in files[:-80]:
        try:
            p.unlink()
        except OSError:
            pass


def take_shots(target, actions, auto, w, h):
    from playwright.sync_api import sync_playwright
    if re.match(r"^https?://", target, re.I):
        check_url(target)
        url, name = target, "web"
    else:
        p = workspace_api.safe_path(target, must_exist=True)
        if p.is_dir():
            p = p / "index.html"
            if not p.exists():
                raise HTTPException(404, "В папке нет index.html")
        if p.suffix.lower() not in (".html", ".htm", ".svg"):
            raise HTTPException(400, "Открыть можно только .html / .svg")
        url = BASE + "/wsfiles/" + "/".join(urllib.parse.quote(x) for x in workspace_api.rel_of(p).split("/"))
        name = p.stem
    script = actions or (auto_script(w, h) if auto else [Act(t="wait", ms=900), Act(t="shot")])
    logs, shots, acts = [], [], []
    stamp = time.strftime("%H%M%S")
    t0 = time.time()
    with _shot_lock, sync_playwright() as pw:
        br = pw.chromium.launch(channel="msedge", headless=True,
                                args=["--disable-gpu", "--mute-audio", "--autoplay-policy=no-user-gesture-required"])
        try:
            ctx = br.new_context(viewport={"width": w, "height": h}, accept_downloads=False, service_workers="block")
            ctx.route("**/*", lambda route: route.continue_() if _allowed_request(route.request.url) else route.abort())
            pg = ctx.new_page()
            pg.set_default_timeout(8000)
            pg.on("console", lambda m: logs.append(f"[{m.type}] {m.text}"[:300]) if m.type in ("error", "warning") else None)
            def on_pageerror(e):
                st = getattr(e, "stack", "") or ""
                m = re.search(r":(\d+):(\d+)", st)
                logs.append(f"[ОШИБКА СКРИПТА] {str(e)[:300]}" + (f" (строка {m.group(1)}, столбец {m.group(2)})" if m else ""))
            pg.on("pageerror", on_pageerror)
            pg.on("requestfailed", lambda r: logs.append(f"[не загрузилось] {r.url[:120]}")
                  if not r.url.startswith(("data:", "blob:")) else None)
            pg.on("dialog", lambda d: d.dismiss())
            try:
                pg.goto(url, wait_until="load", timeout=12000)
            except Exception as e:
                logs.append(f"[страница не открылась] {str(e)[:200]}")
            for a in script:
                if time.time() - t0 > 40:
                    logs.append("[время проверки вышло]")
                    break
                try:
                    if a.t == "wait":
                        pg.wait_for_timeout(a.ms or 500)
                    elif a.t == "key":
                        pg.keyboard.press(KEYS.get((a.k or "").lower(), a.k or "Space"))
                    elif a.t == "type":
                        pg.keyboard.type(a.text or "", delay=20)
                    elif a.t == "click":
                        pg.mouse.click(a.x or w // 2, a.y or h // 2)
                    elif a.t == "btn":
                        b = pg.evaluate(FIND_BTN, a.k or "start")
                        if b:
                            pg.mouse.click(b["x"], b["y"])
                            acts.append(f"нажата кнопка «{b['t'] or '?'}»")
                        elif (a.k or "start") == "start":
                            acts.append("кнопка старта не найдена")
                    elif a.t == "scroll":
                        pg.mouse.wheel(0, a.y or 500)
                    elif a.t == "shot":
                        fn = f"{name}_{stamp}_{len(shots) + 1}.png"
                        pg.screenshot(path=str(SHOTS / fn))
                        shots.append(fn)
                except Exception as e:
                    logs.append(f"[действие {a.t} не выполнилось] {str(e)[:150]}")
            if not shots:
                fn = f"{name}_{stamp}_1.png"
                pg.screenshot(path=str(SHOTS / fn))
                shots.append(fn)
            title = ""
            try:
                title = pg.title()
                text = pg.evaluate("document.body ? document.body.innerText.slice(0, 600) : ''")
            except Exception:
                text = ""
        finally:
            br.close()
    _clean_old()
    seen, uniq = set(), []
    for l in logs:  # одинаковые сообщения не повторяем
        if l not in seen:
            seen.add(l)
            uniq.append(l)
    return {"shots": shots, "urls": ["/wsfiles/.shots/" + s for s in shots], "logs": uniq[:25], "title": title,
            "text": text, "acts": acts}


@router.post("/shot")
def cc_shot(req: ShotReq):
    if req.target.startswith(("http://", "https://")):
        okc, _c, msg = safety.check_text(req.target)
        if not okc:
            raise HTTPException(403, msg)
    try:
        return take_shots(req.target, req.actions, req.auto, req.w, req.h)
    except HTTPException:
        raise
    except Exception as e:  # noqa
        raise HTTPException(500, f"Не удалось сфотографировать страницу: {str(e)[:300]}")


# ───────── «посмотреть» картинку моделью со зрением ─────────
class LookReq(BaseModel):
    shots: list[str] = Field(min_length=1, max_length=6)
    question: str = Field(default="", max_length=3000)
    logs: str = Field(default="", max_length=3000)


def _jpeg_b64(path):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((800, 800))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


LOOK_SYS = ("Ты — строгий тестировщик (QA) веб-страниц и игр. Тебе показывают снимки экрана по порядку (сразу после "
            "запуска, затем после нажатий кнопок и клавиш) и сообщения консоли браузера.\n"
            "Ответ строго такой:\n"
            "Наблюдения: 3–7 коротких пунктов — что на каждом снимке (видны ли кнопки, персонажи, текст интерфейса, "
            "рисунок; или это просто текст кода / пустой или чёрный экран) и что изменилось между снимками.\n"
            "Проблемы: список того, что сломано или не соответствует задаче (или «нет»).\n"
            "Последняя строка ровно: ВЕРДИКТ: OK  или  ВЕРДИКТ: BUG.\n"
            "OK — только если это настоящая игра/страница с интерфейсом, она соответствует задаче, снимки меняются после "
            "действий (реагирует на управление) и в консоли нет ошибок. Если на экране виден исходный код, пустота, "
            "ошибка или ничего не меняется — BUG. Не придумывай того, чего не видно. Пиши по-русски.")


@router.post("/look")
def cc_look(req: LookReq):
    S = _deps["S"]
    if not S.on:
        raise HTTPException(409, "ИИ выключен.")
    if S.gpu_busy:
        raise HTTPException(423, "Видеокарта занята генерацией картинки/видео.")
    if not _deps["ensure_ollama"]():
        raise HTTPException(503, "Ollama не запускается.")
    model = _deps["pick_vision_model"]()
    if not model:
        raise HTTPException(409, "Нет модели со зрением (нужна qwen3.5 или qwen2.5vl).")
    imgs = []
    for s in req.shots:
        if not re.fullmatch(r"[\w.\-]{1,120}\.png", s):
            raise HTTPException(400, "Плохое имя снимка.")
        p = SHOTS / s
        if not p.is_file():
            raise HTTPException(404, "Снимок не найден.")
        imgs.append(_jpeg_b64(p))
    q = (req.question.strip() or "Работает ли страница как задумано?") + \
        ("\n\nКонсоль браузера:\n" + req.logs.strip() if req.logs.strip() else "\n\nКонсоль браузера: ошибок нет.")
    r = requests.post(_deps["OLLAMA"] + "/api/chat", timeout=300, json={
        "model": model, "stream": False, "keep_alive": _deps["KEEP"], **_deps["no_think"](model),
        "options": {"num_ctx": 8192, "temperature": 0.2},
        "messages": [{"role": "system", "content": LOOK_SYS}, {"role": "user", "content": q, "images": imgs}]})
    r.raise_for_status()
    text = (r.json().get("message") or {}).get("content", "").strip()
    found = re.findall(r"ВЕРДИКТ\s*:\s*\**\s*(OK|BUG)", text, re.I)
    verdict = "OK" if found and found[-1].upper() == "OK" else "BUG"
    if re.search(r"\[(error|ОШИБКА СКРИПТА|страница не открылась)", req.logs):
        verdict = "BUG"
    return {"verdict": verdict, "text": text, "model": model}


# ───────── поиск в интернете ─────────
class SearchReq(BaseModel):
    q: str = Field(min_length=2, max_length=300)


def _strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _bing(q):
    r = requests.get("https://www.bing.com/search", params={"q": q, "setlang": "ru"}, headers=UA, timeout=15)
    out = []
    for blk in r.text.split('<li class="b_algo"')[1:]:
        blk = blk.split("</li>")[0]
        m = re.search(r"<h2[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", blk, re.S)
        if not m:
            continue
        href = html.unescape(m.group(1))
        mm = re.search(r"[?&]u=a1([^&]+)", href)
        if "bing.com/ck/a" in href and mm:
            try:
                href = base64.urlsafe_b64decode(mm.group(1) + "=" * (-len(mm.group(1)) % 4)).decode()
            except Exception:
                pass
        sn = re.search(r"<p[^>]*>(.*?)</p>", blk, re.S)
        out.append({"title": _strip_tags(m.group(2)), "url": href, "snippet": _strip_tags(sn.group(1)) if sn else ""})
    return out


CONF = ROOT.parent / "codwecode_config.json"


def conf():
    import json
    try:
        return json.loads(CONF.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _brave(q, key):
    r = requests.get("https://api.search.brave.com/res/v1/web/search", params={"q": q, "count": 8}, timeout=15,
                     headers={"Accept": "application/json", "X-Subscription-Token": key})
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": _strip_tags(x.get("description", ""))}
            for x in r.json().get("web", {}).get("results", [])]


def _searx(q, base):
    r = requests.get(base.rstrip("/") + "/search", params={"q": q, "format": "json"}, headers=UA, timeout=15)
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
            for x in r.json().get("results", [])[:8]]


def _wiki(q):
    lang = "ru" if re.search(r"[а-яё]", q, re.I) else "en"
    r = requests.get(f"https://{lang}.wikipedia.org/w/api.php", headers=UA, timeout=12, params={
        "action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": 4, "utf8": 1})
    return [{"title": "Википедия: " + x["title"], "url": f"https://{lang}.wikipedia.org/wiki/" +
             urllib.parse.quote(x["title"].replace(" ", "_")), "snippet": _strip_tags(x.get("snippet", ""))}
            for x in r.json().get("query", {}).get("search", [])]


def _mdn(q):
    r = requests.get("https://developer.mozilla.org/api/v1/search", params={"q": q, "locale": "en-US"}, headers=UA, timeout=12)
    return [{"title": "MDN: " + x["title"], "url": "https://developer.mozilla.org" + x["mdn_url"],
             "snippet": x.get("summary", "")} for x in r.json().get("documents", [])[:4]]


def _stack(q):
    r = requests.get("https://api.stackexchange.com/2.3/search/advanced", headers=UA, timeout=12, params={
        "order": "desc", "sort": "relevance", "q": q, "site": "stackoverflow", "pagesize": 4})
    return [{"title": "StackOverflow: " + html.unescape(x["title"]), "url": x["link"],
             "snippet": f"ответов: {x.get('answer_count', 0)}, оценка: {x.get('score', 0)}"} for x in r.json().get("items", [])]


@router.post("/search")
def cc_search(req: SearchReq):
    okc, _c, msg = safety.check_text(req.q)
    if not okc:
        raise HTTPException(403, msg)
    c = conf()
    jobs = []
    if c.get("brave_key"):
        jobs.append(lambda: _brave(req.q, c["brave_key"]))
    if c.get("searxng"):
        jobs.append(lambda: _searx(req.q, c["searxng"]))
    jobs += [lambda: _bing(req.q), lambda: _mdn(req.q), lambda: _stack(req.q), lambda: _wiki(req.q)]
    results = [None] * len(jobs)

    def run(i):
        try:
            results[i] = [x for x in jobs[i]() if str(x.get("url", "")).startswith("http")]
        except Exception:  # noqa
            results[i] = []
    ths = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(len(jobs))]
    for t in ths:
        t.start()
    for t in ths:
        t.join(20)
    merged, seen = [], set()
    # по очереди берём из каждого источника, чтобы выдача была разнообразной
    for rank in range(8):
        for r in results:
            if r and rank < len(r) and r[rank]["url"] not in seen and len(merged) < 10:
                seen.add(r[rank]["url"])
                merged.append(r[rank])
    if not merged:
        raise HTTPException(502, "Поиск не дал результатов (нет интернета?).")
    return {"q": req.q, "results": merged}


class ConfReq(BaseModel):
    brave_key: str = Field(default="", max_length=200)
    searxng: str = Field(default="", max_length=300)


@router.get("/config")
def cc_conf_get():
    c = conf()
    return {"brave": bool(c.get("brave_key")), "searxng": c.get("searxng", "")}


@router.post("/config")
def cc_conf_set(req: ConfReq):
    import json
    if req.searxng and not re.match(r"^https?://", req.searxng):
        raise HTTPException(400, "Адрес SearXNG должен начинаться с http:// или https://")
    c = conf()
    if req.brave_key.strip():
        c["brave_key"] = req.brave_key.strip()
    elif req.brave_key == "-":
        c.pop("brave_key", None)
    c["searxng"] = req.searxng.strip()
    CONF.write_text(json.dumps(c), encoding="utf-8")
    return cc_conf_get()


# ───────── чтение страницы ─────────
class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "iframe", "template"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "pre", "section", "article", "blockquote", "td"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buf, self.skip, self.title, self._in_title, self.links = [], 0, "", False, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.buf.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.buf.append("\n## ")
        if tag == "a" and len(self.links) < 40:
            h = dict(attrs).get("href")
            if h and h.startswith("http"):
                self.links.append(h)

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.buf.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.buf.append(data)


def fetch_page(url):
    cur = url
    for _ in range(5):
        check_url(cur)
        r = requests.get(cur, headers=UA, timeout=(8, 15), stream=True, allow_redirects=False)
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            cur = urllib.parse.urljoin(cur, r.headers.get("Location", ""))
            r.close()
            continue
        ctype = r.headers.get("Content-Type", "").lower()
        if not any(k in ctype for k in ("text/", "json", "xml", "javascript")):
            r.close()
            raise HTTPException(415, f"Тип содержимого не читается ({ctype or 'неизвестно'}).")
        data = b""
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) > 2_500_000:
                break
        r.close()
        enc = r.encoding or r.apparent_encoding or "utf-8"
        if "charset" not in ctype:
            m = re.search(rb'charset=["\']?([\w\-]+)', data[:3000])
            enc = m.group(1).decode() if m else "utf-8"
        try:
            return cur, r.status_code, ctype, data.decode(enc, errors="replace")
        except LookupError:
            return cur, r.status_code, ctype, data.decode("utf-8", errors="replace")
    raise HTTPException(400, "Слишком много перенаправлений.")


class ReadReq(BaseModel):
    url: str = Field(min_length=8, max_length=800)
    see: bool = False       # заодно сфотографировать страницу
    max_chars: int = Field(default=9000, ge=500, le=20000)


@router.post("/read")
def cc_read(req: ReadReq):
    okc, _c, msg = safety.check_text(req.url)
    if not okc:
        raise HTTPException(403, msg)
    try:
        final, status, ctype, body = fetch_page(req.url)
    except HTTPException:
        raise
    except Exception as e:  # noqa
        raise HTTPException(502, f"Не удалось открыть страницу: {str(e)[:200]}")
    if "html" in ctype:
        p = _Text()
        p.feed(body)
        text, title, links = "".join(p.buf), p.title.strip(), p.links
    else:
        text, title, links = body, "", []
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    okc, cat, msg = safety.check_text(text[:6000], only_hard=True)
    if not okc:
        raise HTTPException(403, msg)
    out = {"url": final, "status": status, "title": title[:200], "text": text[:req.max_chars],
           "truncated": len(text) > req.max_chars, "links": links[:15]}
    if req.see:
        try:
            s = take_shots(final, [Act(t="wait", ms=1200), Act(t="shot")], False, 1100, 700)
            out["shots"], out["urls"] = s["shots"], s["urls"]
        except Exception as e:  # noqa
            out["see_error"] = str(getattr(e, "detail", e))[:200]
    return out
