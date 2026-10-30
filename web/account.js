// Меню аккаунта (как у Claude): кружок с буквой справа вверху -> почта, роль, сохранённые аккаунты, админка, выход.
(() => {
  const A = () => window.codweAuth;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let st = null, btn = null, menu = null, accounts = [];

  const initial = (m) => (m || "?").trim().charAt(0).toUpperCase();
  const hue = (m) => { let h = 0; for (const c of m || "") h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
  const av = (m, big) => `<span class="ac-av ${big ? "big" : ""}" style="--h:${hue(m)}">${esc(initial(m))}</span>`;

  function ensureButton() {
    if (btn && document.body.contains(btn)) return;
    const gear = document.getElementById("openSettings");
    if (!gear) return;
    btn = document.createElement("button");
    btn.id = "accountBtn"; btn.className = "ac-btn"; btn.setAttribute("aria-label", "Аккаунт"); btn.title = "Аккаунт";
    gear.insertAdjacentElement("afterend", btn);
    btn.onclick = (e) => { e.stopPropagation(); toggle(); };
  }

  function close() { if (menu) { menu.remove(); menu = null; } }
  async function loadAccounts() {
    try { const d = await A().call("/api/auth/accounts"); accounts = (d.accounts || []).filter((x) => x !== d.current); } catch { accounts = []; }
  }
  async function toggle() {
    if (menu) return close();
    await loadAccounts();
    menu = document.createElement("div");
    menu.className = "ac-menu";
    const admin = st && st.role === "admin";
    menu.innerHTML = `
      <div class="ac-head">${av(st.email, true)}<div class="ac-who"><b>${esc(st.email)}</b><span>${admin ? "Администратор" : "Пользователь"} · Codwe AI</span></div></div>
      ${accounts.length ? `<div class="ac-sep"></div><div class="ac-lbl">Другие аккаунты</div>${accounts.map((e) => `<button class="ac-item" data-sw="${esc(e)}">${av(e)}<span>${esc(e)}</span></button>`).join("")}` : ""}
      <button class="ac-item" data-act="add"><span class="ac-ic">＋</span><span>Добавить аккаунт</span></button>
      <div class="ac-sep"></div>
      ${admin ? `<button class="ac-item" data-act="admin"><span class="ac-ic">👑</span><span>Пользователи и права</span></button>
      <button class="ac-item" data-act="code"><span class="ac-ic">🔗</span><span>Код подключения</span></button>` : ""}
      <button class="ac-item" data-act="settings"><span class="ac-ic">⚙</span><span>Настройки</span></button>
      <div class="ac-sep"></div>
      <button class="ac-item danger" data-act="out"><span class="ac-ic">⎋</span><span>Выйти</span></button>`;
    document.body.appendChild(menu);
    const r = btn.getBoundingClientRect();
    menu.style.top = Math.round(r.bottom + 8) + "px";
    menu.style.right = Math.max(8, Math.round(window.innerWidth - r.right)) + "px";
    menu.onclick = async (e) => {
      const b = e.target.closest("button"); if (!b) return;
      const sw = b.dataset.sw, act = b.dataset.act;
      close();
      try {
        if (sw) { await A().call("/api/auth/switch", "POST", { email: sw }); location.reload(); }
        else if (act === "add") { await A().call("/api/auth/add", "POST", {}); A().refresh(); }
        else if (act === "admin") A().admin();
        else if (act === "code") { const d = await A().call("/api/admin/connect-code"); await navigator.clipboard.writeText(d.code); alert("Код подключения скопирован. Отправьте его пользователю вместе с паролем доступа."); }
        else if (act === "settings") { const g = document.getElementById("openSettings"); if (g) g.click(); }
        else if (act === "out") { if (confirm("Выйти из аккаунта " + st.email + "?")) { await A().call("/api/auth/logout", "POST", {}); A().refresh(); } }
      } catch (err) { alert(err.message); }
    };
  }
  document.addEventListener("click", (e) => { if (menu && !menu.contains(e.target) && e.target !== btn) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });

  function onState(s) {
    st = s; ensureButton(); if (!btn) return;
    const ok = s && s.logged_in && s.email && !s.tampered && !s.banned;
    btn.hidden = !ok;
    if (ok) btn.innerHTML = av(s.email);
    if (!ok) close();
  }
  const wait = setInterval(() => { if (window.codweAuth) { window.codweAuth.onState = onState; clearInterval(wait); if (window.codweAuth.state()) onState(window.codweAuth.state()); } }, 100);
})();
