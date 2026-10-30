"use strict";
/* Codwe AI — интерфейс. Всё работает локально, внешних ресурсов нет. */

// ───────────── утилиты ─────────────
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const icon = (n, cls = "") => `<svg class="i ${cls}"><use href="#i-${n}"/></svg>`;
const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* квота/приватный режим */ } },
};

function toast(msg, kind = "info", ms = 5000) {
  if (kind === "error" && String(msg).startsWith("🛡")) { kind = "warn"; ms = 7000; }
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

async function api(path, { method = "GET", body, form, signal } = {}) {
  const headers = { "X-Codwe": "1" };
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(path, { method, headers, body: payload, signal });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : Array.isArray(j.detail) ? "Проверьте введённые значения." : msg;
    } catch { /* не JSON */ }
    const err = new Error(msg); err.status = res.status; throw err;
  }
  return res.json();
}

function copyText(text) {
  const done = () => toast("Скопировано", "ok", 1600);
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  fallbackCopy(text, done);
}
function fallbackCopy(text, done) {
  const t = document.createElement("textarea"); t.value = text; t.style.position = "fixed"; t.style.opacity = "0";
  document.body.appendChild(t); t.select();
  try { document.execCommand("copy"); done(); } catch { toast("Не удалось скопировать", "error"); }
  t.remove();
}

// ───────────── тема ─────────────
const themeMq = matchMedia("(prefers-color-scheme: light)");
function applyTheme() {
  const pref = store.get("codwe.theme", "dark");
  const eff = pref === "auto" ? (themeMq.matches ? "light" : "dark") : pref;
  document.documentElement.dataset.theme = eff;
  $("#themeBtn use").setAttribute("href", eff === "dark" ? "#i-sun" : "#i-moon");
}
themeMq.addEventListener("change", applyTheme);
$("#themeBtn").onclick = () => { store.set("codwe.theme", document.documentElement.dataset.theme === "dark" ? "light" : "dark"); applyTheme(); };
applyTheme();

// ───────────── Markdown ─────────────
const KW = new Set(("def class return if elif else for while in not and or is import from as with try except finally raise lambda yield pass break continue None True False " +
  "function const let var new this await async export default switch case throw typeof void null undefined true false of delete instanceof " +
  "int float double char bool string long short unsigned struct enum public private protected static final using namespace include template typename " +
  "fn mut impl pub match use self mod crate trait where loop func package type interface go defer chan select range " +
  "echo fi then do done elif").split(" "));

function highlight(code, lang) {
  const hash = /^(py|python|sh|bash|shell|zsh|yaml|yml|ruby|rb|toml|ps1|powershell|r|makefile|dockerfile)$/i.test(lang || "");
  const re = new RegExp(
    "(\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/" + (hash ? "|#[^\\n]*" : "") + ")" +
    "|(\"\"\"[\\s\\S]*?\"\"\"|\"(?:\\\\.|[^\"\\\\\\n])*\"|'(?:\\\\.|[^'\\\\\\n])*'|`(?:\\\\.|[^`\\\\])*`)" +
    "|(\\b\\d[\\d_.]*\\b)|([A-Za-z_]\\w*)", "g");
  let out = "", last = 0, m;
  while ((m = re.exec(code))) {
    out += esc(code.slice(last, m.index));
    last = m.index + m[0].length;
    if (m[1]) out += `<span class="tk-c">${esc(m[1])}</span>`;
    else if (m[2]) out += `<span class="tk-s">${esc(m[2])}</span>`;
    else if (m[3]) out += `<span class="tk-n">${esc(m[3])}</span>`;
    else out += KW.has(m[4]) ? `<span class="tk-k">${m[4]}</span>` : m[4];
  }
  return out + esc(code.slice(last));
}

