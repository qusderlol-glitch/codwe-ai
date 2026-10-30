// Живой фон пустого чата: полярное сияние из «цифр и кусочков кода». Время от времени сияние рвётся по шву:
// частицы расходятся, а из разрыва вылетают обрывки кода. Рисуется на canvas, останавливается, когда чат не пустой.
(() => {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const COLORS = ["#c6f432", "#3ee6c4", "#d58bff", "#ffb454", "#5fd0ff"];
  const RIBBON_COLORS = [[0, 1], [1, 4], [2, 0]];           // какие цвета у каждой ленты сияния
  const GLY = ["0", "1", "0", "1", "{", "}", "[", "]", "(", ")", "<", ">", "/", "=", "+", "*", "#", ";", "$", "&", "|", "~", "=>", "</>", "fn", "0x", "//", "{}", "[]", "if", "::", "&&"];
  const SNIPPETS = [
    ["async function draw(prompt) {", "  const img = await zimage(prompt);", "  return img.save();", "}"],
    ["for (let i = 0; i < 8; i++) {", "  latent = denoise(latent, i);", "}"],
    ["model.load('qwen3.5:9b');", "chat.stream(msg); // 0xA7F3"],
    ["01101011 01100001", "10100110 11100010", "</aurora>"],
    ["if (glow > 0.8) {", "  sky.paint('lime', 'aqua');", "}"],
    ["const ideas = await mind.think();", "ideas.map(i => i.render());"],
    ["fn tear(seam: Line) -> Shards {", "  split(seam).scatter()", "}"],
  ];
  let cv = null, ctx = null, host = null, W = 0, H = 0, dpr = 1, raf = 0, running = false, ro = null;
  let parts = [], atlas = null, ray = [], shards = [], tears = [], mouse = { x: -999, y: -999 }, t0 = 0, nextTear = 0, last = 0;

  const rnd = (a, b) => a + Math.random() * (b - a);
  const gauss = () => (Math.random() + Math.random() + Math.random() - 1.5) / 1.5;

  function buildAtlas() {
    atlas = COLORS.map((c) => GLY.map((g) => {
      const s = document.createElement("canvas"), x = s.getContext("2d");
      const fs = 15 * dpr; x.font = `600 ${fs}px Consolas, "Cascadia Code", monospace`;
      s.width = Math.ceil(x.measureText(g).width) + 4; s.height = Math.ceil(fs * 1.35);
      x.font = `600 ${fs}px Consolas, "Cascadia Code", monospace`; x.textBaseline = "top"; x.fillStyle = c; x.shadowColor = c; x.shadowBlur = 5 * dpr; x.fillText(g, 2, 2);
      return s;
    }));
    ray = COLORS.map((c) => {
      const s = document.createElement("canvas"); s.width = 4; s.height = 160;
      const x = s.getContext("2d"), g = x.createLinearGradient(0, 0, 0, 160);
      g.addColorStop(0, c + "00"); g.addColorStop(0.08, c + "cc"); g.addColorStop(1, c + "00"); x.fillStyle = g; x.fillRect(0, 0, 4, 160);
      return s;
    });
  }

  function seed() {
    const n = Math.max(260, Math.min(1500, Math.round(W * H / 950)));
    parts = Array.from({ length: n }, () => {
      const r = Math.min(2, Math.floor(Math.random() * 3)), rc = RIBBON_COLORS[r];
      return { r, u: Math.random(), v: gauss() * 120, g: Math.floor(Math.random() * GLY.length), c: Math.random() < 0.72 ? rc[0] : rc[1], sp: rnd(0.6, 1.6), ph: rnd(0, 6.28), ox: 0, oy: 0, sz: rnd(0.8, 1.3) };
    });
  }

  const cl = (r, x, t) => H * (0.27 + 0.13 * r) + Math.sin(x * 0.0042 * (1 + 0.3 * r) + t * 0.00036 * (1 + r * 0.4) + r * 2) * H * 0.085 + Math.sin(x * 0.011 + t * 0.0007 + r) * H * 0.028;

  function newTear(now) {
    const x0 = rnd(W * 0.18, W * 0.82), y0 = rnd(H * 0.2, H * 0.45), len = H * rnd(0.55, 0.8), slope = rnd(-0.38, 0.38), seedn = rnd(0, 9);
    const tear = { x0, y0, len, slope, seed: seedn, t: now, dur: 3200 };
    tears.push(tear);
    const count = 6 + Math.floor(Math.random() * 3);
    for (let i = 0; i < count; i++) {
      const y = y0 + rnd(0, len), x = x0 + (y - y0) * slope, side = i % 2 ? 1 : -1, lines = SNIPPETS[Math.floor(Math.random() * SNIPPETS.length)];
      const w = Math.max(...lines.map((l) => l.length)) * 7.2 + 20, h = lines.length * 17 + 14;
      shards.push({ x, y, vx: side * rnd(0.35, 1.0), vy: rnd(-0.45, 0.25), rot: rnd(-0.15, 0.15), vr: side * rnd(0.0002, 0.0007), lines, w, h, t: now + rnd(0, 700), life: rnd(3600, 5200), jag: Array.from({ length: 8 }, () => rnd(-4, 4)), c: Math.floor(rnd(0, 5)) });
    }
  }
  const jag = (tear, y) => Math.sin(y * 0.09 + tear.seed) * 7 + Math.sin(y * 0.23 + tear.seed * 2) * 4;
  const seamX = (tear, y) => tear.x0 + (y - tear.y0) * tear.slope + jag(tear, y);
  const ease = (k) => Math.pow(Math.sin(Math.PI * Math.min(1, Math.max(0, k))), 1.4);

  function frame(now) {
    if (!running) return;
    raf = requestAnimationFrame(frame);
    if (now - last < 30 || !W || !H) return;                      // не чаще ~33 кадров в секунду: хватает и бережёт батарею
    last = now; const t = now - t0;
    if (now > nextTear) { newTear(now); nextTear = now + rnd(6200, 9000); }
    tears = tears.filter((tr) => now - tr.t < tr.dur);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);

    // лучи сияния (вертикальные «шторы»)
    ctx.globalCompositeOperation = "lighter";
    for (let r = 0; r < 3; r++) {
      const rc = RIBBON_COLORS[r];
      for (let x = -6; x < W + 12; x += 11) {
        const inten = 0.5 + 0.5 * Math.sin(x * 0.013 + t * 0.0013 + r * 1.7), y = cl(r, x, t);
        if (inten < 0.18) continue;
        ctx.globalAlpha = 0.2 * inten * (0.6 + 0.4 * Math.sin(x * 0.05 - t * 0.002));
        ctx.drawImage(ray[rc[(x / 11 | 0) % 2 ? 0 : 1]], x, y - 14, 11, 70 + 90 * inten);
      }
    }
    ctx.globalCompositeOperation = "source-over";

    // «цифры и код»
    for (const p of parts) {
      let x = ((p.u * (W + 60) + t * p.sp * 0.018) % (W + 60)) - 30;
      let y = cl(p.r, x, t) + p.v * (0.8 + 0.4 * Math.sin(t * 0.0009 + p.ph));
      let tx = 0, ty = 0;
      const mdx = x - mouse.x, mdy = y - mouse.y, md = Math.hypot(mdx, mdy);
      if (md < 130) { const f = (1 - md / 130) * 26; tx += (mdx / (md || 1)) * f; ty += (mdy / (md || 1)) * f; }
      for (const tr of tears) {
        const e = ease((now - tr.t) / tr.dur);
        if (y < tr.y0 - 20 || y > tr.y0 + tr.len + 20) continue;
        const d = x - seamX(tr, y), band = 150;
        if (Math.abs(d) < band) { const k = (1 - Math.abs(d) / band) * e, s = d < 0 ? -1 : 1; tx += s * k * 70; ty += s * k * 26; }
      }
      p.ox += (tx - p.ox) * 0.14; p.oy += (ty - p.oy) * 0.14;
      x += p.ox; y += p.oy;
      const a = 1.0 * Math.pow(Math.max(0, 1 - Math.abs(p.v) / 175), 1.3) * (0.5 + 0.5 * Math.sin(x * 0.018 + t * 0.0021 + p.ph));
      if (a < 0.04) continue;
      const sp = atlas[p.c][p.g];
      ctx.globalAlpha = a; ctx.drawImage(sp, x, y, sp.width / dpr * p.sz, sp.height / dpr * p.sz);
    }

    // шов разрыва
    for (const tr of tears) {
      const e = ease((now - tr.t) / tr.dur);
      const g = ctx.createLinearGradient(0, tr.y0, 0, tr.y0 + tr.len); g.addColorStop(0, "#c6f432"); g.addColorStop(0.5, "#3ee6c4"); g.addColorStop(1, "#d58bff");
      ctx.globalAlpha = e * 0.9; ctx.strokeStyle = g; ctx.lineWidth = 1.6; ctx.shadowColor = "#3ee6c4"; ctx.shadowBlur = 14; ctx.beginPath();
      for (let y = tr.y0; y <= tr.y0 + tr.len; y += 6) { const x = seamX(tr, y); y === tr.y0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
      ctx.stroke(); ctx.shadowBlur = 0;
    }

    // обрывки кода
    shards = shards.filter((s) => now - s.t < s.life);
    for (const s of shards) {
      const age = now - s.t; if (age < 0) continue;
      const k = age / s.life, a = Math.min(1, age / 400) * (1 - Math.pow(k, 2.2));
      s.x += s.vx * 0.4; s.y += s.vy * 0.4; s.vx *= 0.992; s.vy -= 0.0016; s.rot += s.vr * 30;
      ctx.save(); ctx.translate(s.x, s.y); ctx.rotate(s.rot); ctx.globalAlpha = a * 0.92;
      ctx.beginPath(); ctx.moveTo(0, s.jag[0]);
      for (let i = 1; i < 8; i++) ctx.lineTo(s.w * i / 7, s.jag[i]);
      ctx.lineTo(s.w, s.h + s.jag[3]); for (let i = 7; i >= 0; i--) ctx.lineTo(s.w * i / 7, s.h + s.jag[(i + 2) % 8]);
      ctx.closePath(); ctx.fillStyle = "rgba(6,16,12,.66)"; ctx.fill(); ctx.strokeStyle = COLORS[s.c]; ctx.lineWidth = 1; ctx.globalAlpha = a * 0.7; ctx.stroke();
      ctx.globalAlpha = a; ctx.font = "12px Consolas, 'Cascadia Code', monospace"; ctx.textBaseline = "top";
      s.lines.forEach((l, i) => { ctx.fillStyle = i % 2 ? "#bfeed9" : COLORS[s.c]; ctx.fillText(l, 10, 8 + i * 17); });
      ctx.restore();
    }
    ctx.globalAlpha = 1;
  }

  function resize() {
    if (!host || !cv) return;
    dpr = Math.min(1.5, window.devicePixelRatio || 1);
    W = host.clientWidth; H = host.clientHeight;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    buildAtlas(); seed();
    if (!running && !cv.hidden) { t0 = performance.now(); running = true; raf = requestAnimationFrame(frame); }
  }

  function mount() {
    if (cv) return;
    host = document.querySelector(".chat-main"); if (!host) return;
    cv = document.createElement("canvas"); cv.className = "aurora-cv"; cv.setAttribute("aria-hidden", "true"); cv.hidden = true;
    host.prepend(cv); ctx = cv.getContext("2d");
    ro = new ResizeObserver(() => { if (!cv.hidden) resize(); }); ro.observe(host);
    host.addEventListener("pointermove", (e) => { const r = host.getBoundingClientRect(); mouse.x = e.clientX - r.left; mouse.y = e.clientY - r.top; });
    host.addEventListener("pointerleave", () => { mouse.x = mouse.y = -999; });
    document.addEventListener("visibilitychange", () => { if (document.hidden) { cancelAnimationFrame(raf); running = false; } else if (cv && !cv.hidden) start(); });
  }

  function start() {
    mount(); if (!cv) return;
    cv.hidden = false;
    dpr = Math.min(1.5, window.devicePixelRatio || 1); W = host.clientWidth; H = host.clientHeight;
    if (!W || !H) { setTimeout(() => { if (cv && !cv.hidden) start(); }, 250); return; }
    if (!atlas || cv.width !== Math.round(W * dpr)) resize();
    if (reduce) { t0 = performance.now() - 4000; running = true; frame(performance.now() + 100); running = false; return; }   // без анимации: один красивый кадр
    if (running) return;
    t0 = performance.now(); nextTear = t0 + 1600; running = true; raf = requestAnimationFrame(frame);
  }
  function stop() { running = false; cancelAnimationFrame(raf); if (cv) { cv.hidden = true; if (ctx) ctx.clearRect(0, 0, cv.width, cv.height); } tears = []; shards = []; }

  window.codweAurora = { start, stop };
})();
