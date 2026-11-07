// Страж страницы Codwe AI. Три независимых стража + сервер (guard_api.py):
//   А «Глаз»    — каждые ~15 с и при любом изменении сверяет защищённые тексты и картинки с эталоном;
//   Б «Разум»   — следит за кодом стража, настоящими функциями браузера, открытыми инструментами разработчика, F12 и за стражами А и В;
//   В «Сердце»  — отдельный поток (Worker) шлёт серверу подписанный пульс каждые 15 с и проверяет А и Б.
// Чтобы вмешаться незаметно, нужно обезвредить всех трёх сразу, а сервер закроет доступ, если пульс пропал.
// Нашли вмешательство — на экран выходит скример, сервер закрывает доступ (2 мин, потом 10 мин, потом час).
// Владельцу и рангам с правом dev.console скример не показывается (режим «наблюдение»: только запись в журнал DEV).
(() => {
  "use strict";
  if (window.codweGuard) return;
  // защита от «вставки в чужую рамку» (clickjacking)
  if (window.top !== window.self) { try { window.top.location = window.self.location; } catch { document.documentElement.innerHTML = ""; } return; }
  // тот же файл стережёт и публичный сайт: там нет сервера, поэтому пульс не шлём, а замок хранится в браузере (для вида)
  const LOCAL = /^(127\.0\.0\.1|localhost|\[::1\])$/.test(location.hostname);
  const rawFetch = window.fetch.bind(window);
  const NATIVE = Function.prototype.toString;
  const H = { "X-Codwe": "1", "Content-Type": "application/json" };
  const ST = { sid: "", key: null, seq: 0, observe: true, every: 15, tripped: false, a: 0, b: 0, dt: 0, grace: 0, lockT: 0, said: {} };
  const seen = { A: Date.now(), B: Date.now(), C: Date.now() };
  const now = () => Date.now();
  const isApi = (u) => { try { const x = new URL(typeof u === "string" ? u : u.url, location.href); return x.origin === location.origin && x.pathname.startsWith("/api/"); } catch { return false; } };

  // ───── подпись пульса ─────
  const hex = (buf) => Array.from(new Uint8Array(buf), (x) => x.toString(16).padStart(2, "0")).join("");
  async function mac(...parts) {
    const k = await crypto.subtle.importKey("raw", Uint8Array.from(ST.key.match(/../g), (h) => parseInt(h, 16)), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    return hex(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(parts.join("|"))));
  }
  async function post(path, body) {
    if (!LOCAL) return { status: 0, j: {} };
    const r = await rawFetch(path, { method: "POST", headers: H, body: JSON.stringify(body || {}), keepalive: true });
    let j = {}; try { j = await r.json(); } catch { /* */ }
    return { status: r.status, j };
  }

  // ───── связь с сервером ─────
  let helloP = null;
  function hello() {
    if (!LOCAL) { ST.observe = false; ST.sid = "site"; return Promise.resolve({}); }
    if (!helloP) helloP = (async () => {
      const { j } = await post("/api/guard/hello");
      if (!j.sid) throw new Error("hello");
      Object.assign(ST, { sid: j.sid, key: j.key, seq: 0, every: j.every || 15, observe: !!j.observe });
      if (j.locked_for > 0 && !j.observe) lockScreen(j.locked_for, j.reason);
      return j;
    })().finally(() => { helloP = null; });
    return helloP;
  }
  let beating = null;
  function beat() {
    if (!LOCAL) { seen.C = now(); return Promise.resolve(); }
    if (beating) return beating;
    beating = (async () => {
      try {
        if (!ST.sid) await hello();
        const seq = ++ST.seq, ts = now(), dt = 0;
        const m = await mac(ST.sid, seq, ts, ST.a, ST.b, dt);
        const r = await post("/api/guard/beat", { sid: ST.sid, seq, ts, a: ST.a, b: ST.b, dt, mac: m });
        if (r.status === 409) { ST.sid = ""; return; }
        if (r.j && typeof r.j.observe === "boolean") ST.observe = r.j.observe;
        if (r.j && r.j.locked_for > 0 && !ST.observe && !ST.tripped) lockScreen(r.j.locked_for, r.j.reason);
        seen.C = now();
      } catch { /* сервер перезапускается: пульс попробуем в следующий раз */ }
      finally { beating = null; }
    })();
    return beating;
  }
  async function report(layer, reason) {
    if (!LOCAL) {   // сайт: «замок» запоминается в этом браузере и усиливается при повторах
      try { const n = (+localStorage.getItem("cg_s") || 0) + 1; localStorage.setItem("cg_s", n); localStorage.setItem("cg_l", now() + [120, 600, 3600][Math.min(n - 1, 2)] * 1000); localStorage.setItem("cg_r", reason); } catch { /* */ }
      return;
    }
    try {
      if (!ST.sid) await hello();
      const seq = ST.seq + 1, ts = now(), L = { "А": "A", "Б": "B", "В": "C" }[layer] || "A";
      await post("/api/guard/trip", { sid: ST.sid, seq, ts, layer: L, reason, mac: await mac(ST.sid, seq, ts, L, reason) });
    } catch { /* */ }
  }

  // все запросы к /api/ несут пропуск стража; при «страж не отвечает» один раз оживляем пульс и повторяем
  if (LOCAL) window.fetch = async function (input, init) {
    const api = isApi(input);
    const go = () => {
      if (!api || !ST.sid) return rawFetch(input, init);
      const h = new Headers((init && init.headers) || (typeof input !== "string" && input.headers) || {});
      h.set("X-Guard", ST.sid);
      return rawFetch(input, Object.assign({}, init || {}, { headers: h }));
    };
    if (api && !ST.sid) { try { await hello(); } catch { /* */ } }
    let r = await go();
    if (api && r.status === 403 && !ST.tripped) {
      let j = null; try { j = await r.clone().json(); } catch { /* */ }
      if (j && j.code === "guard") { ST.sid = ""; await beat(); if (ST.sid) r = await go(); }
    }
    return r;
  };

  // ───── что именно защищаем ─────
  // [data-g] — тексты и картинки, которые приложение само не меняет; «постоянные» нельзя и убирать со страницы.
  const SEL = "[data-g], #logoBtn img, #nav .nav-btn > span";
  const PERSIST = "#logoBtn img, #nav .nav-btn > span";
  const base = new Map();      // элемент -> эталон
  let persistN = 0;
  const sigOf = (el) => {
    const t = (el.textContent || "").replace(/\s+/g, " ").trim();
    const at = ["src", "href", "alt", "title", "hidden"].map((k) => el.getAttribute(k) || "").join("~");
    return t + "|" + at;
  };
  const h32 = (s) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0).toString(36); };

  // ───── вмешательство ─────
  function trip(layer, reason) {
    if (ST.tripped) return;
    if (ST.observe) {  // наблюдение: пишем в журнал DEV, не чаще раза в минуту на одну причину
      if (now() - (ST.said[reason] || 0) < 60000) return;
      ST.said[reason] = now();
      try { console.warn("[страж " + layer + "] " + reason); } catch { /* */ }
      toast("Страж " + layer + " (наблюдение): " + reason); report(layer, reason); return;
    }
    ST.tripped = true;
    scream(reason);
    report(layer, reason);
  }
  function toast(t) {
    const n = document.createElement("div");
    n.style.cssText = "position:fixed;left:14px;bottom:14px;z-index:2147483000;background:#1b0b0b;color:#ffb3b3;border:1px solid #ff4d4d;border-radius:10px;padding:10px 14px;font:13px system-ui;max-width:380px;box-shadow:0 6px 30px #0008";
    n.textContent = "🛡 " + t; document.body && document.body.appendChild(n); setTimeout(() => n.remove(), 6000);
  }

  // ───── страж А «Глаз»: тексты и картинки ─────
  function eyeCheck(reason0) {
    seen.A = now(); ST.a++;
    if (document.querySelector("html.translated-ltr,html.translated-rtl,font[style*='vertical-align']")) { othersAlive("А"); return; }  // автоперевод страницы — не взлом
    document.querySelectorAll(SEL).forEach((el) => {
      const s = sigOf(el), b = base.get(el);
      if (b === undefined) base.set(el, { s, p: el.matches(PERSIST) });
      else if (b.s !== s) trip("А", (reason0 || "изменён защищённый текст или картинка") + ": «" + (el.textContent || el.getAttribute("src") || "").trim().slice(0, 40) + "»");
    });
    for (const [el, b] of base) if (!el.isConnected) { if (b.p) trip("А", "убран защищённый элемент страницы"); else base.delete(el); }
    const n = document.querySelectorAll(PERSIST).length;
    if (persistN && n < persistN) trip("А", "убран защищённый элемент страницы");
    persistN = Math.max(persistN, n);
    // на странице не должно появиться чужих скриптов
    document.querySelectorAll("script[src]").forEach((s) => { try { const u = new URL(s.src, location.href); if (/^https?:$/.test(u.protocol) && u.origin !== location.origin) trip("А", "на страницу добавлен чужой скрипт"); } catch { /* */ } });
    othersAlive("А");
  }
  function startEye() {
    setInterval(() => eyeCheck(), 15000 + Math.floor(Math.random() * 1500));
    // мгновенная реакция: как только тронули защищённый элемент
    let pend = false;
    new MutationObserver((ml) => {
      if (pend || !ml.some((m) => { const t = m.target.nodeType === 1 ? m.target : m.target.parentElement; return t && t.closest && t.closest(SEL) && base.has(t.closest(SEL)); })) return;
      pend = true; requestAnimationFrame(() => { pend = false; eyeCheck("защищённый текст или картинка изменены на лету"); });
    }).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["src", "href", "alt", "title", "hidden"] });
    setTimeout(() => eyeCheck(), 1200);
  }

  // ───── страж Б «Разум»: код, браузер, инструменты разработчика ─────
  let ownSig = "";
  const ownHash = () => h32([trip, eyeCheck, report, beat, sigOf, lockScreen].map((f) => NATIVE.call(f)).join("#"));
  function devtoolsProbe() {
    if (ST.observe || ST.tripped) return false;   // владельца и режим разработчика не трогаем
    let opened = false;
    const e = new Error();
    Object.defineProperty(e, "stack", { get() { opened = true; return ""; } });
    try { console.debug(e); } catch { /* */ }
    const t = performance.now();
    (function () { debugger; })();           // при открытых инструментах разработчика страница встаёт на паузу
    if (performance.now() - t > 150) opened = true;
    return opened;
  }
  function mindCheck() {
    seen.B = now(); ST.b++;
    if (ownHash() !== ownSig) trip("Б", "код стража изменён");
    if (!/\[native code\]/.test(NATIVE.call(rawFetch))) trip("Б", "подменена функция fetch");
    for (const f of [JSON.parse, Element.prototype.remove, Node.prototype.removeChild, EventTarget.prototype.addEventListener]) {
      if (!/\[native code\]/.test(NATIVE.call(f))) { trip("Б", "подменена функция браузера"); break; }
    }
    if (!/\[native code\]/.test(NATIVE.call(NATIVE))) trip("Б", "подменён toString");
    if (devtoolsProbe()) { ST.dt = 1; trip("Б", "открыты инструменты разработчика"); }
    othersAlive("Б");
  }
  function startMind() {
    ownSig = ownHash();
    const loop = () => { setTimeout(() => { mindCheck(); loop(); }, 12000 + Math.floor(Math.random() * 5000)); };
    loop();
    setInterval(() => { if (!ST.tripped && devtoolsProbe()) { ST.dt = 1; trip("Б", "открыты инструменты разработчика"); } }, 4000);
    // горячие клавиши
    addEventListener("keydown", (e) => {
      const k = (e.key || "").toLowerCase(), c = e.ctrlKey || e.metaKey;
      if (e.key === "F12" || (c && e.shiftKey && ["i", "j", "c", "k"].includes(k)) || (c && !e.shiftKey && k === "u") || (e.metaKey && e.altKey && ["i", "j", "c", "u"].includes(k))) {
        if (!ST.observe) { e.preventDefault(); e.stopImmediatePropagation(); }
        ST.dt = 1; trip("Б", "нажата " + (e.key === "F12" ? "F12" : "комбинация клавиш инструментов разработчика"));
      }
    }, true);
    // меню «Просмотреть код» и перетаскивание картинок
    addEventListener("contextmenu", (e) => { if (ST.observe) return; const t = e.target; if (!(t && t.closest && t.closest("input,textarea,[contenteditable='true'],.msg,.code,pre,.tos-box"))) e.preventDefault(); }, true);
    addEventListener("dragstart", (e) => { if (e.target && e.target.tagName === "IMG" && e.target.closest("#topbar,.welcome,.auth-layer")) e.preventDefault(); }, true);
  }

  // ───── страж В «Сердце»: отдельный поток шлёт пульс ─────
  function startHeart() {
    let w;
    try { w = new Worker(URL.createObjectURL(new Blob(["setInterval(()=>postMessage(1)," + ST.every * 1000 + ")"], { type: "text/javascript" }))); }
    catch { setInterval(beat, ST.every * 1000); return; }
    w.onmessage = () => { seen.C = now(); beat(); othersAlive("В"); };
    const wake = () => { ST.grace = now() + 40000; beat(); };
    document.addEventListener("visibilitychange", () => { if (!document.hidden) wake(); });
    addEventListener("focus", wake); addEventListener("online", wake); addEventListener("pageshow", wake);
    addEventListener("pagehide", () => { try { rawFetch("/api/guard/bye", { method: "POST", headers: H, body: JSON.stringify({ sid: ST.sid }), keepalive: true }); } catch { /* */ } });
    beat();
  }

  // стражи следят друг за другом: если кого-то «вырубили», остальные поднимают тревогу
  function othersAlive(me) {
    if (document.hidden || now() < ST.grace || ST.tripped) return;
    const lim = 75000, t = now();
    for (const [k, name] of [["A", "А"], ["B", "Б"], ["C", "В"]]) if (name !== me && t - seen[k] > lim) { trip(me, "страж " + name + " остановлен"); return; }
  }

  // ───── скример ─────
  function scream(reason) {
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;inset:0;z-index:2147483647;";
    const root = host.attachShadow({ mode: "closed" });
    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
    root.innerHTML = `<style>
      :host{all:initial} *{box-sizing:border-box}
      .w{position:fixed;inset:0;background:#000;display:grid;place-items:center;overflow:hidden;font-family:system-ui,Segoe UI,sans-serif;cursor:none;user-select:none}
      .v{position:absolute;inset:0;background:radial-gradient(ellipse at center,rgba(0,0,0,0) 30%,rgba(140,0,0,.9) 100%);animation:${calm ? "none" : "pulse 1.4s ease-in-out infinite"}}
      canvas{position:absolute;inset:0;width:100%;height:100%;opacity:.28;image-rendering:pixelated}
      svg{position:relative;width:min(78vmin,760px);height:auto;animation:${calm ? "none" : "zoom .28s cubic-bezier(.2,1.6,.4,1) both,shake .09s steps(2) .28s infinite"}}
      h1{position:absolute;bottom:6vh;left:0;right:0;margin:0;text-align:center;color:#ff2b2b;font-size:clamp(26px,6vw,64px);letter-spacing:4px;text-shadow:0 0 24px #f00;font-weight:900}
      p{position:absolute;bottom:2.4vh;left:0;right:0;margin:0;text-align:center;color:#ffb0b0;font-size:clamp(12px,1.6vw,17px)}
      @keyframes zoom{from{transform:scale(.15)}to{transform:scale(1.12)}}
      @keyframes shake{0%{transform:translate(0,0) scale(1.12)}50%{transform:translate(6px,-5px) scale(1.14)}100%{transform:translate(-6px,4px) scale(1.12)}}
      @keyframes pulse{0%,100%{opacity:.55}50%{opacity:1}}
    </style>
    <div class="w"><canvas id="n" width="160" height="90"></canvas><div class="v"></div>
      <svg viewBox="0 0 400 420" aria-hidden="true">
        <defs><radialGradient id="g" cx="50%" cy="40%" r="65%"><stop offset="0" stop-color="#3a3a3a"/><stop offset="1" stop-color="#050505"/></radialGradient>
        <radialGradient id="e" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#fff2b0"/><stop offset=".35" stop-color="#ff3a1a"/><stop offset="1" stop-color="#600"/></radialGradient></defs>
        <path d="M200 10C95 10 30 90 38 200c4 56 30 82 54 98v70h216v-70c24-16 50-42 54-98C370 90 305 10 200 10z" fill="url(#g)" stroke="#a00" stroke-width="4"/>
        <ellipse cx="135" cy="170" rx="58" ry="66" fill="#000"/><ellipse cx="265" cy="170" rx="58" ry="66" fill="#000"/>
        <circle cx="135" cy="176" r="30" fill="url(#e)"/><circle cx="265" cy="176" r="30" fill="url(#e)"/>
        <ellipse cx="135" cy="176" rx="6" ry="24" fill="#000"/><ellipse cx="265" cy="176" rx="6" ry="24" fill="#000"/>
        <path d="M200 215l-26 56h52z" fill="#000"/>
        <path d="M70 318c40 46 220 46 260 0v52c-30 30-230 30-260 0z" fill="#000" stroke="#a00" stroke-width="3"/>
        <path d="M92 326l14 40M124 340l10 44M158 346l6 46M200 348v48M242 346l-6 46M276 340l-10 44M308 326l-14 40" stroke="#e8e0cc" stroke-width="13" stroke-linecap="round"/>
      </svg>
      <h1>Я ВИЖУ ТЕБЯ</h1><p>Обнаружено вмешательство в страницу: ${String(reason).replace(/[<>&]/g, "")}. Доступ закрыт. Закройте окно.</p></div>`;
    document.documentElement.appendChild(host);
    new MutationObserver(() => { if (!host.isConnected) document.documentElement.appendChild(host); }).observe(document.documentElement, { childList: true });
    for (const t of ["keydown", "keyup", "keypress", "mousedown", "mouseup", "click", "dblclick", "contextmenu", "wheel", "touchstart", "paste", "cut", "copy"]) addEventListener(t, (e) => { e.preventDefault(); e.stopImmediatePropagation(); }, true);
    try { document.querySelectorAll("video,audio").forEach((m) => m.pause()); } catch { /* */ }
    // шум «помех» (медленно: меняется 8 раз в секунду, без резких вспышек)
    const cv = root.getElementById("n"), cx = cv.getContext("2d"), im = cx.createImageData(160, 90);
    const noise = () => { for (let i = 0; i < im.data.length; i += 4) { const v = Math.random() * 255; im.data[i] = v; im.data[i + 1] = v * .2; im.data[i + 2] = v * .2; im.data[i + 3] = 255; } cx.putImageData(im, 0, 0); };
    noise(); if (!calm) setInterval(noise, 125);
    if (!calm) sound();
    return host;
  }
  function sound() {
    try {
      const A = window.AudioContext || window.webkitAudioContext, c = new A();
      const comp = c.createDynamicsCompressor(), master = c.createGain();
      master.gain.value = 0.28; comp.connect(master); master.connect(c.destination);
      const t0 = c.currentTime, dur = 2.4;
      for (const [f0, f1, type, vol] of [[520, 1700, "sawtooth", 0.5], [780, 2300, "square", 0.22], [60, 40, "sine", 0.9]]) {
        const o = c.createOscillator(), g = c.createGain(), lfo = c.createOscillator(), lg = c.createGain();
        o.type = type; o.frequency.setValueAtTime(f0, t0); o.frequency.exponentialRampToValueAtTime(f1, t0 + 0.5); o.frequency.exponentialRampToValueAtTime(f0 * 1.3, t0 + dur);
        lfo.frequency.value = 18; lg.gain.value = f0 * 0.06; lfo.connect(lg); lg.connect(o.frequency);
        g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(vol, t0 + 0.04); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
        o.connect(g); g.connect(comp); o.start(t0); lfo.start(t0); o.stop(t0 + dur); lfo.stop(t0 + dur);
      }
      const nb = c.createBuffer(1, c.sampleRate * dur, c.sampleRate), d = nb.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
      const ns = c.createBufferSource(), nf = c.createBiquadFilter(), ng = c.createGain();
      ns.buffer = nb; nf.type = "bandpass"; nf.frequency.value = 2400; ng.gain.value = 0.5; ns.connect(nf); nf.connect(ng); ng.connect(comp); ns.start(t0);
      c.resume && c.resume();
    } catch { /* звука не будет */ }
  }

  // экран «доступ закрыт» после перезагрузки (скример второй раз не показываем)
  function lockScreen(sec, reason) {
    if (document.getElementById("guardLock")) return;
    const n = document.createElement("div"); n.id = "guardLock";
    n.style.cssText = "position:fixed;inset:0;z-index:2147483000;background:#120000;color:#fff;display:grid;place-items:center;text-align:center;font-family:system-ui,Segoe UI,sans-serif;padding:24px";
    n.innerHTML = `<div style="max-width:560px"><div style="font-size:80px">🛡️</div><h1 style="color:#ff3b3b;margin:6px 0 10px;font-size:34px">ВМЕШАТЕЛЬСТВО ОБНАРУЖЕНО</h1><p style="color:#ffb3b3;margin:0 0 6px"></p><p style="font-size:42px;margin:6px 0" id="gl-t"></p><p style="opacity:.7;font-size:14px">Доступ закрыт. Повторные нарушения закрывают его на дольше.</p></div>`;
    n.querySelector("p").textContent = reason || "Страница была изменена или открыты инструменты разработчика.";
    document.body.appendChild(n);
    let left = Math.max(1, sec | 0); const t = n.querySelector("#gl-t");
    const upd = () => { t.textContent = left > 0 ? Math.floor(left / 60) + ":" + String(left % 60).padStart(2, "0") : "Можно перезагрузить страницу"; };
    upd(); ST.lockT = setInterval(() => { left--; upd(); if (left <= -1) { clearInterval(ST.lockT); location.reload(); } }, 1000);
  }

  // ───── запуск ─────
  const boot = () => {
    if (!LOCAL) { try { const t = +localStorage.getItem("cg_l") || 0; if (t > now()) lockScreen(Math.ceil((t - now()) / 1000), localStorage.getItem("cg_r")); } catch { /* */ } }
    startEye(); startMind(); startHeart();
  };
  hello().catch(() => {}).finally(() => { if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot(); });
  // проверка скримера из DEV-консоли (только в режиме наблюдения; в защите он и так сработает по-настоящему)
  Object.defineProperty(window, "codweGuard", { value: Object.freeze({
    test() { if (!ST.observe) return false; scream("проверка (команда guard test)"); setTimeout(() => location.reload(), 4200); return true; },
  }), writable: false, configurable: false });
})();