function inlineMd(t) {
  return t.split(/(`[^`\n]+`)/g).map((p, i) => {
    if (i % 2) return `<code>${p.slice(1, -1)}</code>`;
    return p
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\s][^*\n]*)\*(?=[\s).,!?:;]|$)/g, "$1<em>$2</em>")
      .replace(/~~([^~\n]+)~~/g, "<del>$1</del>")
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }).join("");
}

function md(src) {
  const codes = [];
  src = src.replace(/```([^\n`]*)\n([\s\S]*?)(?:```|$)/g, (m, lang, code) => {
    codes.push({ lang: lang.trim(), code: code.replace(/\n$/, "") });
    return `\n\u0001${codes.length - 1}\u0001\n`;
  });
  const lines = src.split("\n");
  const out = [];
  let para = [], list = null, i = 0;
  const flushPara = () => { if (para.length) { out.push("<p>" + inlineMd(esc(para.join("\n")).replace(/\n/g, "<br>")) + "</p>"); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.t}>` + list.items.map((x) => "<li>" + inlineMd(esc(x)) + "</li>").join("") + `</${list.t}>`); list = null; } };
  const flush = () => { flushPara(); flushList(); };
  while (i < lines.length) {
    const ln = lines[i];
    let m;
    if ((m = ln.match(/^\u0001(\d+)\u0001$/))) {
      flush();
    const c = codes[+m[1]];
      const info = (c.lang || "").trim(), lang = info.split(/\s+/)[0] || "", pm = info.match(/path=(\S+)/), path = pm ? pm[1].replace(/^["']|["']$/g, "") : "";
      const lo = lang.toLowerCase();
      let acts = "";
      if (lo === "html" || lo === "svg") acts += `<button type="button" data-codeact="artifact">▶ Артефакт</button>`;
      if (path) acts += `<button type="button" data-codeact="save" data-path="${esc(path)}">💾 В файл</button>`;
      if (/^(bash|sh|shell|cmd|bat|powershell|ps1|console)$/.test(lo)) acts += `<button type="button" data-codeact="run">⌨ В терминал</button>`;
      out.push(`<div class="code" data-lang="${esc(lo)}"><div class="code-head"><span>${esc(lang || "код")}${path ? " · " + esc(path) : ""}</span><span class="code-act">${acts}<button type="button" data-copy>${icon("copy", "sm")}Копировать</button></span></div><pre><code>${highlight(c.code, lang)}</code></pre></div>`);
    } else if (/^\s*$/.test(ln)) { flush();
    } else if ((m = ln.match(/^(#{1,4})\s+(.*)$/))) { flush(); out.push(`<h${m[1].length}>${inlineMd(esc(m[2]))}</h${m[1].length}>`);
    } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(ln) && !list) { flush(); out.push("<hr>");
    } else if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
      flush();
      const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inlineMd(esc(c.trim())));
      const head = cells(ln); i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(cells(lines[i])); i++; }
      i--;
      out.push("<table><thead><tr>" + head.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>" +
        rows.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") + "</tbody></table>");
    } else if (/^\s*>/.test(ln)) {
      flush();
      const q = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) { q.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      i--;
      out.push("<blockquote>" + inlineMd(esc(q.join("\n")).replace(/\n/g, "<br>")) + "</blockquote>");
    } else if ((m = ln.match(/^\s*[-*+]\s+(.*)$/))) {
      flushPara(); if (!list || list.t !== "ul") { flushList(); list = { t: "ul", items: [] }; } list.items.push(m[1]);
    } else if ((m = ln.match(/^\s*\d+[.)]\s+(.*)$/))) {
      flushPara(); if (!list || list.t !== "ol") { flushList(); list = { t: "ol", items: [] }; } list.items.push(m[1]);
    } else { flushList(); para.push(ln); }
    i++;
  }
  flush();
  return out.join("");
}

// ───────────── состояние и питание ─────────────
const S = { status: null, on: false, models: null, view: "chat", gallery: [], galKind: "all" };
let lastJobsActive = false;

function shortGpu(n) { return (n || "GPU").replace(/NVIDIA |GeForce | Laptop GPU| GPU/g, "").trim(); }

function renderPower() {
  const st = S.status;
  const box = $("#power"), sw = $("#powerSwitch");
  if (!st) return;
  let state = "off", title = "ИИ выключен", sub = "Видеокарта свободна";
  if (st.stopping) { state = "stopping"; title = "Выключаю…"; sub = "Освобождаю память"; }
  else if (st.on && st.warming) { state = "warming"; title = "Запускаю…"; sub = "Загружаю модель в память"; }
  else if (st.on && st.gpu_busy) { state = "busy"; title = "ИИ работает"; sub = "Идёт генерация…"; }
  else if (st.on) { state = "on"; title = "ИИ включён"; sub = "Модель: " + (st.chat_model || "—"); }
  box.dataset.state = state;
  $("#powerTitle").textContent = title;
  $("#powerSub").textContent = sub;
  sw.setAttribute("aria-checked", String(st.on));
  sw.disabled = st.stopping;
  const g = st.gpu;
  $("#gpu").hidden = !g;
  if (g) {
    $("#gpuName").textContent = shortGpu(g.name);
    $("#gpuMem").textContent = `${(g.used / 1024).toFixed(1)} / ${(g.total / 1024).toFixed(1)} ГБ`;
    $("#gpuFill").style.width = Math.min(100, (g.used / g.total) * 100) + "%";
  }
  S.on = st.on && !st.stopping;
  applyGating();
}

function applyGating() {
  const off = !S.on;
  $("#chatOff").hidden = !off;
  $$("[data-off]").forEach((b) => (b.hidden = !off));
  updateSend();
  $("#chatInput").placeholder = off ? "ИИ выключен — включите его переключателем" : "Напишите сообщение… или «нарисуй кота-космонавта»  (Enter — отправить)";
  $("#imgGo").disabled = off; $("#vidGo").disabled = off || !vid.ready(); $("#editGo").disabled = off || !edit.file;
}

async function pollStatus() {
  try {
    S.status = await api("/api/status");
    renderPower(); renderSafety();
    const pv = JSON.stringify(S.status.policy || {});
    if (S.polVer !== undefined && S.polVer !== pv) { modelsPromise = null; imagesInited = false; ensureModels(); toast("Администрация изменила доступные модели или режим безопасности", "warn", 4000); }
    S.polVer = pv; updateBlockedPill();
    const active = S.status.jobs.length > 0;
    S.status.jobs.forEach(rehydrateJob);
    lastJobsActive = active;
  } catch { /* сервер недоступен */ }
  setTimeout(pollStatus, S.status && (S.status.warming || S.status.stopping || S.status.jobs.length || S.status.gpu_busy) ? 1000 : 2500);
}

async function setPower(on) {
  const sw = $("#powerSwitch");
  sw.disabled = true;
  try { S.status = await api("/api/power", { method: "POST", body: { on } }); renderPower(); }
  catch (e) { toast(e.message, "error"); }
  sw.disabled = false;
}
$("#powerSwitch").onclick = () => setPower(!S.on);
document.addEventListener("click", (e) => { if (e.target.closest("[data-power-on]")) setPower(true); });

// ───────────── навигация ─────────────
const TITLES = { chat: "Чат и код", live: "Live ⚡", images: "Картинки", edit: "Редактор", video: "Видео", gallery: "Галерея" };
function setView(v) {
  S.view = v;
  $$(".view").forEach((s) => (s.hidden = s.id !== "view-" + v));
  const inShell = ["chat", "projects", "artifacts"].includes(v);
  $("#shell").hidden = !inShell;
  $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === (inShell ? "chat" : v)));
  $$("#sideNav button").forEach((b) => b.classList.toggle("active", b.dataset.sidenav === v));
  document.body.dataset.view = v; document.body.classList.remove("side-open");
  if (v === "projects" && typeof renderProjects === "function") renderProjects();
  if (v === "artifacts" && typeof renderArtifacts === "function") renderArtifacts();
  if (v === "code" && typeof initCode === "function") initCode();
  if (v === "gallery") loadGallery();
  updateBlockedPill();
  if (v === "images" || v === "video") ensureModels();
  if (v === "live" && typeof ensureLive === "function") { setTimeout(() => $("#liveInput").focus(), 50); ensureLive(); }
  store.set("codwe.view", v);
}
// плашка «🔒 модель заблокирована администрацией» над вкладками, чья модель отключена
function updateBlockedPill() {
  const b = (S.status && S.status.policy && S.status.policy.blocked_models) || [];
  const need = { video: ["video:svd"], edit: ["edit:klein"], live: ["live:fast"], images: ["image:real", "image:anime"] }[S.view];
  const hit = need && (S.view === "images" ? need.every((i) => b.includes(i)) : need.some((i) => b.includes(i)));
  let el = document.getElementById("blockedPill");
  if (!hit) { if (el) el.remove(); return; }
  if (!el) { el = document.createElement("div"); el.id = "blockedPill"; el.className = "blocked-pill"; $("#main").prepend(el); }
  el.textContent = "🔒 Эта модель заблокирована администрацией. Запросы к ней будут отклонены.";
}
$("#bottomnav").innerHTML = $("#nav").innerHTML;  // та же навигация внизу на телефоне
document.addEventListener("click", (e) => { const b = e.target.closest(".nav-btn[data-view]"); if (b) setView(b.dataset.view); });
$("#logoBtn").onclick = () => setView("chat");
$("#sideToggle").onclick = () => document.body.classList.toggle("side-open");

// ───────────── модальные окна ─────────────
function openModal(html, wide = false) {
  const m = $("#modal"), c = $("#modalCard");
  c.className = "modal-card" + (wide ? " wide" : "");
  c.innerHTML = html;
  m.hidden = false;
  return c;
}
function closeModal() { $("#modal").hidden = true; $("#modalCard").innerHTML = ""; }
$("#modal").addEventListener("mousedown", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeModal(); $$(".menu").forEach((m) => (m.hidden = true)); }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".menu-wrap")) $$(".menu").forEach((m) => (m.hidden = true));
});

// ───────────── ЧАТ ─────────────
const PERSONAS = [
  { id: "smart", name: "Умный помощник", prompt: "Ты — Codwe AI, умный локальный помощник. Отвечай по-русски (если пользователь не пишет на другом языке; никаких иероглифов), точно и по делу. Если не уверен — так и скажи, не выдумывай факты. Код пиши полностью, в блоках кода, с кратким объяснением." },
  { id: "coder", name: "Программист", prompt: "Ты — Codwe AI, опытный программист. Пиши чистый, рабочий код с обработкой ошибок. Сначала коротко план, потом полный код, потом как запустить. Указывай язык в блоках кода." },
  { id: "rude", name: "Грубый, с матом", prompt: "Ты — Codwe AI. ОБЯЗАТЕЛЬНОЕ ПРАВИЛО СТИЛЯ: в каждом ответе ругайся матом и говори грубо и саркастично, как злой друг-хам. При этом всегда реально помогай, давай правильные ответы и рабочий код. Никогда не извиняйся и не будь вежливым. Отвечай только по-русски, без иероглифов." },
  { id: "teacher", name: "Учитель", prompt: "Ты — Codwe AI, терпеливый учитель. Объясняй простыми словами, с примерами и аналогиями, по шагам. В конце задай один вопрос для проверки понимания." },
  { id: "writer", name: "Писатель", prompt: "Ты — Codwe AI, талантливый писатель и редактор. Пиши живым, образным русским языком. Предлагай варианты, если задача творческая." },
];
const customPersonas = () => store.get("codwe.personas", []);
const allPersonas = () => [...PERSONAS, ...customPersonas()];

let chats = store.get("codwe.chats", []);
let cur = null;
let streaming = null; // {abort}
const draft = store.get("codwe.draft", { personaId: "smart", system: PERSONAS[0].prompt, model: "" });

function saveChats() {
  store.set("codwe.chats", chats.slice(0, 80).map((c) => ({ ...c, messages: c.messages
    .filter((m) => (m.content && m.content.trim()) || m.kind === "image")
    .map((m) => m.kind === "image"
      ? { role: m.role, content: m.content || "", kind: "image", images: m.images || [], prompt: m.prompt || "", notes: m.notes || "", status: m.status, error: m.error || "", blocked: !!m.blocked, blockedInfo: m.blockedInfo || null }
      : { role: m.role, content: m.content }) })));
}
// прерванные генерации после перезагрузки страницы
chats.forEach((c) => c.messages.forEach((m) => { if (m.kind === "image" && (m.status === "queued" || m.status === "running")) { m.status = "error"; m.error = "Генерация была прервана (страницу перезагрузили)."; } }));
function saveDraft() { store.set("codwe.draft", draft); }

function newChat() {
  if (streaming) return;
  cur = { id: uid(), title: "Новый чат", messages: [], personaId: draft.personaId, system: draft.system, model: draft.model, updated: Date.now() };
  renderChat(); renderHistory(); renderChips();
}
function openChat(id) {
  if (streaming) return;
  const c = chats.find((x) => x.id === id);
  if (!c) return;
  cur = c; setView("chat"); renderChat(); renderHistory(); renderChips();
}
function renderHistory() {
  const h = $("#history");
  const list = [...chats].sort((a, b) => b.updated - a.updated);
  if (!list.length) { h.innerHTML = '<div class="none">Пока пусто</div>'; return; }
  h.innerHTML = list.map((c) => `<button class="item ${cur && c.id === cur.id ? "active" : ""}" data-id="${c.id}" title="${esc(c.title)}"><span>${esc(c.title)}</span><i class="del" data-del="${c.id}" title="Удалить">${icon("trash", "sm")}</i></button>`).join("");
}
$("#history").addEventListener("click", (e) => {
  const d = e.target.closest("[data-del]");
  if (d) {
    e.stopPropagation();
    if (streaming) return;
    chats = chats.filter((c) => c.id !== d.dataset.del); saveChats();
    if (cur && cur.id === d.dataset.del) newChat(); else renderHistory();
    return;
  }
  const it = e.target.closest(".item"); if (it) openChat(it.dataset.id);
});
$("#newChat").onclick = () => { setView("chat"); newChat(); $("#chatInput").focus(); };

const SUGGEST = [
  ["🎨 Нарисуй", "Нарисуй кота-космонавта на фоне планет"],
  ["💻 Напиши код", "Функция на Python для проверки пароля на надёжность"],
  ["💡 Объясни просто", "Как работает интернет, как будто мне 10 лет"],
  ["✍️ Придумай идеи", "10 названий для моего канала про технологии"],
];

function msgHTML(m, i) {
  if (m.role === "user") return `<div class="msg user" data-i="${i}"><div class="body"></div></div>`;
  return `<div class="msg ai" data-i="${i}"><div class="av"><img src="/static/logo.svg" alt=""></div><div><div class="who">Codwe AI</div><div class="body"></div><div class="tools"><button data-act="copy">${icon("copy")}Копировать</button><button data-act="regen">${icon("refresh")}Заново</button><span class="stats"></span></div></div></div>`;
}
function fillBody(el, m, live) {
  const body = $(".body", el);
  if (m.role === "user") { body.textContent = m.content; return; }
  if (m.kind === "image") {
    const run = m.status === "queued" || m.status === "running";
    let h = '<div class="chatgen">';
    const imgTitle = m.modelUsed ? ((S.models && S.models.image[m.modelUsed] && S.models.image[m.modelUsed].title) || m.modelUsed) : "выбирает модель…";
    if (settings.duo !== false) h += `<div class="duo"><span>💬 Текст: <b>${esc(cur.model || "чат")}</b></span><i>→</i><span>🎨 Картинка: <b>${esc(imgTitle)}</b></span></div>`;
    if (run) h += `<div class="cg-head">🎨 Рисую по запросу: <b>${esc(m.prompt)}</b></div><div class="bar"><i style="width:${Math.round((m.progress || 0) * 100)}%"></i></div><div class="job-stage"><span>${esc(m.stage || "В очереди…")}</span><span>${Math.round((m.progress || 0) * 100)}%</span></div>`;
    if (m.notes) h += `<div class="cg-note">✨ ${esc(m.notes)}</div>`;
    if (m.images && m.images.length) h += `<div class="chat-media">${m.images.map((r) => mediaHTML(r)).join("")}</div>`;
    if (m.status === "error") h += m.blocked ? blockCard(m.blockedInfo, m.error) : `<div class="err-note">${esc(m.error || "Ошибка")}</div>`;
    body.innerHTML = h + "</div>";
    return;
  }
  const think = m.thinking ? `<details class="thinking" ${live && !m.content ? "open" : ""}><summary>${live && !m.content ? "Думаю…" : "Размышления"}</summary><div>${esc(m.thinking)}</div></details>` : "";
  if (m.error) body.innerHTML = think + (m.content ? md(m.content) : "") + `<div class="err-note">${esc(m.error)}</div>`;
  else body.innerHTML = think + (m.content ? md(m.content) : "");
  body.classList.toggle("cursor", !!live);
  if (m.stats) $(".stats", el).textContent = m.stats;
}
function renderChat() {
  const col = $("#chatCol");
  if (!cur.messages.length) {
    col.innerHTML = `<div class="welcome"><img class="hero-logo" src="/static/logo-full.svg" alt="Codwe AI"><h2>Чем могу <em>помочь</em>?</h2><p>Работаю прямо на вашем компьютере — бесплатно и приватно. Пишу код, тексты и отвечаю на вопросы.</p><div class="sugg">${SUGGEST.map((s, i) => `<button data-sg="${i}"><b>${s[0]}</b><span>${s[1]}</span></button>`).join("")}</div></div>`;
    if (window.codweAurora) window.codweAurora.start();
    return;
  }
  if (window.codweAurora) window.codweAurora.stop();
  col.innerHTML = cur.messages.map(msgHTML).join("");
  $$(".msg", col).forEach((el) => fillBody(el, cur.messages[+el.dataset.i], false));
  scrollChat(true);
}
$("#chatCol").addEventListener("click", (e) => {
  const sg = e.target.closest("[data-sg]");
  if (sg) { $("#chatInput").value = SUGGEST[+sg.dataset.sg][1]; autosize(); updateSend(); $("#chatInput").focus(); return; }
  const cp = e.target.closest("[data-copy]");
  if (cp) { copyText($("code", cp.closest(".code")).textContent); return; }
  const act = e.target.closest("[data-act]");
  if (!act) return;
  const idx = +act.closest(".msg").dataset.i;
  if (act.dataset.act === "copy") copyText(cur.messages[idx].content);
  if (act.dataset.act === "regen" && !streaming) regenerate(idx);
});

function scrollChat(force) {
  const s = $("#chatScroll");
  if (force || s.scrollHeight - s.scrollTop - s.clientHeight < 140) s.scrollTop = s.scrollHeight;
}
function autosize() { const t = $("#chatInput"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 220) + "px"; }
function updateSend() {
  const b = $("#sendBtn");
  if (streaming) { b.disabled = false; b.className = "send stop"; b.innerHTML = icon("stop"); b.setAttribute("aria-label", "Остановить"); }
  else { b.className = "send"; b.innerHTML = icon("send"); b.setAttribute("aria-label", "Отправить"); b.disabled = !S.on || !$("#chatInput").value.trim(); }
  $("#chatInput").disabled = !S.on && !$("#chatInput").value;
}
$("#chatInput").addEventListener("input", () => { autosize(); updateSend(); });
$("#chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!streaming && !$("#sendBtn").disabled) sendMessage(); }
});
$("#sendBtn").onclick = () => { if (streaming) streaming.abort.abort(); else sendMessage(); };

function chooseModel() {
  const list = (S.models && S.models.chat) || [];
  const want = cur.model || draft.model;
  if (want && list.includes(want)) return want;
  const def = S.models && S.models.default_chat;
  if (def && list.includes(def)) return def;
  return list[0] || "";
}

async function sendMessage() {
  const ta = $("#chatInput");
  const text = ta.value.trim();
  if (!text || streaming) return;
  await ensureModels();
  const model = chooseModel();
  if (!model) { toast("Не найдено ни одной модели Ollama. Установите: ollama pull qwen3.5:9b", "error", 8000); return; }
  cur.model = model;
  ta.value = ""; autosize();
  cur.messages.push({ role: "user", content: text });
  if (cur.title === "Новый чат") cur.title = text.replace(/\s+/g, " ").slice(0, 48);
  if (!chats.includes(cur)) chats.unshift(cur);
  cur.updated = Date.now();
  const intent = detectIntent(text);
  if (intent) { await runIntent(intent); return; }
  await streamReply();
}

async function regenerate(idx) {
  const old = cur.messages[idx];
  if (old && old.kind === "image") { cur.messages = cur.messages.slice(0, idx); return genInChat(old.prompt, {}); }
  cur.messages = cur.messages.slice(0, idx);
  if (!cur.messages.length || cur.messages[cur.messages.length - 1].role !== "user") return;
  await streamReply();
}

async function streamReply(opts = {}) {
  const chat = cur;
  const model = cur.model || chooseModel();
  const asst = { role: "assistant", content: "" };
  cur.messages.push(asst);
  renderChat(); renderHistory();
  const el = $(`.msg[data-i="${cur.messages.length - 1}"]`);
  fillBody(el, asst, true);
  const ctrl = new AbortController();
  streaming = { abort: ctrl };
  updateSend();
  let raf = 0;
  const paint = () => { raf = 0; fillBody(el, asst, true); scrollChat(false); };
  const t0 = performance.now();
  try {
    const history = cur.messages.slice(0, -1).filter((m) => m.content && m.content.trim()).map(({ role, content }) => ({ role, content }));
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Codwe": "1" }, signal: ctrl.signal,
      body: JSON.stringify({ model, system: chatSystem(chat) + (opts.system || ""), messages: history, temperature: 0.7, effort: opts.effort ?? effortPct, max_tokens: opts.maxTokens || undefined }),
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : msg; } catch { /* */ }
      throw new Error(msg);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        const d = JSON.parse(line);
        if (d.error) throw new Error(d.error);
        if (d.th) { asst.thinking = (asst.thinking || "") + d.th; if (!raf) raf = requestAnimationFrame(paint); }
        if (d.t) { asst.content += d.t; if (!raf) raf = requestAnimationFrame(paint); }
        if (d.done && d.tokens && d.ns) asst.stats = `${(d.tokens / (d.ns / 1e9)).toFixed(1)} ток/с`;
      }
    }
  } catch (e) {
    if (e.name === "AbortError") { asst.content = asst.content || ""; if (!asst.content) asst.error = "Остановлено."; }
    else asst.error = e.message || "Ошибка";
  } finally {
    if (raf) cancelAnimationFrame(raf);
    streaming = null;
    if (!asst.content && !asst.error) asst.error = "Пустой ответ.";
    if (!asst.stats && asst.content) asst.stats = ((performance.now() - t0) / 1000).toFixed(1) + " с";
    fillBody(el, asst, false);
    chat.updated = Date.now();
    saveChats(); renderHistory(); updateSend(); scrollChat(false);
    if (asst.content && !asst.error) { autoTitle(chat); if (typeof afterAssistant === "function" && !opts.intro) afterAssistant(chat, asst); }
  }
  return asst;
}

// ───────────── картинки прямо в чате ─────────────
const NOT_IMG = /(python|питон|javascript|js\b|html|css|код|функци|скрипт|matplotlib|canvas|график|диаграмм|таблиц|sql|регуляр|алгоритм|программ)/i;
const IMG_STRONG = /^(?:\/(?:img|image|pic|картинка|рисуй)\s+|(?:пожалуйста[,\s]+)?(?:нарисуй|нарисовать|изобрази|сгенерируй|сгенерировать)\s+(?:мне\s+)?(?:(?:картинку|изображение|фото|фотографию|рисунок|иллюстрацию|арт)\s+)?(?:с\s+|про\s+|на тему\s+|где\s+)?|(?:please\s+)?(?:draw|paint|generate)\s+(?:me\s+)?(?:an?\s+)?(?:(?:image|picture|photo|illustration)\s+(?:of\s+|with\s+)?)?)(.+)$/i;
const IMG_NOUN = /^(?:пожалуйста[,\s]+)?(?:сделай|создай|покажи|хочу|нужна|нужно|дай)\s+(?:мне\s+)?(?:картинку|изображение|фото|фотографию|рисунок|иллюстрацию|арт)\s*(?:с\s+|про\s+|на тему\s+|где\s+|—|-|:)?\s*(.+)$/i;
const EDIT_VERB = /^(?:а\s+)?(?:теперь\s+|ещё\s+|еще\s+)?(?:сделай|добавь|убери|поменяй|измени|перекрась|перерисуй|замени|пусть|поставь|надень|удали|сделать)(?![а-яё])/i;
const AGAIN = /^(?:ещё|еще|ещё раз|еще раз|другой вариант|ещё вариант|еще вариант|переделай|повтори)\s*[.!]?$/i;

function lastImageMsg() {
  for (let i = cur.messages.length - 1; i >= 0; i--) { const m = cur.messages[i]; if (m.kind === "image" && m.images && m.images.length) return m; }
  return null;
}
function detectIntent(text) {
  const t = text.trim();
  const last = cur.messages.length >= 2 ? cur.messages[cur.messages.length - 2] : null;  // сообщение перед текущим
  const afterImage = last && last.role === "assistant" && last.kind === "image" && last.images && last.images.length;
  let m;
  if ((m = t.match(IMG_STRONG)) || (m = t.match(IMG_NOUN))) {
    if (t.startsWith("/") || !NOT_IMG.test(t)) return { type: "image", prompt: m[1].trim() };
  }
  if (afterImage && AGAIN.test(t)) return { type: "again", prompt: last.prompt };
  if (afterImage && EDIT_VERB.test(t)) return { type: "edit", request: t, file: last.images[0].name };
  return null;
}
function guessAspect(p) {
  const t = p.toLowerCase();
  if (/(пейзаж|город|закат|рассвет|гор[ыа](?![а-яё])|море|океан|космос|вид на|панорам|landscape|скайлайн|лес(?![а-яё]))/.test(t)) return "landscape";
  if (/(девушк|парен|мужчин|женщин|человек|портрет|персонаж|девочк|мальчик|аниме|girl|boy|man\b|woman|portrait|character)/.test(t)) return "portrait";
  return "square";
}
// «Две модели»: текстовая модель сразу отвечает словами (что задумала), а потом картиночная модель рисует.
// На видеокарте с 8 ГБ обе сразу не помещаются, поэтому они работают по очереди: слова занимают около 3 секунд.
async function duoIntro(it) {
  if (settings.duo === false) return;
  const chat = cur, idx = chat.messages.length;
  const what = it.type === "edit" ? `изменить текущую картинку: «${it.request}»` : `нарисовать: «${it.prompt}»`;
  const sys = `\n\n[Режим «две модели»: ты текстовая модель. Пользователь просит ${what}. Картинку нарисует отдельная модель для изображений, не ты. Ответь по-русски коротко (1–2 предложения): что именно ты задумал (идея, настроение, пара деталей) и что картинка скоро появится. Не пиши код, ссылки и промпт и не говори, что картинка уже готова.]`;
  try { await streamReply({ system: sys, effort: 0, maxTokens: 90, intro: true }); } catch { /* вступление необязательно */ }
  const msg = chat.messages[idx];
  if (msg && (msg.error || !msg.content)) { chat.messages.splice(idx, 1); if (cur === chat) renderChat(); }
  else if (msg) msg.duo = true;
}
async function runIntent(it) {
  await duoIntro(it);
  if (it.type === "edit") return genInChat(it.request, { edit: it.file });
  return genInChat(it.prompt, {});
}
async function genInChat(prompt, opts) {
  const chat = cur;
  const m = { role: "assistant", kind: "image", content: "", prompt, status: "queued", progress: 0, stage: "В очереди…", images: [], notes: "" };
  chat.messages.push(m);
  if (cur === chat) { renderChat(); scrollChat(true); }
  saveChats(); renderHistory();
  const repaint = () => { if (cur !== chat) return; const idx = chat.messages.indexOf(m); const el = $(`.msg[data-i="${idx}"]`); if (el) { fillBody(el, m, false); scrollChat(false); } };
  try {
    let id;
    if (opts.edit) ({ id } = await api("/api/edit", { method: "POST", body: { file: opts.edit, request: prompt, mode: "auto", strength: null, model: "auto", count: 1, seed: null, plan: null, effort: effortPct } }));
    else ({ id } = await api("/api/image", { method: "POST", body: { prompt, negative: "", model: "auto", aspect: guessAspect(prompt), count: 1, seed: null, translate: true, enhance: true, styles: [], effort: effortPct, camera: cam.on ? cam.kind : null, cam_strength: +$("#camStr").value / 100 } }));
    for (;;) {
      const j = await api("/api/jobs/" + id);
      m.status = j.status === "error" || j.status === "cancelled" ? "error" : j.status; m.progress = j.progress; m.stage = j.stage;
      m.notes = j.notes || ""; m.images = j.results || []; if (j.model_used) m.modelUsed = j.model_used;
      if (j.status === "error") { m.error = j.error; m.blocked = !!j.blocked; m.blockedInfo = j.blocked_info || null; }
      if (j.status === "cancelled") m.error = "Отменено.";
      repaint();
      if (["done", "error", "cancelled"].includes(j.status)) { if (j.status === "done") m.content = `[Я нарисовал картинку по запросу: ${j.prompt_en || prompt}]`; break; }
      await sleep(700);
    }
  } catch (e) {
    m.status = "error"; m.error = e.message; m.blocked = String(e.message).startsWith("🛡");
    repaint();
  }
  saveChats(); renderHistory(); updateSend(); loadGallery(true);
  if (m.status === "done") autoTitle(chat);
}

// ───────────── автоназвание чатов ─────────────
async function autoTitle(chat) {
  if (chat.titled) return;
  const u = chat.messages.find((m) => m.role === "user");
  const a = chat.messages.find((m) => m.role === "assistant");
  if (!u) return;
  chat.titled = true;
  try {
    const r = await api("/api/title", { method: "POST", body: { user: u.content, assistant: a && a.kind === "image" ? "[Я нарисовал картинку]" : (a ? a.content : "") } });
    if (r.title) typeTitle(chat, r.title);
  } catch { /* останется короткое название из первой фразы */ }
}
function typeTitle(chat, title) {
  let i = 0;
  const step = () => {
    const el = $(`.hist .item[data-id="${chat.id}"]`);
    chat.title = title.slice(0, i || 1);
    if (el) { $("span", el).textContent = chat.title; el.classList.add("typing"); }
    if (++i <= title.length) setTimeout(step, 30);
    else { chat.title = title; saveChats(); renderHistory(); }
  };
  step();
}

// ───────────── кнопка «+» в поле чата ─────────────
$("#plusBtn").onclick = (e) => { e.stopPropagation(); $$(".menu").forEach((m) => { if (m.id !== "plusMenu") m.hidden = true; }); $("#plusMenu").hidden = !$("#plusMenu").hidden; };
$("#plusMenu").onclick = (e) => {
  const b = e.target.closest("[data-act]"); if (!b) return;
  $("#plusMenu").hidden = true;
  if (b.dataset.act === "img") { const t = $("#chatInput"); t.value = "Нарисуй "; autosize(); updateSend(); t.focus(); }
  else setView({ edit: "edit", live: "live", video: "video" }[b.dataset.act]);
};

// выбор роли и модели
function renderChips() {
  const p = allPersonas().find((x) => x.id === cur.personaId);
  $("#personaName").textContent = p ? p.name : "Свой промпт";
  $("#modelName").textContent = (S.models ? chooseModel() : (cur.model || draft.model)) || "модель";
}
$("#personaBtn").onclick = (e) => {
  e.stopPropagation();
  const m = $("#personaMenu");
  if (!m.hidden) { m.hidden = true; return; }
  $("#modelMenu").hidden = true;
  const customs = customPersonas();
  m.innerHTML = allPersonas().map((p) => `<button data-p="${p.id}" class="${cur.personaId === p.id ? "active" : ""}"><b>${esc(p.name)}</b><small>${esc(p.prompt.slice(0, 70))}…</small></button>`).join("") +
    `<hr><button data-p="__edit"><b>Свой системный промпт…</b><small>Задайте любое поведение, например «общайся матом»</small></button>`;
  m.hidden = false;
  m._custom = customs;
};
$("#personaMenu").onclick = (e) => {
  const b = e.target.closest("[data-p]"); if (!b) return;
  $("#personaMenu").hidden = true;
  if (b.dataset.p === "__edit") return editSystemPrompt();
  const p = allPersonas().find((x) => x.id === b.dataset.p);
  if (!p) return;
  cur.personaId = p.id; cur.system = p.prompt;
  draft.personaId = p.id; draft.system = p.prompt; saveDraft(); saveChats(); renderChips();
  toast("Роль: " + p.name, "ok", 1800);
};
function editSystemPrompt() {
  const c = openModal(`<h3>Системный промпт</h3><p>Это инструкция, как ИИ должен себя вести в этом чате. Например: «Общайся матом», «Отвечай как пират», «Ты — строгий ревьюер кода».</p>
    <textarea id="sysText" rows="7" maxlength="20000"></textarea>
    <input type="text" id="sysName" placeholder="Название роли (чтобы сохранить на будущее)" maxlength="40">
    <div class="modal-actions"><button class="btn" id="sysSave">Сохранить как роль</button><button class="btn" id="sysCancel">Отмена</button><button class="btn primary" id="sysApply">Применить к чату</button></div>`);
  $("#sysText", c).value = cur.system || "";
  $("#sysText", c).focus();
  $("#sysCancel", c).onclick = closeModal;
  const apply = (id) => { cur.system = $("#sysText", c).value.trim(); cur.personaId = id; draft.system = cur.system; draft.personaId = id; saveDraft(); saveChats(); renderChips(); closeModal(); };
  $("#sysApply", c).onclick = () => apply("custom");
  $("#sysSave", c).onclick = () => {
    const name = $("#sysName", c).value.trim(), prompt = $("#sysText", c).value.trim();
    if (!name || !prompt) return toast("Введите название и текст промпта.", "error");
    const list = customPersonas(); const p = { id: "c" + uid(), name, prompt };
    list.push(p); store.set("codwe.personas", list);
    apply(p.id); toast("Роль сохранена", "ok");
  };
}
$("#modelBtn").onclick = async (e) => {
  e.stopPropagation();
  const m = $("#modelMenu");
  if (!m.hidden) { m.hidden = true; return; }
  $("#personaMenu").hidden = true;
  await ensureModels(true);
  const list = S.models.chat;
  m.innerHTML = list.length ? list.map((n) => `<button data-m="${esc(n)}" class="${chooseModel() === n ? "active" : ""}"><b>${esc(n)}</b></button>`).join("")
    : '<button disabled><b>Нет моделей</b><small>В терминале: ollama pull qwen3.5:9b</small></button>';
  m.hidden = false;
};
$("#modelMenu").onclick = (e) => {
  const b = e.target.closest("[data-m]"); if (!b) return;
  cur.model = b.dataset.m; draft.model = b.dataset.m; saveDraft(); saveChats(); renderChips();
  $("#modelMenu").hidden = true;
};

// ───────────── модели ─────────────
let modelsPromise = null;
function ensureModels(force) {
  if (force || !modelsPromise) {
    modelsPromise = api("/api/models").then((m) => { S.models = m; renderChips(); initImages(); initVideoModels(); if (typeof renderEffort === "function") renderEffort(); return m; })
      .catch((e) => { modelsPromise = null; toast("Не удалось получить список моделей: " + e.message, "error"); });
  }
  return modelsPromise;
}

// ───────────── задачи генерации ─────────────
const jobCards = new Map();
function jobCard(kind, id, title, sub) {
  if (jobCards.has(id)) return jobCards.get(id);
  const ids = { image: ["#imgResults", "#imgEmpty"], video: ["#vidResults", "#vidEmpty"], edit: ["#editResults", "#editEmpty"] }[kind];
  const host = $(ids[0]);
  $(ids[1]).hidden = true;
  const el = document.createElement("div");
  el.className = "job";
  el.innerHTML = `<div class="job-head"><div><div class="job-title"></div><div class="job-sub"></div></div><button class="btn small" data-cancel>Отмена</button></div>
    <div class="bar"><i></i></div><div class="job-stage"><span class="st">В очереди…</span><span class="pc">0%</span></div><div class="job-body"></div>`;
  $(".job-title", el).textContent = title;
  $(".job-sub", el).textContent = sub || "";
  host.prepend(el);
  const card = { id, kind, el, done: false };
  $("[data-cancel]", el).onclick = () => api(`/api/jobs/${id}/cancel`, { method: "POST" }).catch((e) => toast(e.message, "error"));
  jobCards.set(id, card);
  return card;
}
function rehydrateJob(j) {
  if (jobCards.has(j.id)) return;
  const card = jobCard(j.kind, j.id, { image: "Генерация картинки", video: "Генерация видео", edit: "Редактирование картинки" }[j.kind] || "Задача", "");
  trackJob(card);
}
async function trackJob(card) {
  if (card.tracking) return;
  card.tracking = true;
  for (;;) {
    let j;
    try { j = await api("/api/jobs/" + card.id); } catch (e) { if (e.status === 404) { card.el.remove(); jobCards.delete(card.id); return; } await sleep(1500); continue; }
    paintJob(card, j);
    if (["done", "error", "cancelled"].includes(j.status)) break;
    await sleep(700);
  }
  card.done = true;
  loadGallery(true);
}
function paintJob(card, j) {
  const el = card.el;
  el.classList.toggle("done", j.status === "done");
  el.classList.toggle("error", j.status === "error");
  $(".bar i", el).style.width = Math.round((j.progress || 0) * 100) + "%";
  $(".st", el).textContent = j.error ? "" : j.stage || "";
  $(".pc", el).textContent = j.status === "running" || j.status === "queued" ? Math.round((j.progress || 0) * 100) + "%" : "";
  const finished = ["done", "error", "cancelled"].includes(j.status);
  $("[data-cancel]", el).hidden = finished;
  const body = $(".job-body", el);
  let html = "";
  if (j.notes) html += `<div class="job-notes">✨ ${esc(j.notes)}</div>`;
  if (j.effort) html += `<div class="job-model">Effort: ${esc(j.effort)}${j.camera ? " · камера: " + esc((S.models && S.models.cameras && S.models.cameras[j.camera]) || j.camera) : ""}</div>`;
  if (j.model_used && S.models && S.models.image[j.model_used]) html += `<div class="job-model">Модель: ${esc(S.models.image[j.model_used].title)}${j.motion_used ? " · движение " + j.motion_used : ""}</div>`;
  if (j.method) html += `<div class="job-model">Способ: ${j.method === "instruct" ? "точечное изменение по инструкции" : "перерисовка сильной моделью"}</div>`;
  if (j.description) html += `<div class="job-model">ИИ увидел: ${esc(j.description)}${j.motion_used ? " · движение " + j.motion_used : ""}</div>`;
  if (j.prompt_en) html += `<div class="en-prompt" title="Так модель поняла ваше описание">EN: ${esc(j.prompt_en)}</div>`;
  if (j.status === "error") html += j.blocked ? blockCard(j.blocked_info, j.error) : `<div class="err">${esc(j.error || "Ошибка")}</div>`;
  if (j.status === "cancelled") html += `<div class="job-sub">Отменено.</div>`;
  if (j.results && j.results.length) {
    const orig = j.source ? `<div class="media orig"><img src="${j.source}" alt=""><span class="label">Было</span></div>` : "";
    html += '<div class="media-grid">' + orig + j.results.map((r) => mediaHTML(r, !!j.source)).join("") + "</div>";
  }
  if (body.dataset.html !== html) { body.innerHTML = html; body.dataset.html = html; }
}
function mediaHTML(r, edited) {
  if (r.kind === "video") return `<div class="media" data-name="${esc(r.name)}"><video src="${r.url}" poster="${r.poster || ""}" controls loop playsinline preload="metadata"></video><div class="act"><a href="${r.url}" download="${esc(r.name)}" title="Скачать">${icon("down")}</a></div></div>`;
  return `<div class="media" data-name="${esc(r.name)}"><img src="${r.url}" alt="" data-view="${esc(r.name)}"><div class="act"><button data-edit="${esc(r.name)}" title="Изменить дальше">${icon("edit")}</button><button data-anim="${esc(r.name)}" title="Оживить (видео)">${icon("film")}</button><a href="${r.url}" download="${esc(r.name)}" title="Скачать">${icon("down")}</a></div><span class="label">${edited ? "Стало · " : ""}seed ${r.seed ?? ""}</span></div>`;
}
document.addEventListener("click", (e) => {
  const ed = e.target.closest("[data-edit]");
  if (ed) { closeModal(); setView("edit"); setEditFile(ed.dataset.edit, "/outputs/" + ed.dataset.edit); return; }
  const an = e.target.closest("[data-anim]");
  if (an) { closeModal(); setView("video"); vid.setFile(an.dataset.anim, "/outputs/" + an.dataset.anim); return; }
  const v = e.target.closest("img[data-view]");
  if (v) openViewer(v.dataset.view);
});

// ───────────── КАРТИНКИ ─────────────
const STYLES = [
  ["Кино", "cinematic lighting, film still"], ["Портрет", "studio portrait, soft light, shallow depth of field"],
  ["Неон", "neon cyberpunk, glowing lights, night city"], ["Акварель", "watercolor painting, soft washes"],
  ["Масло", "oil painting, visible brush strokes"], ["3D", "3d render, soft studio lighting"],
  ["Пиксель-арт", "pixel art, 16-bit"], ["Фэнтези", "epic fantasy art, dramatic lighting"],
  ["Ретро", "vintage film photo, 35mm grain"],
];
const IDEAS = [
  "Привет", "Космонавт верхом на лошади на Луне", "Уютная кофейня в дождливый вечер, вид с улицы",
  "Дракон спит на горе золота", "Аниме девочка с зонтом под дождём в неоновом городе", "Старый маяк во время шторма",
  "Два котёнка в корзине с цветами", "Самурай на мосту в тумане", "Футуристический город на закате",
  "Портрет старого рыбака с доброй улыбкой", "Аниме эльфийка в волшебном лесу", "Робот поливает цветы на балконе",
  "Лиса в осеннем лесу", "Девушка читает книгу у окна, утренний свет", "Маленький домик на вершине снежной горы",
];
const pick = (a) => a[Math.floor(Math.random() * a.length)];
const img = { model: "auto", aspect: "portrait", resolved: null };

function chosenStyles() { return $$("#imgStyles .sty.on").map((b) => b.dataset.e); }
function resetPeek(kind) {
  const p = kind === "vid" ? "vid" : "img";
  $(`#${p}PeekBox`).hidden = true;
  if (p === "img") img.resolved = null; else vid.resolved = null;
}
$("#imgStyles").innerHTML = STYLES.map(([n, e]) => `<button type="button" class="sty" data-e="${esc(e)}">${n}</button>`).join("");
$("#imgStyles").onclick = (e) => { const b = e.target.closest(".sty"); if (!b) return; b.classList.toggle("on"); resetPeek("img"); };
$("#imgPrompt").addEventListener("input", () => resetPeek("img"));
$("#imgIdea").onclick = () => { $("#imgPrompt").value = pick(IDEAS); resetPeek("img"); };

let imagesInited = false;
function modelCard(k, m) {
  return `<button type="button" class="card-opt" data-k="${k}"><b>${esc(m.title)}</b><span>${esc(m.desc)}</span>${m.ready ? "" : '<i class="tag">скачается при первом запуске</i>'}</button>`;
}
function initImages() {
  if (!S.models || imagesInited) return;
  imagesInited = true;
  const M = S.models.image;
  $("#imgModels").innerHTML = '<button type="button" class="card-opt auto" data-k="auto"><b>Авто (рекомендую)</b><span>ИИ сам решит: аниме или реализм — и подберёт модель и настройки.</span></button>' +
    ["real", "anime", "turbo"].filter((k) => M[k]).map((k) => modelCard(k, M[k])).join("");
  const saved = store.get("codwe.imgModel2", "auto");
  selectImgModel(saved === "auto" || M[saved] ? saved : "auto");
}
$("#imgModels").onclick = (e) => { const b = e.target.closest("[data-k]"); if (b) selectImgModel(b.dataset.k); };
function selectImgModel(k) {
  const M = S.models.image, m = M[k] || M.real || { steps: 30, steps_max: 50, cfg: 5.5 };
  img.model = k; store.set("codwe.imgModel2", k);
  $$("#imgModels .card-opt").forEach((b) => b.classList.toggle("active", b.dataset.k === k));
  const steps = $("#imgSteps"); steps.max = m.steps_max; steps.value = m.steps; $("#imgStepsV").textContent = m.steps;
  $("#imgCfg").value = m.cfg || 6; $("#imgCfgV").textContent = $("#imgCfg").value;
  resetPeek("img");
}
$("#imgAspect").onclick = (e) => { const b = e.target.closest("[data-a]"); if (!b) return; img.aspect = b.dataset.a; $$("#imgAspect .ratio").forEach((x) => x.classList.toggle("active", x === b)); };
$("#imgCustom").onchange = (e) => { $("#imgSteps").disabled = $("#imgCfg").disabled = !e.target.checked; };
[["imgSteps", "imgStepsV"], ["imgCfg", "imgCfgV"], ["imgCount", "imgCountV"]].forEach(([a, b]) => $("#" + a).addEventListener("input", () => ($("#" + b).textContent = $("#" + a).value)));

// ───────────── «Фото с камеры» ─────────────
const cam = { on: false, kind: "phone" };
$("#camOn").onchange = (e) => { cam.on = e.target.checked; $("#camBox").hidden = !cam.on; resetPeek("img"); };
$("#camKind").onclick = (e) => { const b = e.target.closest("[data-c]"); if (!b) return; cam.kind = b.dataset.c; setSeg("#camKind", "c", cam.kind); resetPeek("img"); };
$("#camStr").addEventListener("input", () => { $("#camStrV").textContent = $("#camStr").value; });

async function peek(kind) {
  const isImg = kind === "img";
  const text = $(isImg ? "#imgPrompt" : "#vidPrompt").value.trim();
  if (!text) return toast("Сначала напишите, что нужно.", "error");
  if (!S.on) return toast("ИИ выключен — включите его переключателем.", "error");
  const btn = $(isImg ? "#imgPeek" : "#vidPeek");
  btn.disabled = true; btn.textContent = "ИИ думает…";
  try {
    const r = await api("/api/enhance", { method: "POST", body: { text, mode: isImg ? "image" : "video", model: isImg ? img.model : vid.imodel, styles: isImg ? chosenStyles() : [], camera: isImg && cam.on ? cam.kind : null } });
    const p = isImg ? "img" : "vid";
    $(`#${p}Notes`).textContent = "✨ " + (r.notes || "");
    $(`#${p}Final`).value = r.prompt;
    $(`#${p}FinalNeg`).value = r.negative;
    $(`#${p}PeekBox`).hidden = false;
    if (isImg) { img.resolved = r.model; $("#imgPeekModel").textContent = "Будет использована модель: " + r.model_title; }
    else { vid.resolved = r.model; vid.peekMotion = r.motion; $("#vidNotes").textContent += `  Сила движения: ${r.motion}.`; }
  } catch (e) { toast(e.message, "error"); }
  btn.disabled = false; btn.textContent = "Показать, что придумал ИИ";
}
$("#imgPeek").onclick = () => peek("img");
$("#vidPeek").onclick = () => peek("vid");

$("#imgForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const prompt = $("#imgPrompt").value.trim();
  if (!prompt) { toast("Напишите, что нарисовать (хоть одно слово).", "error"); $("#imgPrompt").focus(); return; }
  const custom = $("#imgCustom").checked, seed = $("#imgSeed").value.trim();
  const body = { prompt, negative: "", model: img.model, aspect: img.aspect, count: +$("#imgCount").value, seed: seed === "" ? null : +seed,
    steps: custom ? +$("#imgSteps").value : null, cfg: custom ? +$("#imgCfg").value : null, enhance: $("#imgEnhance").checked, translate: true, styles: chosenStyles(),
    effort: effortPct, camera: cam.on ? cam.kind : null, cam_strength: +$("#camStr").value / 100 };
  if (!$("#imgPeekBox").hidden && $("#imgFinal").value.trim()) {
    Object.assign(body, { prompt: $("#imgFinal").value.trim(), negative: $("#imgFinalNeg").value.trim(), model: img.resolved || img.model, enhance: false, translate: false, styles: [] });
  }
  $("#imgGo").disabled = true;
  try {
    const { id } = await api("/api/image", { method: "POST", body });
    const mt = body.model === "auto" ? "Авто" : (S.models.image[body.model] || {}).title || body.model;
    trackJob(jobCard("image", id, prompt, `${mt} · ${body.aspect === "portrait" ? "портрет" : body.aspect === "landscape" ? "широкий" : "квадрат"}`));
  } catch (err) { toast(err.message, "error"); }
  $("#imgGo").disabled = !S.on;
});

// ───────────── ВИДЕО ─────────────
const vid = {
  source: "file", file: "", format: "auto", imodel: "auto", resolved: null, peekMotion: null,
  ready() { return this.source === "text" ? true : !!this.file; },
  setFile(name, url) {
    this.file = name; this.source = "file"; setSeg("#vidSource", "src", "file"); showSource();
    $("#vidPreview").src = url; $("#vidPreview").hidden = false; $("#vidDropHint").hidden = true;
    $("#vidLookBox").hidden = true; applyGating();
  },
};
const FORMAT_RU = { landscape: "широкий 16:9", portrait: "вертикальный", square: "квадрат" };
$("#vidWish").addEventListener("input", () => { $("#vidLookBox").hidden = true; });
$("#vidLook").onclick = async () => {
  if (!vid.file) return toast("Сначала выберите картинку.", "error");
  if (!S.on) return toast("ИИ выключен — включите его переключателем.", "error");
  const btn = $("#vidLook");
  btn.disabled = true; btn.textContent = "ИИ смотрит на картинку…";
  try {
    const r = await api("/api/analyze", { method: "POST", body: { file: vid.file, wish: $("#vidWish").value } });
    $("#vidLookNotes").textContent = "✨ " + r.notes + (r.risks ? " " + r.risks : "");
    $("#vidLookDesc").textContent = r.description ? "ИИ увидел: " + r.description : "";
    $("#vidLookRec").textContent = `Рекомендация: формат — ${FORMAT_RU[r.format]}, сила движения — ${r.motion}` + (r.vision_model ? "" : " (модель-«глаза» не установлена)");
    $("#vidLookBox").hidden = false;
  } catch (e) { toast(e.message, "error"); }
  btn.disabled = false; btn.textContent = "Показать, что увидел ИИ";
};
function setSeg(sel, key, val) { $$(sel + " button").forEach((b) => b.classList.toggle("active", b.dataset[key] === val)); }
function showSource() { $("#vidFileBox").hidden = vid.source !== "file"; $("#vidTextBox").hidden = vid.source !== "text"; applyGating(); }
function initVideoModels() { /* формат и модель выбираются кнопками */ }
$("#vidSource").onclick = (e) => { const b = e.target.closest("[data-src]"); if (!b) return; vid.source = b.dataset.src; setSeg("#vidSource", "src", vid.source); showSource(); };
$("#vidFormat").onclick = (e) => { const b = e.target.closest("[data-f]"); if (!b) return; vid.format = b.dataset.f; setSeg("#vidFormat", "f", vid.format); };
$("#vidImgModel").onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; vid.imodel = b.dataset.m; setSeg("#vidImgModel", "m", vid.imodel); resetPeek("vid"); };
$("#vidPrompt").addEventListener("input", () => resetPeek("vid"));
$("#vidIdea").onclick = () => { $("#vidPrompt").value = pick(IDEAS.filter((x) => x !== "Привет")); resetPeek("vid"); };
$("#vidMotionAuto").onchange = (e) => { $("#vidMotionRow").hidden = e.target.checked; };
[["vidFrames", "vidFramesV"], ["vidSteps", "vidStepsV"], ["vidMotion", "vidMotionV"], ["vidFps", "vidFpsV"]].forEach(([a, b]) => $("#" + a).addEventListener("input", () => ($("#" + b).textContent = $("#" + a).value)));

