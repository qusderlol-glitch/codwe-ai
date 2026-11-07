// Вход по почте, условия с проверкой «прочитал ли», экран блокировки и админ-панель.
// Все проверки (вход, бан, время чтения, ответы, права админа) делает сервер и облако; здесь только интерфейс.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const H = { "X-Codwe": "1", "Content-Type": "application/json" };
  const rawFetch = window.fetch.bind(window);
  let st = null, layer = null, busy = false, lastKey = "", timer = null;

  async function call(path, method = "GET", body) {
    const r = await rawFetch(path, { method, headers: H, body: body ? JSON.stringify(body) : undefined });
    let j = {}; try { j = await r.json(); } catch { /* */ }
    if (!r.ok) { const e = new Error(typeof j.detail === "string" ? j.detail : "Ошибка " + r.status); e.status = r.status; throw e; }
    return j;
  }

  // любой ответ «нужен вход / условия / файлы изменены» от сервера сразу открывает нужный экран
  window.fetch = async (...a) => {
    const res = await rawFetch(...a);
    try {
      const u = typeof a[0] === "string" ? a[0] : a[0].url;
      if ((res.status === 401 || res.status === 403) && /\/api\//.test(u) && !/\/api\/auth\//.test(u)) {
        res.clone().json().then((j) => { if (j && ["auth_required", "tos_required", "tamper", "banned", "connect_required", "access_required", "guard"].includes(j.code)) refresh(); }).catch(() => {});
      }
    } catch { /* */ }
    return res;
  };

  function mount() {
    if (layer) return layer;
    layer = document.createElement("div");
    layer.id = "authLayer"; layer.hidden = true;
    document.body.appendChild(layer);
    return layer;
  }
  const show = (html, cls = "") => { const l = mount(); l.className = "auth-layer " + cls; l.innerHTML = html; l.hidden = false; document.body.classList.add("auth-open"); };
  const hide = () => { if (layer) { layer.hidden = true; layer.innerHTML = ""; } document.body.classList.remove("auth-open"); clearInterval(timer); };

  // ─────── экраны ───────
  function lockScreen(reason) {
    show(`<div class="lock"><div class="lock-ico">⛔</div><h1>ОБНАРУЖЕНО ИЗМЕНЕНИЕ ПРОГРАММЫ</h1>
      <p>Файлы Codwe AI не совпадают с оригиналом, поэтому программа заблокирована.</p>
      <p class="lock-r">${esc(reason || "")}</p><p>Скачайте Codwe AI заново из официального источника.</p></div>`, "lock-layer");
  }
  function bannedScreen(s) {
    show(`<div class="lock ban-box"><div class="lock-ico">🚫</div><h1>ВЫ БЫЛИ ЗАБАНЕНЫ</h1>
      <div class="ban-mail">${esc((s && s.email) || "")}</div>
      <div class="ban-why"><span>Причина</span><b>${esc((s && s.ban_reason) || "не указана")}</b></div>
      <p>Доступ к Codwe AI для этого аккаунта закрыт. Если вы считаете, что это ошибка, напишите тому, кто выдал вам программу.</p>
      <div class="ban-btns"><button class="btn" id="auAgain">Проверить снова</button><button class="btn" id="auOut">Выйти из аккаунта</button></div></div>`, "lock-layer ban");
    layer.querySelector("#auOut").onclick = async () => { await call("/api/auth/logout", "POST").catch(() => {}); lastKey = ""; refresh(); };
    layer.querySelector("#auAgain").onclick = async () => { try { await call("/api/auth/recheck", "POST"); } catch { /* */ } lastKey = ""; busy = false; refresh(); };
  }

  function connectScreen(msg) {
    show(`<div class="auth-card"><img class="au-logo" src="/static/codwe_icon.png" alt="" onerror="this.remove()"><h2>Код подключения</h2>
      <p class="au-sub">Чтобы войти, нужен <b>код подключения</b>. Его выдаёт тот, кто дал вам программу (он начинается с <b>CODWE1.</b>). Вводить его нужно один раз.</p>
      ${msg ? `<div class="au-err">${esc(msg)}</div>` : ""}
      <input id="cnCode" autocomplete="off" spellcheck="false" placeholder="CODWE1.xxxxxxxx…" class="au-in">
      <button class="btn primary au-btn" id="cnGo">Подключиться</button>
      <p class="au-fine">Код не открывает доступ сам по себе: дальше всё равно нужны почта, пароль доступа и согласие с условиями.</p></div>`);
    const q = (id) => layer.querySelector(id);
    const go = async () => { const v = q("#cnCode").value.trim(); if (!v) return; q("#cnGo").disabled = true;
      try { await call("/api/auth/connect", "POST", { code: v }); lastKey = ""; refresh(); } catch (e) { connectScreen(e.message); } };
    q("#cnGo").onclick = go; q("#cnCode").onkeydown = (e) => { if (e.key === "Enter") go(); }; q("#cnCode").focus();
  }

  function accessScreen(msg) {
    show(`<div class="auth-card"><img class="au-logo" src="/static/codwe_icon.png" alt="" onerror="this.remove()"><h2>Пароль доступа</h2>
      <p class="au-sub">Вы вошли как <b>${esc(st && st.email)}</b>. Для работы нужен пароль доступа, его выдаёт администратор.</p>
      ${msg ? `<div class="au-err">${esc(msg)}</div>` : ""}
      <input id="acPw" type="password" autocomplete="off" placeholder="Пароль доступа" class="au-in">
      <button class="btn primary au-btn" id="acGo">Продолжить</button>
      <button class="au-link" id="acOut">Выйти из аккаунта</button></div>`);
    const q = (id) => layer.querySelector(id);
    const go = async () => { const v = q("#acPw").value; if (!v) return; q("#acGo").disabled = true;
      try { await call("/api/auth/access", "POST", { password: v }); lastKey = ""; refresh(); }
      catch (e) { accessScreen(e.message); } };
    q("#acGo").onclick = go; q("#acPw").onkeydown = (e) => { if (e.key === "Enter") go(); }; q("#acPw").focus();
    q("#acOut").onclick = async () => { await call("/api/auth/logout", "POST").catch(() => {}); lastKey = ""; refresh(); };
  }

  function loginScreen(notice) {
    let email = "", sent = false, cool = 0, cd, saved = [];
    call("/api/auth/accounts").then((d) => { saved = (d.accounts || []).filter((e) => e !== d.current); if (saved.length && !sent && layer && layer.querySelector("#auEmail")) draw(); }).catch(() => {});
    const draw = (err = "", info = "") => {
      show(`<div class="auth-card"><img class="au-logo" src="/static/codwe_icon.png" alt="" onerror="this.remove()"><h2>Вход в Codwe AI</h2>
        <p class="au-sub">${sent ? `Мы отправили письмо на <b>${esc(email)}</b>. Нажмите ссылку в письме — окно войдёт само. Если в письме есть код, введите его ниже.` : "Введите почту — пришлём письмо со входом. Пароль не нужен."}</p>
        ${notice && !sent ? `<div class="au-note">${esc(notice)}</div>` : ""}
        ${err ? `<div class="au-err">${esc(err)}</div>` : ""}${info ? `<div class="au-info">${esc(info)}</div>` : ""}
        ${sent ? `<input id="auCode" inputmode="numeric" autocomplete="one-time-code" maxlength="10" placeholder="Код из письма" class="au-in au-code">
          <button class="btn primary au-btn" id="auVerify">Войти</button>
          <button class="au-link" id="auResend" ${cool > 0 ? "disabled" : ""}>${cool > 0 ? `Отправить код ещё раз (${cool} с)` : "Отправить код ещё раз"}</button>
          <button class="au-link" id="auChange">Другая почта</button>`
          : `${saved.length ? `<div class="au-saved"><div class="au-fine">Ваши аккаунты на этом компьютере:</div>${saved.map((e) => `<button class="btn au-btn" data-sw="${esc(e)}">Продолжить как ${esc(e)}</button>`).join("")}<div class="au-fine">или войдите другой почтой:</div></div>` : ""}<input id="auEmail" type="email" autocomplete="email" placeholder="you@example.com" value="${esc(email)}" class="au-in">
          <button class="btn primary au-btn" id="auSend">Получить код</button>`}
        <p class="au-fine">Входя, вы соглашаетесь с условиями использования (они покажутся следующим шагом).</p></div>`);
      const q = (id) => layer.querySelector(id);
      layer.querySelectorAll("[data-sw]").forEach((b) => (b.onclick = async () => { b.disabled = true; try { await call("/api/auth/switch", "POST", { email: b.dataset.sw }); lastKey = ""; refresh(); } catch (e) { draw(e.message); } }));
      if (!sent) {
        const go = async () => { email = q("#auEmail").value.trim(); q("#auSend").disabled = true;
          try { await call("/api/auth/send-code", "POST", { email }); sent = true; cool = 45; tick(); waitLink(); draw("", "Письмо отправлено. Проверьте «Входящие» и «Спам»."); }
          catch (e) { draw(e.message); } };
        q("#auSend").onclick = go; q("#auEmail").onkeydown = (e) => { if (e.key === "Enter") go(); }; q("#auEmail").focus();
      } else {
        const go = async () => { q("#auVerify").disabled = true;
          try { await call("/api/auth/verify", "POST", { email, code: q("#auCode").value }); clearInterval(cd); refresh(); }
          catch (e) { draw(e.message); } };
        q("#auVerify").onclick = go; q("#auCode").onkeydown = (e) => { if (e.key === "Enter") go(); }; q("#auCode").focus();
        q("#auResend").onclick = async () => { try { await call("/api/auth/send-code", "POST", { email }); cool = 45; tick(); draw("", "Новый код отправлен."); } catch (e) { draw(e.message); } };
        q("#auChange").onclick = () => { sent = false; clearInterval(cd); draw(); };
      }
    };
    // пока ждём письмо: если ссылку нажали в другой вкладке/браузере, это окно заметит вход и продолжит само
    let wl; const waitLink = () => { clearInterval(wl); wl = setInterval(() => { if (!layer || layer.hidden || !layer.querySelector("#auCode")) { clearInterval(wl); return; } refresh(); }, 3000); };
    const tick = () => { clearInterval(cd); cd = setInterval(() => { cool = Math.max(0, cool - 1); const b = layer && layer.querySelector("#auResend"); if (b) { b.disabled = cool > 0; b.textContent = cool > 0 ? `Отправить код ещё раз (${cool} с)` : "Отправить код ещё раз"; } if (!cool) clearInterval(cd); }, 1000); };
    draw();
  }

  async function tosScreen(msg) {
    let t;
    try { t = await call("/api/auth/tos"); } catch (e) { return show(`<div class="auth-card"><p class="au-err">${esc(e.message)}</p></div>`); }
    const t0 = Date.now(), need = t.min_seconds + 1;
    show(`<div class="auth-card tos"><h2>Условия использования и конфиденциальность</h2>
      ${msg ? `<div class="au-err">${esc(msg)}</div>` : ""}
      <p class="au-sub">Прочитайте до конца. Кнопка станет доступна через ${t.min_seconds} секунд и после прокрутки вниз.</p>
      <div class="tos-box" id="tosBox">${t.sections.map((s, i) => `<h4>${i + 1}. ${esc(s.title)}</h4><p>${esc(s.text)}</p>`).join("")}<p class="tos-end">— конец условий —</p></div>
      <div class="tos-bar"><i id="tosFill"></i></div>
      <button class="btn primary au-btn" id="tosNext" disabled>Прочитайте условия…</button>
      <div id="tosQuiz" hidden></div></div>`);
    const box = layer.querySelector("#tosBox"), next = layer.querySelector("#tosNext"), fill = layer.querySelector("#tosFill");
    let scrolled = false;
    const upd = () => {
      const left = Math.max(0, need - (Date.now() - t0) / 1000);
      fill.style.width = Math.min(100, (1 - left / need) * 100) + "%";
      scrolled = scrolled || box.scrollTop + box.clientHeight >= box.scrollHeight - 6;
      next.disabled = !(left <= 0 && scrolled);
      next.textContent = left > 0 ? `Осталось ${Math.ceil(left)} с…` : scrolled ? `Я прочитал(а) — ответить на ${t.quiz.length} вопроса` : "Прокрутите условия до конца";
    };
    box.onscroll = upd; clearInterval(timer); timer = setInterval(upd, 250); upd();
    next.onclick = () => {
      clearInterval(timer);
      const qz = layer.querySelector("#tosQuiz");
      next.hidden = true; qz.hidden = false;
      qz.innerHTML = t.quiz.map((q) => `<div class="qz"><b>${esc(q.q)}</b>${q.opts.map((o) => `<label><input type="radio" name="q_${esc(q.id)}" value="${esc(o.id)}"> ${esc(o.text)}</label>`).join("")}</div>`).join("")
        + `<button class="btn primary au-btn" id="tosOk" disabled>Принимаю условия</button>`;
      const ok = qz.querySelector("#tosOk");
      qz.onchange = () => { ok.disabled = !t.quiz.every((q) => qz.querySelector(`input[name="q_${q.id}"]:checked`)); };
      ok.onclick = async () => {
        const answers = {}; t.quiz.forEach((q) => { answers[q.id] = qz.querySelector(`input[name="q_${q.id}"]:checked`).value; });
        ok.disabled = true;
        try { await call("/api/auth/tos/accept", "POST", { nonce: t.nonce, answers }); refresh(); }
        catch (e) { if (e.message.startsWith("restart:")) tosScreen(e.message.slice(8)); else { show(`<div class="auth-card"><p class="au-err">${esc(e.message)}</p><button class="btn" onclick="location.reload()">Назад</button></div>`); } }
      };
    };
  }

  function adminButton() { const old = document.getElementById("adminBtn"); if (old) old.remove(); }

  // вход по ссылке из письма: облако возвращает пользователя на этот адрес с токеном после «#»
  async function takeLinkSession() {
    const h = location.hash.replace(/^#/, "");
    if (!/(access_token|error)=/.test(h)) return;
    const p = new URLSearchParams(h);
    history.replaceState(null, "", location.pathname + location.search);
    if (p.get("error")) { linkErr = (p.get("error_description") || "Ссылка недействительна").replace(/\+/g, " ") + ". Запросите новое письмо."; return; }
    try { await call("/api/auth/session", "POST", { access_token: p.get("access_token"), refresh_token: p.get("refresh_token") || "" }); }
    catch (e) { linkErr = e.message; }
  }
  let linkErr = "";

  // ─────── главный цикл ───────
  async function refresh() {
    if (busy) return; busy = true;
    try {
      st = await call("/api/auth/state");
      const key = [st.tampered, st.needs_connect, st.banned, st.ban_reason, st.logged_in, st.access_ok, st.tos_ok, st.email].join("|");
      adminButton();
      if (window.codweDevAllow) window.codweDevAllow(st.logged_in && st.access_ok && st.tos_ok && !st.tampered && !st.banned && (st.perms || []).includes("dev.console"));
      if (window.codweAuth && window.codweAuth.onState) window.codweAuth.onState(st);
      if (st.tampered) { if (key !== lastKey) lockScreen(st.tamper_reason); }
      else if (st.needs_connect) { if (key !== lastKey) connectScreen(); }
      else if (st.banned) { if (key !== lastKey) bannedScreen(st); }
      else if (!st.logged_in) { if (key !== lastKey) { loginScreen(linkErr || st.notice); linkErr = ""; } }
      else if (!st.access_ok) { if (key !== lastKey) accessScreen(); }
      else if (!st.tos_ok) { if (key !== lastKey) tosScreen(); }
      else { if (layer && !layer.hidden) hide(); if (lastKey && lastKey !== key) location.reload(); }
      lastKey = key;
    } catch { /* сервер перезапускается */ }
    busy = false;
  }
  window.codweAuth = { refresh, state: () => st, admin: () => window.codweAdmin && window.codweAdmin.open(), call, esc };
  if (!window.codweGuard) { lockScreen("Страж безопасности не загружен: страница изменена."); return; }  // без стража интерфейс не запускается
  takeLinkSession().then(refresh);
  setInterval(refresh, 20000);
})();
