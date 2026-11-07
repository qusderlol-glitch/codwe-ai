// DEV-консоль: отдельная вкладка (не в меню аккаунта). Слева командная строка, справа живая лента того,
// что программа делает на фоне. Каждая команда показывает, что она делает. Тексты запросов и картинки сюда не попадают.
(() => {
  const A = () => window.codweAuth;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const root = document.getElementById("view-dev");
  if (!root) return;

  const KIND = {
    api: ["API", "запросы"], job: ["ЗАДАЧА", "задачи"], auth: ["ВХОД", "вход"], admin: ["АДМИН", "админ"], cmd: ["КОМАНДА", "админ"],
    block: ["ЗАЩИТА", "защита"], warn: ["ВНИМАНИЕ", "защита"], err: ["ОШИБКА", "защита"], info: ["ИНФО", "запросы"],
  };
  const FILTERS = [["all", "Всё"], ["запросы", "Запросы"], ["задачи", "Задачи"], ["вход", "Вход"], ["админ", "Админ"], ["защита", "Защита и ошибки"]];

  let built = false, cmds = [], hist = [], hi = 0, last = 0, poll = null, filt = "all", feed = [], busy = false, pulling = false, queue = [];
  try { hist = JSON.parse(localStorage.getItem("codwe.devhist") || "[]").slice(-60); } catch { hist = []; }
  hi = hist.length;

  function build() {
    if (built) return; built = true;
    root.innerHTML = `<div class="dev-wrap">
      <section class="dev-term" aria-label="Консоль">
        <div class="dev-bar"><i class="d1"></i><i class="d2"></i><i class="d3"></i><b>DEV-консоль</b><span class="dev-rank" id="devRank"></span></div>
        <div class="dev-out" id="devOut" tabindex="-1"></div>
        <div class="dev-explain" id="devExplain"><span>ℹ</span> Начните вводить команду: здесь появится, что она делает.</div>
        <form class="dev-in" id="devForm" autocomplete="off"><span class="dev-ps">codwe ❯</span><input id="devIn" spellcheck="false" autocapitalize="off" placeholder="help" maxlength="400"><button class="dev-run" title="Выполнить">⏎</button></form>
        <div class="dev-chips" id="devChips"></div>
      </section>
      <aside class="dev-feed" aria-label="Что происходит на фоне">
        <div class="dev-fh"><b>Что происходит на фоне</b><span class="live-dot"></span></div>
        <div class="dev-filters" id="devFilters">${FILTERS.map(([k, t]) => `<button data-f="${k}" class="${k === filt ? "active" : ""}">${t}</button>`).join("")}</div>
        <div class="dev-fl" id="devFeed"></div>
        <p class="dev-priv">🔒 Здесь нет текстов ваших запросов и картинок: только действия программы.</p>
      </aside></div>`;
    const inp = root.querySelector("#devIn");
    root.querySelector("#devForm").onsubmit = (e) => { e.preventDefault(); run(inp.value); };
    inp.addEventListener("input", explain);
    inp.addEventListener("keydown", onKey);
    root.querySelector("#devChips").onclick = (e) => { const b = e.target.closest("[data-c]"); if (!b) return; inp.value = b.dataset.c + " "; inp.focus(); explain(); };
    root.querySelector("#devFilters").onclick = (e) => {
      const b = e.target.closest("[data-f]"); if (!b) return; filt = b.dataset.f;
      root.querySelectorAll("#devFilters button").forEach((x) => x.classList.toggle("active", x === b)); drawFeed(true);
    };
    root.querySelector("#devOut").onclick = () => { if (!getSelection().toString()) inp.focus(); };
    out("head", "Codwe DEV-консоль");
    out("dim", "Команды выполняются сразу. Справа видно, что программа делает на фоне. Нажмите на команду внизу или напишите help.");
    out("dim", "Tab — подсказка команды, ↑ ↓ — прошлые команды.");
    loadCmds();
  }

  function out(cls, text) {
    const o = root.querySelector("#devOut"); if (!o) return;
    const atEnd = o.scrollTop + o.clientHeight >= o.scrollHeight - 30;
    const d = document.createElement("div"); d.className = "tl t-" + cls; d.textContent = text;
    o.appendChild(d);
    while (o.childElementCount > 500) o.firstChild.remove();
    if (atEnd || cls === "cmd") o.scrollTop = o.scrollHeight;
  }

  async function loadCmds() {
    try {
      cmds = (await A().call("/api/dev/commands")).commands;
      root.querySelector("#devChips").innerHTML = cmds.map((c) =>
        `<button type="button" class="dev-chip ${c.allowed ? "" : "locked"}" data-c="${esc(c.name)}" title="${esc(c.usage + " — " + c.desc + (c.allowed ? "" : " (не хватает прав)"))}">${c.allowed ? "" : "🔒 "}${esc(c.name)}</button>`).join("");
    } catch (e) { out("err", e.message); }
  }

  function explain() {
    const v = root.querySelector("#devIn").value.trim().split(/\s+/)[0].toLowerCase();
    const el = root.querySelector("#devExplain");
    const c = cmds.find((x) => x.name === v);
    if (c) el.innerHTML = `<span>ℹ</span> <b>${esc(c.usage)}</b> — ${esc(c.desc)}${c.allowed ? "" : ' <em class="lk">🔒 не хватает прав</em>'}`;
    else if (v) { const m = cmds.filter((x) => x.name.startsWith(v)).slice(0, 5); el.innerHTML = m.length ? `<span>ℹ</span> Может быть: ${m.map((x) => `<b>${esc(x.name)}</b>`).join(", ")}` : `<span>ℹ</span> Такой команды нет. Напишите <b>help</b>.`; }
    else el.innerHTML = "<span>ℹ</span> Начните вводить команду: здесь появится, что она делает.";
  }

  function onKey(e) {
    const inp = e.target;
    if (e.key === "ArrowUp") { if (hist.length) { hi = Math.max(0, hi - 1); inp.value = hist[hi] || ""; } e.preventDefault(); }
    else if (e.key === "ArrowDown") { hi = Math.min(hist.length, hi + 1); inp.value = hist[hi] || ""; e.preventDefault(); }
    else if (e.key === "Tab") {
      e.preventDefault();
      const v = inp.value.trim().toLowerCase(); if (!v || /\s/.test(v)) return;
      const m = cmds.filter((c) => c.name.startsWith(v));
      if (m.length === 1) { inp.value = m[0].name + " "; explain(); }
      else if (m.length > 1) out("dim", m.map((c) => c.name).join("   "));
    }
  }

  async function run(text) {
    text = (text || "").trim(); const inp = root.querySelector("#devIn");
    if (!text) return;
    inp.value = ""; explain();
    if (hist[hist.length - 1] !== text) hist.push(text);
    hist = hist.slice(-60); hi = hist.length;
    try { localStorage.setItem("codwe.devhist", JSON.stringify(hist)); } catch { /* */ }
    queue.push(text);
    if (busy) return;
    busy = true;
    while (queue.length) {
      const cmd = queue.shift();
      out("cmd", "codwe ❯ " + cmd);
      try {
        const r = await A().call("/api/dev/exec", "POST", { cmd });
        if (r.clear) root.querySelector("#devOut").innerHTML = "";
        if (r.explain) out("exp", "↳ " + r.explain);
        (r.lines || []).forEach((l) => { if (l.c === "act") { if (l.t === "guard-test" && window.codweGuard) window.codweGuard.test(); } else out(l.c, l.t); });
      } catch (e) { out("err", e.message); }
    }
    busy = false;
    pull();
  }

  // ───── лента ─────
  const hhmm = (t) => { const d = new Date(t * 1000); return d.toTimeString().slice(0, 8); };
  const show = (e) => filt === "all" || (KIND[e.k] || KIND.info)[1] === filt;
  function rowHtml(e) {
    const k = KIND[e.k] || KIND.info;
    return `<div class="fl k-${esc(e.k)}"><time>${hhmm(e.t)}</time><span class="tag">${k[0]}</span><span class="tx">${esc(e.x)}</span></div>`;
  }
  function drawFeed(all) {
    const f = root.querySelector("#devFeed"); if (!f) return;
    const atEnd = f.scrollTop + f.clientHeight >= f.scrollHeight - 40;
    if (all) f.innerHTML = feed.filter(show).map(rowHtml).join("");
    if (all || atEnd) f.scrollTop = f.scrollHeight;
  }
  async function pull() {
    if (pulling || document.hidden || document.body.dataset.view !== "dev") return;
    pulling = true;
    try {
      const r = await A().call("/api/dev/feed?since=" + last);
      const fresh = r.events.filter((e) => e.n > last);
      last = Math.max(last, r.last);
      if (fresh.length) {
        feed = feed.concat(fresh).slice(-400);
        const f = root.querySelector("#devFeed");
        if (f) {
          const atEnd = f.scrollTop + f.clientHeight >= f.scrollHeight - 40;
          f.insertAdjacentHTML("beforeend", fresh.filter(show).map(rowHtml).join(""));
          while (f.childElementCount > 400) f.firstChild.remove();
          if (atEnd) f.scrollTop = f.scrollHeight;
        }
      }
    } catch { /* сервер перезапускается */ }
    pulling = false;
  }

  function start() { build(); const s = A().state() || {}; const r = root.querySelector("#devRank"); if (r) r.innerHTML = `<span class="rk-pill" style="--rc:${/^#[0-9a-f]{6}$/i.test(s.rank_color) ? s.rank_color : "#8d95aa"}">${esc(s.rank_name || "")}</span>`; loadCmds(); pull(); clearInterval(poll); poll = setInterval(pull, 1200); setTimeout(() => { const i = root.querySelector("#devIn"); if (i) i.focus(); }, 80); }
  function stop() { clearInterval(poll); poll = null; }

  new MutationObserver(() => { if (document.body.dataset.view === "dev") { if (!poll) start(); } else stop(); })
    .observe(document.body, { attributes: true, attributeFilter: ["data-view"] });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && document.body.dataset.view === "dev") pull(); });

  // вкладка DEV видна только рангам с правом dev.console; если право пропало — уходим из вкладки
  window.codweDevAllow = (ok) => {
    document.body.classList.toggle("is-dev", !!ok);
    if (!ok && document.body.dataset.view === "dev" && typeof setView === "function") setView("chat");
  };
  // первый опрос состояния мог завершиться раньше, чем загрузился этот файл
  const first = A() && A().state();
  if (first) window.codweDevAllow(first.logged_in && first.access_ok && first.tos_ok && !first.tampered && !first.banned && (first.perms || []).includes("dev.console"));
})();