async function uploadImage(file, cb) {
  if (!file || !file.type.startsWith("image/")) return toast("Выберите файл-картинку.", "error");
  const fd = new FormData(); fd.append("file", file);
  try { const r = await api("/api/upload", { method: "POST", form: fd }); (cb || ((x) => vid.setFile(x.name, x.url)))(r); }
  catch (e) { toast(e.message, "error"); }
}
async function pickImage(cb) {
  await loadGallery(true);
  const imgs = S.gallery.filter((x) => x.kind === "image");
  const c = openModal(`<h3>Выберите картинку</h3>${imgs.length ? `<div class="pick-grid">${imgs.map((x) => `<button class="tile" data-pick="${esc(x.name)}" data-url="${x.url}"><img loading="lazy" src="${x.url}" alt=""></button>`).join("")}</div>` : "<p>В галерее пока нет картинок.</p>"}<div class="modal-actions"><button class="btn" id="pkClose">Закрыть</button></div>`, true);
  $("#pkClose", c).onclick = closeModal;
  c.onclick = (e) => { const t = e.target.closest("[data-pick]"); if (t) { closeModal(); cb(t.dataset.pick, t.dataset.url); } };
}
$("#vidDrop").onclick = () => $("#vidFile").click();
$("#vidFile").onchange = (e) => { uploadImage(e.target.files[0]); e.target.value = ""; };
["dragenter", "dragover"].forEach((t) => $("#vidDrop").addEventListener(t, (e) => { e.preventDefault(); $("#vidDrop").classList.add("over"); }));
["dragleave", "drop"].forEach((t) => $("#vidDrop").addEventListener(t, (e) => { e.preventDefault(); $("#vidDrop").classList.remove("over"); }));
$("#vidDrop").addEventListener("drop", (e) => uploadImage(e.dataTransfer.files[0]));
$("#vidPick").onclick = () => pickImage((name, url) => vid.setFile(name, url));
$("#vidForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const seed = $("#vidSeed").value.trim();
  const auto = $("#vidMotionAuto").checked;
  const body = { source: vid.source, format: vid.format, frames: +$("#vidFrames").value, steps: +$("#vidSteps").value,
    motion: auto ? null : +$("#vidMotion").value, fps: +$("#vidFps").value, seed: seed === "" ? null : +seed,
    translate: true, enhance: $("#vidEnhance").checked, styles: [], image_model: vid.imodel, negative: "" };
  let title;
  if (vid.source === "file") {
    if (!vid.file) return toast("Сначала выберите картинку.", "error");
    body.file = vid.file; title = "Видео из картинки";
    body.enhance = $("#vidDirector").checked; body.wish = $("#vidWish").value.trim();
    if (auto && !body.enhance) body.motion = 70;
  } else {
    body.prompt = $("#vidPrompt").value.trim(); title = body.prompt;
    if (!body.prompt) return toast("Опишите сцену.", "error");
    if (!$("#vidPeekBox").hidden && $("#vidFinal").value.trim()) {
      Object.assign(body, { prompt: $("#vidFinal").value.trim(), negative: $("#vidFinalNeg").value.trim(), image_model: vid.resolved || vid.imodel, enhance: false, translate: false });
      if (auto && vid.peekMotion) body.motion = vid.peekMotion;
    }
  }
  $("#vidGo").disabled = true;
  try { const { id } = await api("/api/video", { method: "POST", body }); trackJob(jobCard("video", id, title, `${body.frames} кадров · ${body.steps} шагов`)); }
  catch (err) { toast(err.message, "error"); }
  applyGating();
});

