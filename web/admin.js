// Админ-панель с рангами (как роли в Discord): пользователи, ранги, модели, безопасность, доступ, журнал.
// Эта страница только показывает и отправляет команды. Права и иерархию рангов проверяет облако (supabase/ranks.sql),
// поэтому обойти их, изменив эту страницу, нельзя.
(() => {
  const A = () => window.codweAuth;
  const esc = (s) => A().esc(s);
  const call = (...a) => A().call(...a);
  const okColor = (c) => (/^#[0-9a-f]{6}$/i.test(c || "") ? c : "#8d95aa");

  const TABS = [
    ["users", "👥", "Пользователи", ["users.view"]],
    ["ranks", "🎖", "Ранги", null],
    ["models", "🧩", "Модели", ["models.manage"]],
    ["safety", "🛡", "Безопасность", ["safety.toggle"]],
    ["access", "🔑", "Доступ", ["access.manage"]],
    ["audit", "📜", "Журнал", ["users.view"]],
  ];
  const KINDS = { chat: "Чат (языковые модели)", image: "Картинки", edit: "Редактор", video: "Видео", live: "Live" };
  const ACTIONS = {
    ban: "заблокировал", unban: "разблокировал", rank: "сменил ранг", "access-reset": "сбросил доступ",
    "access-password-set": "сменил пароль доступа", "access-password-clear": "снял пароль доступа",
    "model-block": "заблокировал модель", "model-unblock": "разблокировал модель",
    "safety-on": "включил режим безопасности", "safety-off": "ВЫКЛЮЧИЛ режим безопасности",
    "rank-save": "сохранил ранг", "rank-delete": "удалил ранг",
  };

  let wrap = null, D = null, tab = "users", filter = "", catalog = null, audit = null, editing = null, timer = null;

  const has = (p) => !!(D && D.perms.includes(p));
  const rk = (key) => (D && D.ranks.find((r) => r.key === key)) || { key, name: key || "—", level: 0, color: "#8d95aa" };
  const pill = (r) => `<span class="rk-pill" style="--rc:${okColor(r.color)}">${esc(r.name)}</span>`;
  const canOpen = () => { const p = (A().state() || {}).perms || []; return ["users.view", "models.manage", "safety.toggle", "access.manage"].some((x) => p.includes(x)); };

  function msg(text, kind = "ok") {
    const m = wrap && wrap.querySelector("#adMsg"); if (!m) return;
    m.textContent = text; m.className = "ad-msg " + kind; m.hidden = !text;
    clearTimeout(timer); if (text) timer = setTimeout(() => { m.hidden = true; }, 6000);
  }

  async function open() {
    if (wrap) return;
    wrap = document.createElement("div");
    wrap.className = "auth-layer admin-layer";
    wrap.innerHTML = `<div class="admin-card"><div class="ad-head"><h2>Управление</h2><span id="adMe" class="ad-me"></span><button class="btn" id="adClose">Закрыть</button></div>
      <div id="adMsg" class="ad-msg" hidden></div><div class="ad-tabs" id="adTabs"></div><div id="adBody" class="ad-body">Загрузка…</div></div>`;
    document.body.appendChild(wrap);
    wrap.querySelector("#adClose").onclick = close;
    wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
    wrap.addEventListener("click", onClick);
    wrap.addEventListener("change", onChange);
    wrap.addEventListener("input", (e) => { if (e.target.id === "adFind") { filter = e.target.value.toLowerCase(); drawUsers(true); } });
    document.addEventListener("keydown", esc2);
    await load();
  }
  const esc2 = (e) => { if (e.key === "Escape" && !document.querySelector(".modal:not([hidden])")) close(); };
  function close() { if (wrap) { wrap.remove(); wrap = null; document.removeEventListener("keydown", esc2); } D = null; catalog = null; audit = null; editing = null; }

  async function load() {
    try {
      D = await call("/api/admin/overview");
    } catch (e) { return body(`<p class="au-err">${esc(e.message)}</p>`); }
    const me = (A().state() || {}).email || "";
    const r = D.ranks.find((x) => x.level === D.my_level) || rk((A().state() || {}).role);
    wrap.querySelector("#adMe").innerHTML = `${esc(me)} ${pill(r)} <span class="au-fine">уровень ${D.my_level}</span>`;
    const allowed = TABS.filter((t) => !t[3] || t[3].some(has));
    if (!allowed.find((t) => t[0] === tab)) tab = allowed[0] ? allowed[0][0] : "ranks";
    wrap.querySelector("#adTabs").innerHTML = allowed.map((t) => `<button class="ad-tab ${t[0] === tab ? "active" : ""}" data-tab="${t[0]}">${t[1]} ${t[2]}</button>`).join("");
    draw();
  }
  const body = (html) => { const b = wrap && wrap.querySelector("#adBody"); if (b) b.innerHTML = html; };

  function legacyBanner() {
    return D.legacy ? `<div class="ad-legacy"><b>⚠ Ранги ещё не включены в облаке.</b> Сейчас работает старая схема «админ / пользователь».
      Откройте Supabase → SQL Editor → New query, вставьте весь файл <code>supabase\\ranks.sql</code> (он лежит в папке программы) и нажмите Run. После этого появятся ранги, защита владельца, блокировка моделей и режим безопасности.</div>` : "";
  }

  async function draw() {
    if (!wrap) return;
    wrap.querySelectorAll(".ad-tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    if (tab === "users") return drawUsers();
    if (tab === "ranks") return drawRanks();
    if (tab === "models") return drawModels();
    if (tab === "safety") return drawSafety();
    if (tab === "access") return drawAccess();
    if (tab === "audit") return drawAudit();
  }

  // ───── пользователи ─────
  async function drawUsers(keepFocus) {
    if (!D.users || !keepFocus) {
      body("Загрузка…");
      try { const d = await call("/api/admin/users"); D.users = d.users; } catch (e) { return body(legacyBanner() + `<p class="au-err">${esc(e.message)}</p>`); }
    }
    const rows = D.users.filter((u) => !filter || (u.email || "").toLowerCase().includes(filter) || (rk(u.role).name || "").toLowerCase().includes(filter));
    const selOpts = (cur) => D.ranks.filter((r) => r.level < D.my_level && r.key !== "owner").map((r) => `<option value="${esc(r.key)}" ${r.key === cur ? "selected" : ""}>${esc(r.name)}</option>`).join("");
    const html = legacyBanner() + `<div class="ad-bar"><input id="adFind" class="au-in ad-find" placeholder="Поиск по почте или рангу…" value="${esc(filter)}" autocomplete="off"><span class="au-fine">Всего: ${D.users.length}</span></div>
      <div class="ad-scroll"><table class="ad-tbl"><thead><tr><th>Почта</th><th>Ранг</th><th>Статус</th><th>Создан</th><th>Был</th><th>Действия</th></tr></thead><tbody>`
      + rows.map((u) => {
        const r = rk(u.role), mine = u.id === D.me, can = !mine && r.level < D.my_level;
        let act = "";
        if (mine) act = `<i class="au-fine">это вы</i>`;
        else if (!can) act = `<span class="ad-lock" title="Ранг этого пользователя не ниже вашего: действовать на него нельзя">🔒 не ниже вас</span>`;
        else {
          if (has("users.rank")) act += `<select class="ad-sel" data-rk="${u.id}" title="Выдать ранг (только ниже вашего)">${selOpts(u.role)}</select> `;
          if (has("users.ban")) act += `<button class="btn small ${u.banned ? "" : "danger"}" data-ban="${u.id}" data-to="${!u.banned}">${u.banned ? "Разбанить" : "Забанить"}</button> `;
          if (has("access.manage")) act += `<button class="btn small" data-reset="${u.id}" title="Пользователь снова введёт пароль доступа">Сбросить доступ</button>`;
          if (!act) act = `<i class="au-fine">нет прав</i>`;
        }
        return `<tr class="${u.banned ? "is-banned" : ""}"><td>${esc(u.email)}${mine ? " <i>(вы)</i>" : ""}</td><td>${pill(r)}</td>
          <td>${u.banned ? `<span class="bad">бан${u.ban_reason ? ": " + esc(u.ban_reason) : ""}</span>` : u.tos_version ? "ок" : "не принял условия"}</td>
          <td>${esc((u.created_at || "").slice(0, 10))}</td><td>${esc((u.last_seen || "").slice(0, 16).replace("T", " "))}</td><td class="ad-act">${act}</td></tr>`;
      }).join("") + `</tbody></table></div><p class="au-fine">Действовать можно только на тех, кто ниже вас рангом. Права проверяет облако, а не эта страница.</p>`;
    body(html);
    if (keepFocus) { const f = wrap.querySelector("#adFind"); if (f) { f.focus(); f.setSelectionRange(f.value.length, f.value.length); } }
  }

  // ───── ранги ─────
  function drawRanks() {
    const owner = D.is_owner;
    const permChips = (r) => r.key === "owner" ? `<span class="perm-chip all">ВСЕ права</span>`
      : ((r.perms || []).map((p) => `<span class="perm-chip" title="${esc(D.perm_names[p] || p)}">${esc(D.perm_names[p] || p)}</span>`).join("") || `<span class="au-fine">особых прав нет</span>`);
    const form = (r) => {
      const isNew = !r;
      const v = r || { key: "", name: "", level: 30, color: "#c6f432", perms: [] };
      return `<div class="rk-card rk-edit" style="--rc:${okColor(v.color)}" data-form="${isNew ? "" : esc(v.key)}">
        <div class="rk-grid">
          <label>Ключ (латиницей)<input class="au-in" id="rkKey" ${isNew ? "" : "disabled"} value="${esc(v.key)}" placeholder="vip" maxlength="24"></label>
          <label>Название<input class="au-in" id="rkName" value="${esc(v.name)}" placeholder="VIP" maxlength="24"></label>
          <label>Уровень (1–99)<input class="au-in" id="rkLevel" type="number" min="1" max="99" value="${v.level}"></label>
          <label>Цвет<input class="au-in rk-color" id="rkColor" type="color" value="${okColor(v.color)}"></label>
        </div>
        <div class="rk-permlist">${Object.entries(D.perm_names).map(([p, t]) => `<label class="check"><input type="checkbox" data-perm="${p}" ${(v.perms || []).includes(p) ? "checked" : ""}><span>${esc(t)}</span></label>`).join("")}</div>
        <div class="rk-btns"><button class="btn primary" data-rksave>Сохранить</button><button class="btn" data-rkcancel>Отмена</button>
          ${isNew ? "" : `<button class="btn danger" data-rkdel="${esc(v.key)}">Удалить ранг</button>`}</div></div>`;
    };
    const cards = D.ranks.map((r) => editing === r.key ? form(r) : `<div class="rk-card" style="--rc:${okColor(r.color)}">
        <div class="rk-top">${pill(r)}<span class="au-fine">ключ <code>${esc(r.key)}</code> · уровень <b>${r.level}</b></span>
        ${r.key === D.ranks.find((x) => x.level === D.my_level)?.key ? '<span class="rk-you">ваш ранг</span>' : ""}
        ${owner && r.key !== "owner" ? `<button class="btn small rk-edit-btn" data-rkedit="${esc(r.key)}">Изменить</button>` : ""}</div>
        <div class="rk-perms">${permChips(r)}</div></div>`).join("");
    body(legacyBanner() + `<div class="rk-help"><b>Как работают ранги</b>
        <ul><li>Чем выше уровень, тем выше ранг. <b>Владелец = 100</b>, его нельзя забанить, понизить или изменить.</li>
        <li>Действовать (бан, смена ранга, сброс доступа) можно <b>только на тех, кто строго ниже вас</b>.</li>
        <li>Выдать можно только ранг <b>ниже вашего</b>. Ранг «Владелец» выдать нельзя никому.</li>
        <li>${owner ? "Вы владелец: можете создавать ранги и менять их права." : "Ранги и их права меняет только владелец."}</li></ul></div>
        <div class="rk-list">${cards}${editing === "" ? form(null) : ""}</div>
        ${owner && editing === null ? `<button class="btn primary" data-rknew>＋ Новый ранг</button>` : ""}`);
  }

  async function saveRank() {
    const card = wrap.querySelector(".rk-edit"); if (!card) return;
    const key = card.dataset.form || wrap.querySelector("#rkKey").value.trim().toLowerCase();
    const perms = [...card.querySelectorAll("[data-perm]:checked")].map((x) => x.dataset.perm);
    try {
      await call("/api/admin/rank-save", "POST", { key, name: wrap.querySelector("#rkName").value.trim(), level: +wrap.querySelector("#rkLevel").value, color: wrap.querySelector("#rkColor").value, perms });
      editing = null; msg("Ранг сохранён.", "ok"); await load();
    } catch (e) { msg(e.message, "err"); }
  }

  // ───── модели ─────
  async function drawModels() {
    if (!catalog) {
      body("Загрузка…");
      try { catalog = (await call("/api/models/catalog")).models; } catch (e) { return body(`<p class="au-err">${esc(e.message)}</p>`); }
    }
    const groups = {};
    catalog.forEach((m) => (groups[m.kind] = groups[m.kind] || []).push(m));
    const nBlocked = catalog.filter((m) => m.blocked).length;
    body(legacyBanner() + `<div class="ad-note">Заблокированная модель пропадает у <b>всех</b> пользователей (в течение ~1,5 минуты) и не запускается, даже если очень постараться. Заблокировано сейчас: <b>${nBlocked}</b>.</div>`
      + Object.keys(KINDS).filter((k) => groups[k]).map((k) => `<div class="md-group"><div class="md-title">${KINDS[k]}</div>`
        + groups[k].map((m) => `<div class="md-row ${m.blocked ? "is-blocked" : ""}"><div class="md-info"><b>${esc(m.title)}</b><code>${esc(m.id)}</code></div>
          <span class="md-state">${m.blocked ? "🔒 заблокирована" : m.ready ? "готова" : "не скачана"}</span>
          <label class="tg" title="${m.blocked ? "Разблокировать" : "Заблокировать"}"><input type="checkbox" data-mb="${esc(m.id)}" ${m.blocked ? "" : "checked"}><i></i></label></div>`).join("") + `</div>`).join(""));
  }

  // ───── безопасность ─────
  function drawSafety() {
    const on = D.policy.safety_soft;
    body(legacyBanner() + `<div class="sf-card ${on ? "on" : "off"}"><div class="sf-ico">${on ? "🛡" : "⚠"}</div><div><h3>Режим безопасности: ${on ? "ВКЛЮЧЁН" : "ВЫКЛЮЧЕН"}</h3>
        <p>${on ? "Откровенный 18+ контент и жестокий gore блокируются у всех пользователей, с объяснением причины." : "Откровенный 18+ контент и gore у ВСЕХ пользователей НЕ блокируются. Вы отвечаете за это решение."}</p></div></div>
      <div class="sf-lock">🔒 <b>Всегда включено и не отключается никем:</b> любой сексуальный контент с несовершеннолетними. Этот переключатель его не затрагивает.</div>
      ${on ? `<label class="check big"><input type="checkbox" id="sfAdult"><span>Мне есть 18 лет, я понимаю последствия и отвечаю за решение.</span></label>
        <button class="btn danger" id="sfOff" disabled>Выключить режим безопасности для всех</button>`
        : `<button class="btn primary" id="sfOn">Включить режим безопасности для всех</button>`}
      <p class="au-fine">Изменение применяется у всех пользователей в течение ~1,5 минуты и записывается в журнал.</p>`);
  }

  // ───── доступ ─────
  function drawAccess() {
    body(legacyBanner() + `<div class="ad-pw"><b>Пароль доступа</b>
      <span class="au-fine">Один пароль для всех (минимум 8 знаков, лучше длинный и со знаками). Все, кроме рангов с правом управлять доступом, должны будут ввести его заново. В облаке хранится только хеш.</span>
      <div class="ad-pw-row"><input id="adPw" type="password" autocomplete="new-password" class="au-in" placeholder="Новый пароль доступа"><button class="btn primary" id="adPwSet">Задать</button><button class="btn" id="adPwClear">Снять пароль</button></div></div>
      <div class="ad-pw"><b>Код подключения</b>
      <span class="au-fine">Новым пользователям нужен код подключения (его просит программа при первом запуске). Отправляйте его вместе с паролем доступа.</span>
      <div><button class="btn" id="adCode">Скопировать код подключения</button></div></div>`);
  }

  // ───── журнал ─────
  async function drawAudit() {
    body("Загрузка…");
    try { audit = (await call("/api/admin/audit")).log; } catch (e) { return body(legacyBanner() + `<p class="au-err">${esc(e.message)}</p>`); }
    body(legacyBanner() + (audit.length ? `<div class="ad-scroll"><table class="ad-tbl"><thead><tr><th>Когда</th><th>Кто</th><th>Что сделал</th><th>Над кем / чем</th><th>Подробности</th></tr></thead><tbody>`
      + audit.map((r) => `<tr><td>${esc((r.at || "").slice(5, 16).replace("T", " "))}</td><td>${esc(r.actor_email || "?")}</td><td>${esc(ACTIONS[r.action] || r.action)}</td><td>${esc(r.target || "")}</td><td>${esc(r.detail || "")}</td></tr>`).join("")
      + `</tbody></table></div>` : `<p class="au-fine">Пока записей нет.</p>`));
  }

  // ───── события ─────
  async function onClick(e) {
    const t = e.target;
    const tb = t.closest("[data-tab]");
    if (tb) { tab = tb.dataset.tab; editing = null; if (tab === "models") catalog = null; return draw(); }
    try {
      if (t.id === "adPwSet") {
        const v = wrap.querySelector("#adPw").value;
        await call("/api/admin/access-password", "POST", { password: v }); wrap.querySelector("#adPw").value = "";
        return msg("Пароль доступа задан. Остальные введут его при следующем входе.");
      }
      if (t.id === "adPwClear") { if (!confirm("Снять пароль доступа? Войти смогут все с аккаунтом.")) return; await call("/api/admin/access-clear", "POST", {}); return msg("Пароль доступа снят."); }
      if (t.id === "adCode") { const d = await call("/api/admin/connect-code"); await navigator.clipboard.writeText(d.code); return msg("Код подключения скопирован. Отправьте его вместе с паролем доступа."); }
      if (t.id === "sfOff") { await call("/api/admin/safety", "POST", { enabled: false, confirm_adult: true }); msg("Режим безопасности ВЫКЛЮЧЕН у всех.", "warn"); return load(); }
      if (t.id === "sfOn") { await call("/api/admin/safety", "POST", { enabled: true }); msg("Режим безопасности включён у всех."); return load(); }
      const ban = t.closest("[data-ban]");
      if (ban) {
        const to = ban.dataset.to === "true"; let reason = "";
        if (to) { reason = prompt("Причина блокировки (увидит пользователь):", "нарушение правил"); if (reason === null) return; }
        await call("/api/admin/ban", "POST", { id: ban.dataset.ban, banned: to, reason }); msg(to ? "Пользователь заблокирован." : "Пользователь разблокирован.");
        D.users = null; return drawUsers();
      }
      const rs = t.closest("[data-reset]");
      if (rs) { if (!confirm("Сбросить доступ? Пользователь снова введёт пароль доступа.")) return; await call("/api/admin/access-reset", "POST", { id: rs.dataset.reset }); return msg("Доступ сброшен."); }
      if (t.closest("[data-rknew]")) { editing = ""; return drawRanks(); }
      const ed = t.closest("[data-rkedit]"); if (ed) { editing = ed.dataset.rkedit; return drawRanks(); }
      if (t.closest("[data-rkcancel]")) { editing = null; return drawRanks(); }
      if (t.closest("[data-rksave]")) return saveRank();
      const del = t.closest("[data-rkdel]");
      if (del) { if (!confirm("Удалить ранг «" + del.dataset.rkdel + "»? Его участники станут «Пользователь».")) return; await call("/api/admin/rank-delete", "POST", { key: del.dataset.rkdel }); editing = null; msg("Ранг удалён."); return load(); }
    } catch (err) { msg(err.message, "err"); }
  }

  async function onChange(e) {
    const t = e.target;
    try {
      if (t.id === "sfAdult") { const b = wrap.querySelector("#sfOff"); if (b) b.disabled = !t.checked; return; }
      if (t.dataset.rk) {
        const old = t.querySelector("option[selected]") ? t.querySelector("option[selected]").value : "";
        if (!confirm("Выдать ранг «" + t.options[t.selectedIndex].text + "»?")) { t.value = old; return; }
        await call("/api/admin/role", "POST", { id: t.dataset.rk, role: t.value }); msg("Ранг изменён."); D.users = null; return drawUsers();
      }
      if (t.dataset.mb) {
        const blocked = !t.checked;
        if (blocked && !confirm("Заблокировать модель «" + t.dataset.mb + "» для ВСЕХ пользователей?")) { t.checked = true; return; }
        t.disabled = true;
        await call("/api/admin/models", "POST", { model: t.dataset.mb, blocked });
        msg(blocked ? "Модель заблокирована для всех." : "Модель снова доступна."); catalog = null; D = Object.assign(D, { policy: (await call("/api/admin/overview")).policy }); return drawModels();
      }
    } catch (err) { msg(err.message, "err"); if (t.dataset.mb) { catalog = null; drawModels(); } if (t.dataset.rk) { D.users = null; drawUsers(); } }
  }

  window.codweAdmin = { open: () => { if (!canOpen()) return alert("У вашего ранга нет прав на управление."); open(); }, canOpen };
})();