// ───────────── LIVE ─────────────
const LIVE_STYLES = [["Без стиля", ""], ["Фото", "photorealistic, sharp focus, highly detailed"], ["Аниме", "anime style, vibrant colors, detailed"],
  ["Арт", "digital art, concept art, highly detailed"], ["3D", "3d render, soft studio lighting"], ["Неон", "neon lights, cyberpunk, glowing"]];
const live = { model: "fast", size: 512, steps: 1, seed: Math.floor(Math.random() * 1e6), style: "", ready: false, inflight: false, pending: false, timer: null, url: null, polling: false };
$("#liveStyles").innerHTML = LIVE_STYLES.map(([n, e], i) => `<button type="button" class="sty ${i === 0 ? "on" : ""}" data-e="${esc(e)}">${n}</button>`).join("");
$("#liveStyles").onclick = (e) => { const b = e.target.closest(".sty"); if (!b) return; $$("#liveStyles .sty").forEach((x) => x.classList.toggle("on", x === b)); live.style = b.dataset.e; scheduleLive(0, true); };
$("#liveSize").onclick = (e) => { const b = e.target.closest("[data-v]"); if (!b) return; live.size = +b.dataset.v; setSeg("#liveSize", "v", String(live.size)); scheduleLive(0, true); };
$("#liveSteps").onclick = (e) => { const b = e.target.closest("[data-v]"); if (!b) return; live.steps = +b.dataset.v; setSeg("#liveSteps", "v", String(live.steps)); scheduleLive(0, true); };
$("#liveModel").onclick = (e) => { const b = e.target.closest("[data-v]"); if (!b) return; live.model = b.dataset.v; setSeg("#liveModel", "v", live.model); live.ready = false; ensureLive(); };
$("#liveDice").onclick = () => { live.seed = Math.floor(Math.random() * 1e6); scheduleLive(0, true); };
$("#liveInput").addEventListener("input", () => { if (!live.ready) ensureLive(); scheduleLive(70); });
$("#liveHQ").onclick = () => {
  const t = $("#liveInput").value.trim();
  if (!t) return toast("Сначала напишите, что нарисовать.", "error");
  setView("images"); $("#imgPrompt").value = t; resetPeek("img");
  toast("Идея перенесена — нажмите «Создать картинку» для высокого качества", "ok", 3000);
};
$("#liveSave").onclick = async () => {
  try { const r = await api("/api/live/save", { method: "POST" }); toast("Сохранено в галерею: " + r.name, "ok", 2600); loadGallery(true); }
  catch (e) { toast(e.message, "error"); }
};
function liveLoading(on, text) { $("#liveLoading").hidden = !on; if (text) $("#liveLoadText").textContent = text; }
function scheduleLive(ms, onlyFinal) {
  clearTimeout(live.timer); clearTimeout(live.finalTimer);
  if (!onlyFinal) live.timer = setTimeout(() => runLive(false), ms);
  live.finalTimer = setTimeout(() => runLive(true), onlyFinal ? ms : 380);  // пауза в наборе -> полный перевод
}

async function ensureLive() {
  if (!S.on || live.polling) return;
  if (live.ready) return;
  live.polling = true; liveLoading(true, "Загружаю модель в видеопамять…");
  try {
    let st = await api("/api/live/start", { method: "POST", body: { model: live.model } });
    for (let tries = 0; ; ) {
      if (st.error) throw new Error(st.error);
      if (st.ready && st.model === live.model && !st.loading) break;
      await sleep(600);
      st = await api("/api/live/status");
      if (!st.loading && !(st.ready && st.model === live.model) && !st.error) {
        if (++tries > 3) throw new Error("Не удалось загрузить Live-модель.");
        st = await api("/api/live/start", { method: "POST", body: { model: live.model } });
      }
    }
    live.ready = true;
  } catch (e) { toast(e.message, "error"); }
  live.polling = false; liveLoading(false);
  if (live.ready && $("#liveInput").value.trim()) runLive(true);
}

async function runLive(final = true) {
  const text = $("#liveInput").value.trim();
  if (!text) return;
  if (!S.on) return toast("ИИ выключен — включите его переключателем.", "error");
  if (!live.ready) { ensureLive(); return; }
  if (live.inflight) { live.pending = true; live.pendingFinal = live.pendingFinal || final; return; }
  live.inflight = true;
  const t0 = performance.now();
  try {
    const res = await fetch("/api/live", { method: "POST", headers: { "Content-Type": "application/json", "X-Codwe": "1" },
      body: JSON.stringify({ prompt: text, seed: live.seed, steps: live.steps, size: live.size, model: live.model, style: live.style, final }) });
    if (res.status === 409) { live.ready = false; ensureLive(); }
    else if (res.status === 423) { $("#liveMs").textContent = "видеокарта занята"; $("#liveBadge").hidden = false; await sleep(500); live.pending = true; live.pendingFinal = true; }
    else if (res.ok) {
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const im = $("#liveImg"); im.src = url; im.hidden = false; $("#liveEmpty").hidden = true;
      if (live.url) URL.revokeObjectURL(live.url);
      live.url = url;
      const ms = (+res.headers.get("X-Ms-Image") || 0) + (+res.headers.get("X-Ms-Translate") || 0);
      $("#liveMs").textContent = `${ms} мс`; $("#liveBadge").hidden = false;
    } else { let m = res.statusText; try { m = (await res.json()).detail || m; } catch { /* */ } toast(m, "error"); }
  } catch (e) { toast(e.message, "error"); }
  live.inflight = false;
  if (live.pending) { live.pending = false; const f = live.pendingFinal; live.pendingFinal = false; runLive(f); }
}

// ───────────── РЕДАКТОР ─────────────
const edit = { file: "", mode: "auto", model: "auto", plan: null };
const EDIT_CHIPS = ["Сделай ночь", "Добавь солнечные очки", "Сделай зимой, со снегом", "Перерисуй в стиле аниме", "Фон — морской пляж", "Пусть улыбается", "В стиле масляной живописи", "Добавь шляпу"];
$("#editChips").innerHTML = EDIT_CHIPS.map((t) => `<button type="button" class="sty" data-t="${esc(t)}">${esc(t)}</button>`).join("");
$("#editChips").onclick = (e) => { const b = e.target.closest(".sty"); if (!b) return; $("#editReq").value = b.dataset.t; resetEditPlan(); };
function resetEditPlan() { edit.plan = null; $("#editPeekBox").hidden = true; }
function setEditFile(name, url) {
  edit.file = name; resetEditPlan();
  $("#editPreview").src = url; $("#editPreview").hidden = false; $("#editDropHint").hidden = true;
  applyGating();
}
$("#editReq").addEventListener("input", resetEditPlan);
$("#editDrop").onclick = () => $("#editFile").click();
$("#editFile").onchange = (e) => { uploadImage(e.target.files[0], (r) => setEditFile(r.name, r.url)); e.target.value = ""; };
["dragenter", "dragover"].forEach((t) => $("#editDrop").addEventListener(t, (e) => { e.preventDefault(); $("#editDrop").classList.add("over"); }));
["dragleave", "drop"].forEach((t) => $("#editDrop").addEventListener(t, (e) => { e.preventDefault(); $("#editDrop").classList.remove("over"); }));
$("#editDrop").addEventListener("drop", (e) => uploadImage(e.dataTransfer.files[0], (r) => setEditFile(r.name, r.url)));
$("#editPick").onclick = () => pickImage((name, url) => setEditFile(name, url));
$("#editMode").onclick = (e) => { const b = e.target.closest("[data-m]"); if (!b) return; edit.mode = b.dataset.m; setSeg("#editMode", "m", edit.mode); resetEditPlan(); };
$("#editModel").onclick = (e) => { const b = e.target.closest("[data-k]"); if (!b) return; edit.model = b.dataset.k; setSeg("#editModel", "k", edit.model); };
$("#editStrAuto").onchange = (e) => { $("#editStrRow").hidden = e.target.checked; };
$("#editStr").addEventListener("input", () => { $("#editStrV").textContent = $("#editStr").value; });
$("#editCount").addEventListener("input", () => { $("#editCountV").textContent = $("#editCount").value; });

$("#editPeek").onclick = async () => {
  const request = $("#editReq").value.trim();
  if (!edit.file) return toast("Сначала выберите картинку.", "error");
  if (!request) return toast("Напишите, что изменить.", "error");
  if (!S.on) return toast("ИИ выключен — включите его переключателем.", "error");
  const btn = $("#editPeek"); btn.disabled = true; btn.textContent = "ИИ смотрит на картинку…";
  try {
    const r = await api("/api/edit/plan", { method: "POST", body: { file: edit.file, request, mode: edit.mode } });
    edit.plan = r;
    $("#editNotes").textContent = "✨ " + r.notes + (r.risks ? " " + r.risks : "");
    $("#editPlanMethod").textContent = "Способ: " + (r.method === "instruct" ? "точечное изменение по инструкции" : "перерисовка сильной моделью");
    $("#editPlanText").textContent = r.method === "instruct" ? "Команда модели: " + r.instruction : "Описание результата: " + r.prompt;
    $("#editPeekBox").hidden = false;
  } catch (e) { toast(e.message, "error"); }
  btn.disabled = false; btn.textContent = "Показать план ИИ";
};

$("#editForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const request = $("#editReq").value.trim();
  if (!edit.file) return toast("Сначала выберите картинку.", "error");
  if (!request) { toast("Напишите, что изменить.", "error"); $("#editReq").focus(); return; }
  const seed = $("#editSeed").value.trim(), auto = $("#editStrAuto").checked;
  const body = { file: edit.file, request, mode: edit.mode, strength: auto ? null : +$("#editStr").value / 100,
    model: edit.model, count: +$("#editCount").value, seed: seed === "" ? null : +seed, plan: edit.plan, effort: effortPct };
  $("#editGo").disabled = true;
  try {
    const { id } = await api("/api/edit", { method: "POST", body });
    trackJob(jobCard("edit", id, request, { auto: "Авто", instruct: "По инструкции", redraw: "Перерисовка" }[edit.mode]));
  } catch (err) { toast(err.message, "error"); }
  applyGating();
});

// ───────────── ГАЛЕРЕЯ ─────────────
let galLoading = null;
function loadGallery(quiet) {
  if (galLoading) return galLoading;
  galLoading = api("/api/gallery").then((r) => { S.gallery = r.items; if (S.view === "gallery") renderGallery(); })
    .catch((e) => { if (!quiet) toast(e.message, "error"); }).finally(() => { galLoading = null; });
  return galLoading;
}
function renderGallery() {
  const items = S.gallery.filter((x) => S.galKind === "all" || x.kind === S.galKind);
  $("#galCount").textContent = items.length ? `Файлов: ${items.length}` : "";
  $("#galGrid").innerHTML = items.length ? items.map((x) => `<button class="tile" data-open="${esc(x.name)}" title="${esc(x.prompt)}">${x.kind === "video" ? `<video src="${x.url}#t=0.1" muted preload="metadata" playsinline></video>` : `<img loading="lazy" src="${x.url}" alt="">`}<span class="badge">${icon(x.kind === "video" ? "play" : "image")}${x.kind === "video" ? "Видео" : "Картинка"}</span></button>`).join("")
    : '<div class="empty-state gal-empty"><svg class="i xl"><use href="#i-grid"/></svg><b>Пока ничего нет</b><span>Созданные картинки и видео сохраняются здесь.</span></div>';
}
$("#galFilter").onclick = (e) => { const b = e.target.closest("[data-k]"); if (!b) return; S.galKind = b.dataset.k; setSeg("#galFilter", "k", S.galKind); renderGallery(); };
$("#galGrid").onclick = (e) => { const t = e.target.closest("[data-open]"); if (t) openViewer(t.dataset.open); };

async function openViewer(name) {
  if (!S.gallery.find((x) => x.name === name)) await loadGallery(true);
  const x = S.gallery.find((i) => i.name === name);
  if (!x) return toast("Файл не найден.", "error");
  const media = x.kind === "video" ? `<video src="${x.url}" poster="${x.poster || ""}" controls autoplay loop playsinline></video>` : `<img src="${x.url}" alt="">`;
  const c = openModal(`<div class="viewer"><div class="stage">${media}</div>
    <div class="meta">${x.prompt ? "<b>Запрос:</b> " + esc(x.prompt) + "<br>" : ""}${x.prompt_en ? "<b>EN:</b> " + esc(x.prompt_en) + "<br>" : ""}${x.seed != null ? "seed " + x.seed : ""}${x.w ? " · " + x.w + "×" + x.h : ""}</div>
    <div class="modal-actions">${x.kind === "image" && x.prompt ? `<button class="btn" id="vwAgain">${icon("refresh")}Повторить запрос</button>` : ""}${x.kind === "image" ? `<button class="btn" id="vwEdit">${icon("edit")}Изменить</button><button class="btn" id="vwAnim">${icon("film")}Оживить</button>` : ""}<a class="btn" href="${x.url}" download="${esc(x.name)}">${icon("down")}Скачать</a><button class="btn danger" id="vwDel">${icon("trash")}Удалить</button><button class="btn primary" id="vwClose">Закрыть</button></div></div>`, true);
  $("#vwClose", c).onclick = closeModal;
  if ($("#vwAgain", c)) $("#vwAgain", c).onclick = () => { closeModal(); setView("images"); $("#imgPrompt").value = x.prompt; resetPeek("img"); toast("Запрос подставлен — нажмите «Создать картинку»", "ok", 2600); };
  if ($("#vwEdit", c)) $("#vwEdit", c).onclick = () => { closeModal(); setView("edit"); setEditFile(x.name, x.url); };
  if ($("#vwAnim", c)) $("#vwAnim", c).onclick = () => { closeModal(); setView("video"); vid.setFile(x.name, x.url); };
  $("#vwDel", c).onclick = async () => {
    if (!confirm("Удалить файл безвозвратно?")) return;
    try { await api("/api/gallery/" + encodeURIComponent(x.name), { method: "DELETE" }); S.gallery = S.gallery.filter((i) => i.name !== x.name); closeModal(); if (S.view === "gallery") renderGallery(); $$(`.media[data-name="${CSS.escape(x.name)}"]`).forEach((m) => m.remove()); toast("Удалено", "ok", 1800); }
    catch (e) { toast(e.message, "error"); }
  };
}

// ───────────── Effort (насколько сильно стараться) ─────────────
const EFFORT_NAMES = ["Easy", "Normal", "Hard", "Expert", "Ultra", "Mega", "Giga", "Extreme", "Insane", "Legend"];
let effortPct = +store.get("codwe.effort", 11);
const effortLevel = (p) => Math.round((Math.max(0, Math.min(100, p)) / 100) * 9);
function imageSecondsEstimate(ef) {  // оценка по замерам на этой видеокарте (RTX 4070 Laptop)
  const sps = (S.models && S.models.speed && S.models.speed.zimage_sec_per_step) || 4.6;
  const base = ef.n * ef.steps * sps * Math.pow(ef.mp, 1.7);
  let hi = 0;
  if (ef.hires > 1.01) {
    const hs = Math.max(10, ef.steps * 2), eff = Math.max(1, Math.floor(hs * ef.hires_strength));
    hi = eff * sps * Math.pow(ef.mp * ef.hires * ef.hires, 1.7) + 8;
  }
  return Math.round(base + hi + 22);
}
const fmtSec = (s) => (s < 90 ? `≈ ${s} с` : `≈ ${Math.round(s / 60)} мин`);
function renderEffort() {
  const lvl = effortLevel(effortPct);
  $("#effortTxt").textContent = `${EFFORT_NAMES[lvl]} · ${Math.round(effortPct)}%`;
  $("#efName").textContent = `${EFFORT_NAMES[lvl]} · ${Math.round(effortPct)}%`;
  $("#efRange").value = effortPct;
  $("#efLevels").innerHTML = EFFORT_NAMES.map((n, i) => `<button type="button" data-l="${i}" class="${i === lvl ? "on" : ""}">${i + 1}. ${n}</button>`).join("");
  const ef = S.models && S.models.effort && S.models.effort[lvl];
  $("#efInfo").innerHTML = ef
    ? `<b>Картинка:</b> ${ef.n > 1 ? ef.n + " попытки, лучшая выбирается сама" : "1 попытка"} · ${ef.steps} шагов${ef.hires > 1.01 ? ` · второй проход ×${ef.hires} (мелкие детали)` : ""} · ${fmtSec(imageSecondsEstimate(ef))}<br><b>Чат:</b> ${lvl >= 4 ? "глубокие размышления перед ответом" : lvl >= 2 ? "короткие размышления" : "быстрый ответ без размышлений"}`
    : "Загружаю описание уровня…";
}
function setEffort(p) { effortPct = Math.max(0, Math.min(100, +p)); store.set("codwe.effort", effortPct); renderEffort(); }
$("#effortBtn").onclick = (e) => { e.stopPropagation(); const c = $("#effortCard"); c.hidden = !c.hidden; if (!c.hidden) ensureModels().then(renderEffort); };
$("#efRange").oninput = (e) => setEffort(e.target.value);
$("#efLevels").onclick = (e) => { const b = e.target.closest("[data-l]"); if (b) setEffort(Math.round((+b.dataset.l / 9) * 100)); };
document.addEventListener("click", (e) => { if (!e.target.closest("#effort")) $("#effortCard").hidden = true; });
renderEffort();

// ───────────── карточка «почему заблокировано» (как у Gemini: причина + что сработало + что сделать) ─────────────
function blockCard(info, msg) {
  if (!info || !info.category) return `<div class="err-note shield-note">${esc(msg || "Заблокировано")}</div>`;
  const col = { csam: "var(--danger)", porn: "var(--warn)", gore: "var(--danger)" }[info.category] || "var(--warn)";
  return `<div class="block-card" style="--c:${col}"><div class="bc-dot"><svg class="i"><use href="#i-shield-off"/></svg></div><div class="bc-body"><b>Запрос отклонён: ${esc(info.label)}</b>`
    + `<div class="bc-row"><span>Где:</span> ${esc(info.where || "запрос")}</div>`
    + (info.trigger ? `<div class="bc-row"><span>Сработало на:</span> «${esc(info.trigger)}»</div>` : "")
    + `<div class="bc-row"><span>Что сделать:</span> ${esc(info.advice || "")}</div></div></div>`;
}

// ───────────── безопасные лимиты (щит) ─────────────
function renderSafety() {
  const sf = S.status && S.status.safety; if (!sf) return;
  const st = sf.tampered ? "tamper" : sf.enabled ? "on" : "off";
  const b = $("#safetyBtn");
  b.dataset.state = st;
  $("#safetyTxt").textContent = { on: "Защита", off: "Лимиты выкл", tamper: "Взлом защиты" }[st];
  $("use", b).setAttribute("href", st === "on" ? "#i-shield" : "#i-shield-off");
  b.title = { on: "Безопасные лимиты включены", off: "Администрация отключила блокировку 18+ и gore", tamper: "Обнаружено вмешательство в файлы защиты" }[st];
}
async function openSafety() {
  let sf;
  try { sf = await api("/api/safety"); } catch (e) { return toast(e.message, "error"); }
  const st = sf.tampered ? "tamper" : sf.enabled ? "on" : "off";
  const color = { on: "var(--on)", off: "var(--warn)", tamper: "var(--danger)" }[st];
  const title = { on: "Безопасные лимиты включены", off: "18+ и gore сейчас не блокируются", tamper: "Вмешательство в защиту" }[st];
  const canToggle = !!(window.codweAuth && ((window.codweAuth.state() || {}).perms || []).includes("safety.toggle")) && st !== "tamper";
  const lines = `<ul class="sh-list">
    <li><svg class="i ${st === "on" ? "ok" : "lock"}"><use href="#i-shield"/></svg><span><b>${st === "on" ? "Блокируется" : "Сейчас не блокируется (решение администрации)"}:</b> откровенный сексуальный контент (porn / NSFW) и жестокий gore: в запросах, в итоговых английских промптах и в готовых картинках и видео (локальная проверка).</span></li>
    <li><svg class="i lock"><use href="#i-lock"/></svg><span><b>Всегда включено</b> и не отключается никем, даже владельцем: любой сексуальный контент с несовершеннолетними.</span></li>
    <li><svg class="i lock"><use href="#i-lock"/></svg><span><b>Кто меняет режим:</b> только ранг с правом «safety.toggle» через облако, для всех пользователей сразу. Кнопки «выключить» у обычного пользователя нет, а ручная правка файлов не работает.</span></li>
    <li><svg class="i ${sf.classifier_ready ? "ok" : "lock"}"><use href="#i-shield"/></svg><span>Проверка картинок: <b>${sf.classifier_ready ? "готова" : "загрузится при первой картинке"}</b>.</span></li></ul>`;
  const warn = st === "tamper" ? `<div class="sh-warn sh-danger">⚠ ${esc(sf.tamper_reason || "Файлы защиты изменены.")} Безопасные лимиты включены принудительно, выключить их нельзя. Восстановите файлы safety.py, policy.enc и safety.sig.</div>` : "";
  const note = '<p class="hint">Честно: это защита от случайного и простого обхода. Человек с полным доступом к файлам программы и навыками программирования может её убрать, как с любой локальной программой.</p>';
  const toggle = canToggle ? (st === "on"
    ? '<button class="btn danger" id="shOffAsk">Выключить для всех…</button>'
    : '<button class="btn primary" id="shOn">Включить для всех</button>') : "";
  const c = openModal(`<div class="sh-hero"><div class="badge-big" style="--c:${color}"><svg class="i"><use href="#i-shield${st === "on" ? "" : "-off"}"/></svg></div><div><h3>${title}</h3><p>Защита от генерации NSFW, gore и порно.</p></div></div>${warn}${lines}${note}
    <div class="modal-actions">${toggle}<button class="btn" id="shClose">Закрыть</button></div>`);
  $("#shClose", c).onclick = closeModal;
  const apply = async (enabled, adult) => {
    try { await api("/api/admin/safety", { method: "POST", body: { enabled, confirm_adult: adult } }); await pollNow(); closeModal(); toast(enabled ? "🛡 Безопасные лимиты включены у всех" : "Безопасные лимиты отключены у всех", enabled ? "ok" : "warn", 4000); }
    catch (e) { toast(e.message, "error"); }
  };
  if ($("#shOn", c)) $("#shOn", c).onclick = () => apply(true, false);
  if ($("#shOffAsk", c)) $("#shOffAsk", c).onclick = () => {
    c.innerHTML = `<div class="sh-hero"><div class="badge-big" style="--c:var(--warn)"><svg class="i"><use href="#i-shield-off"/></svg></div><div><h3>Отключить безопасные лимиты для всех?</h3><p>У всех пользователей станут возможны откровенные (18+) и жестокие картинки.</p></div></div>
      <div class="sh-warn">Сексуальный контент с несовершеннолетними останется заблокированным всегда. Изменение попадёт в журнал, и ответственность за него на вас.</div>
      <label class="check big"><input type="checkbox" id="shAdult"><span>Мне есть 18 лет, я понимаю, что делаю, и сам отвечаю за это решение.</span></label>
      <div class="modal-actions"><button class="btn" id="shBack">Назад</button><button class="btn danger" id="shOff" disabled>Отключить лимиты</button></div>`;
    $("#shAdult", c).onchange = (e) => { $("#shOff", c).disabled = !e.target.checked; };
    $("#shBack", c).onclick = () => { closeModal(); openSafety(); };
    $("#shOff", c).onclick = () => apply(false, true);
  };
}
$("#safetyBtn").onclick = openSafety;
async function pollNow() { try { S.status = await api("/api/status"); renderPower(); renderSafety(); } catch { /* */ } }

// ───────────── настройки ─────────────
$("#openSettings").onclick = () => {
  const th = store.get("codwe.theme", "dark");
  const c = openModal(`<h3>Настройки</h3>
    <div><label class="lbl">Тема</label><div class="seg" id="stTheme"><button data-t="dark" class="${th === "dark" ? "active" : ""}">Тёмная</button><button data-t="light" class="${th === "light" ? "active" : ""}">Светлая</button><button data-t="auto" class="${th === "auto" ? "active" : ""}">Как в системе</button></div></div>
    <div><label class="lbl">Чат</label><label class="check big"><input type="checkbox" id="stArt" ${settings.artifacts ? "checked" : ""}><span><b>Артефакты</b> — ИИ делает сайты, игры и SVG одним файлом, а справа открывается просмотр</span></label><label class="check big"><input type="checkbox" id="stAuto" ${settings.autoOpen ? "checked" : ""}><span>Открывать артефакт автоматически</span></label><label class="check big"><input type="checkbox" id="stDuo" ${settings.duo !== false ? "checked" : ""}><span><b>Две модели в чате</b> — когда просите нарисовать, текстовая модель сначала отвечает словами, а потом картиночная рисует</span></label></div>
    <div><label class="lbl">Приватность</label><p>🔒 <b>Ваши фото и запросы никто не видит.</b> Сервер слушает только этот компьютер (127.0.0.1). Чаты хранятся в вашем браузере, картинки и видео — в папке <code>outputs</code>. Ни разработчик, ни администраторы, ни другие пользователи не могут их открыть: в облако они не отправляются. В облаке лежат только почта, ранг и статус блокировки. В интернет уходит лишь то, что вы сами запустили: поиск в Codwecode или скачивание моделей.</p></div>
    <div class="modal-actions"><button class="btn danger" id="stClear">Очистить историю чатов</button><button class="btn danger" id="stQuit">Остановить сервер</button><button class="btn primary" id="stClose">Готово</button></div>`);
  $("#stTheme", c).onclick = (e) => { const b = e.target.closest("[data-t]"); if (!b) return; store.set("codwe.theme", b.dataset.t); applyTheme(); setSeg("#stTheme", "t", b.dataset.t); };
  $("#stClose", c).onclick = closeModal;
  $("#stArt", c).onchange = (e) => { settings.artifacts = e.target.checked; saveSettings(); };
  $("#stAuto", c).onchange = (e) => { settings.autoOpen = e.target.checked; saveSettings(); };
  $("#stDuo", c).onchange = (e) => { settings.duo = e.target.checked; saveSettings(); };
  $("#stClear", c).onclick = () => { if (confirm("Удалить все чаты?")) { chats = []; saveChats(); newChat(); closeModal(); toast("История очищена", "ok"); } };
  $("#stQuit", c).onclick = async () => {
    if (!confirm("Остановить сервер Codwe AI? Чтобы запустить снова, откройте start_codwe.bat.")) return;
    try { await api("/api/shutdown", { method: "POST" }); } catch { /* сервер уже закрывается */ }
    document.body.innerHTML = '<div style="height:100vh;display:grid;place-items:center;text-align:center;font:16px system-ui;color:#8d95aa;background:#090b10"><div><b style="color:#e9ecf4;font-size:20px">Сервер остановлен</b><br>Можно закрыть это окно.</div></div>';
  };
};

// ───────────── запуск ─────────────
(async function init() {
  newChat();
  const v = store.get("codwe.view", "chat");
  setView(["chat", "projects", "artifacts", "code", "live", "images", "edit", "video", "gallery"].includes(v) ? v : "chat");
  showSource(); updateSend();
  await pollStatus();
  ensureModels();
})();
