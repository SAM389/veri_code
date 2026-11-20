// Hero: a robot hand (the agent) and a human hand (you) drawn as small pixels, reaching toward a
// VeriCode terminal. Hand shapes and shading come from hands/*.png, two small maps made from a
// photo by web/tools/make_hand_maps.py (gray = brightness, alpha = coverage).
"use strict";

(() => {
  const stage = document.getElementById("hero-stage");
  const canvas = document.getElementById("hero-canvas");
  const term = document.getElementById("hero-term");
  if (!stage || !canvas || !term) return;

  const ctx = canvas.getContext("2d");
  // ?static draws the finished frame at once (also handy for screenshots)
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
    new URLSearchParams(location.search).has("static");
  const MIN_CELL = 1.5; // smallest pixel step we draw, in CSS px
  const ARM_OFF = { robot: 260, human: 200 }; // photo px of forearm allowed off-screen (desktop)
  const ASSEMBLE_MS = 1300;
  const COLORS = { robot: [214, 224, 240], human: [255, 244, 232] };

  let maps = null; // { factor, robot: {w,h,tip,data,top}, human: {...} }
  let W = 0;
  let H = 0;
  let sizeDev = 2; // drawn cell size in device px
  let points = null; // typed arrays, see build()
  let stars = [];
  let tips = { left: [0, 0], right: [0, 0] };
  let built = 0;
  let verdict = "idle"; // set by the terminal script: idle | block | pass
  // Hands are written straight into an ImageData buffer: tens of thousands of fillRect calls
  // per frame are too slow. The background is pure black, so "brightness" is just a darker
  // opaque color.
  let frame = null;
  let pix = null; // Uint32Array view of frame (little-endian ABGR)
  let dprNow = 1;

  function pack(rgb, b) {
    const r = Math.round(rgb[0] * b);
    const g = Math.round(rgb[1] * b);
    const bl = Math.round(rgb[2] * b);
    return ((255 << 24) | (bl << 16) | (g << 8) | r) >>> 0;
  }

  // ---------------------------------------------------------------- load maps
  async function loadImage(src) {
    const img = new Image();
    img.src = src;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width;
    c.height = img.height;
    const cc = c.getContext("2d", { willReadFrequently: true });
    cc.drawImage(img, 0, 0);
    return cc.getImageData(0, 0, img.width, img.height).data;
  }

  async function loadMaps() {
    const meta = await (await fetch("hands/hands.json")).json();
    const [robot, human] = await Promise.all([loadImage("hands/robot.png"), loadImage("hands/human.png")]);
    meta.robot.data = robot;
    meta.human.data = human;
    return meta;
  }

  // ---------------------------------------------------------------- layout
  function layout() {
    const r = stage.getBoundingClientRect();
    const t = term.getBoundingClientRect();
    const narrow = r.width < 760;
    const f = maps.factor;
    const R = maps.robot;
    const Hm = maps.human;
    const gap = narrow ? 12 : t.width / 2 + 16;
    const side = r.width / 2 - gap;

    // Big hands: part of each forearm may run off the screen edge (it fades out anyway).
    let s = Math.min(
      side / (R.tip[0] * f - ARM_OFF.robot),
      side / ((Hm.w - Hm.tip[0]) * f - ARM_OFF.human),
    );
    const robotTipAbs = R.top + R.tip[1] * f;
    const humanTipAbs = Hm.top + Hm.tip[1] * f;
    const offset = humanTipAbs - robotTipAbs; // human fingertip sits lower in the photo
    const above = Math.max(R.tip[1] * f, Hm.tip[1] * f + offset);
    const below = Math.max((R.h - R.tip[1]) * f, (Hm.h - Hm.tip[1]) * f + offset);
    let cy;
    if (narrow) {
      // hands meet above the terminal; let the arms run off the screen so the hands stay big
      s = side / (R.tip[0] * f * 0.5);
      cy = 6 + above * s;
      const termTop = Math.round(cy + below * s + 14);
      term.style.top = `${termTop}px`;
      stage.style.minHeight = `${termTop + t.height + 44}px`;
    } else {
      // as large as the height allows (the human wrist may fade out past the bottom edge),
      // then the terminal is centred between the fingertips
      s = Math.min(s, (r.height * 1.14) / (above + below));
      cy = 8 + above * s;
      term.style.top = `${Math.round(cy + (offset * s) / 2)}px`;
    }
    return { r, f, s, gap, cy, offset };
  }

  // ---------------------------------------------------------------- build points
  function build() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const L = layout();
    W = L.r.width;
    H = L.r.height;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Snap the grid to whole device pixels so every cell is the same size (no moire lines).
    const stride = Math.max(1, Math.ceil(MIN_CELL / (L.s * L.f)));
    const stepDev = Math.max(2, Math.round(L.s * L.f * stride * dpr));
    const step = stepDev / dpr; // CSS px between drawn cells
    const cell = step / stride; // CSS px per map pixel
    sizeDev = stepDev >= 3 ? stepDev - 1 : stepDev; // 1 device px gap = the pixel-grid look

    const xs = [];
    const ys = [];
    const cols = [];
    const cx = W / 2;
    const place = (m, name, tipX, tipY) => {
      for (let my = 0; my < m.h; my += stride) {
        for (let mx = 0; mx < m.w; mx += stride) {
          const i = (my * m.w + mx) * 4;
          const a = m.data[i + 3] / 255;
          if (a < 0.35) continue;
          const b = (m.data[i] / 255) * Math.min(1, a * 1.15);
          const x = tipX + (mx - m.tip[0]) * cell;
          const y = tipY + (my - m.tip[1]) * cell;
          if (x < -step || x > W + step || y < -step || y > H + step) continue;
          xs.push(x);
          ys.push(y);
          cols.push(pack(COLORS[name], b));
        }
      }
    };
    const snap = (v) => Math.round(v * dpr) / dpr;
    const robotTip = [snap(cx - L.gap), snap(L.cy)];
    const humanTip = [snap(cx + L.gap), snap(L.cy + L.offset * L.s)];
    place(maps.robot, "robot", ...robotTip);
    place(maps.human, "human", ...humanTip);
    tips = { left: robotTip, right: humanTip };

    const n = xs.length;
    points = {
      n,
      x: Float32Array.from(xs),
      y: Float32Array.from(ys),
      sx: Float32Array.from({ length: n }, () => Math.random() * W),
      sy: Float32Array.from({ length: n }, () => Math.random() * H),
      delay: Float32Array.from(xs, (x) => Math.random() * 500 + (Math.abs(x - cx) / W) * 700),
      ox: new Float32Array(n),
      oy: new Float32Array(n),
      col: Uint32Array.from(cols),
    };

    const count = Math.round((W * H) / 2400);
    stars = Array.from({ length: count }, () => ({
      x: Math.random() * W,
      y: Math.random() * H,
      b: Math.random() * 0.2 + 0.03,
      phase: Math.random() * 6,
      speed: Math.random() * 1.5 + 0.3,
    }));
    frame = ctx.createImageData(canvas.width, canvas.height);
    pix = new Uint32Array(frame.data.buffer);
    settledLayer = null;
    basePix = null;
    dirty = null;
    dprNow = dpr;
    built = performance.now();
    if (reduceMotion) draw(built + 10_000);
  }

  // ---------------------------------------------------------------- render
  const pointer = { x: -1e4, y: -1e4, active: false };
  stage.addEventListener("pointermove", (e) => {
    const r = stage.getBoundingClientRect();
    pointer.x = e.clientX - r.left;
    pointer.y = e.clientY - r.top;
    pointer.active = true;
  });
  stage.addEventListener("pointerleave", () => {
    pointer.x = pointer.y = -1e4;
    pointer.active = false;
  });

  const ease = (t) => 1 - Math.pow(1 - t, 3);
  const REPEL_R = 70; // CSS px around the pointer that pushes pixels away
  const REPEL_PUSH = 18;
  const PAD = 26; // > REPEL_PUSH + one cell: how far a pushed pixel can land from its home

  function plot(i, x, y) {
    const cw = frame.width;
    const sz = sizeDev;
    const px = Math.round(x * dprNow);
    const py = Math.round(y * dprNow);
    if (px < 0 || py < 0 || px + sz > cw || py + sz > frame.height) return;
    const c = points.col[i];
    let o = py * cw + px;
    for (let yy = 0; yy < sz; yy++, o += cw) {
      for (let xx = 0; xx < sz; xx++) pix[o + xx] = c;
    }
  }

  // Full redraw: every hand pixel, on its way in from a random spot while assembling.
  function drawAll(now) {
    const p = points;
    const assembling = !reduceMotion && now - built < ASSEMBLE_MS + 1300;
    pix.fill(0xff000000);
    for (let i = 0; i < p.n; i++) {
      let x = p.x[i];
      let y = p.y[i];
      if (assembling) {
        const k = ease(Math.min(1, Math.max(0, (now - built - p.delay[i]) / ASSEMBLE_MS)));
        x = p.sx[i] + (x - p.sx[i]) * k;
        y = p.sy[i] + (y - p.sy[i]) * k;
      }
      plot(i, x, y);
    }
  }

  // Hover: start from the settled picture and only redo the box around the pointer plus
  // wherever pixels are still drifting back. Returns false once nothing is displaced.
  let dirty = null; // {x0, y0, x1, y1} in CSS px
  function drawHover() {
    const p = points;
    let x0 = Infinity;
    let y0 = Infinity;
    let x1 = -Infinity;
    let y1 = -Infinity;
    if (pointer.active) {
      x0 = pointer.x - REPEL_R - PAD;
      y0 = pointer.y - REPEL_R - PAD;
      x1 = pointer.x + REPEL_R + PAD;
      y1 = pointer.y + REPEL_R + PAD;
    }
    if (dirty) {
      x0 = Math.min(x0, dirty.x0);
      y0 = Math.min(y0, dirty.y0);
      x1 = Math.max(x1, dirty.x1);
      y1 = Math.max(y1, dirty.y1);
    }
    if (x0 > x1) return false;

    pix.set(basePix);
    const cw = frame.width;
    const px0 = Math.max(0, Math.floor(x0 * dprNow));
    const px1 = Math.min(cw, Math.ceil(x1 * dprNow));
    const py0 = Math.max(0, Math.floor(y0 * dprNow));
    const py1 = Math.min(frame.height, Math.ceil(y1 * dprNow));
    for (let y = py0; y < py1; y++) pix.fill(0xff000000, y * cw + px0, y * cw + px1);

    let next = null;
    const R2 = REPEL_R * REPEL_R;
    for (let i = 0; i < p.n; i++) {
      const bx = p.x[i];
      const by = p.y[i];
      if (bx < x0 - PAD || bx > x1 + PAD || by < y0 - PAD || by > y1 + PAD) continue;
      const dx = bx - pointer.x;
      const dy = by - pointer.y;
      const d2 = dx * dx + dy * dy;
      let tx = 0;
      let ty = 0;
      if (pointer.active && d2 < R2) {
        const dist = Math.sqrt(d2) || 1;
        const push = (1 - dist / REPEL_R) * REPEL_PUSH;
        tx = (dx / dist) * push;
        ty = (dy / dist) * push;
      }
      p.ox[i] += (tx - p.ox[i]) * 0.14;
      p.oy[i] += (ty - p.oy[i]) * 0.14;
      if (Math.abs(p.ox[i]) + Math.abs(p.oy[i]) < 0.05) {
        p.ox[i] = 0;
        p.oy[i] = 0;
      } else {
        next = next || { x0: bx, y0: by, x1: bx, y1: by };
        next.x0 = Math.min(next.x0, bx - PAD);
        next.y0 = Math.min(next.y0, by - PAD);
        next.x1 = Math.max(next.x1, bx + PAD);
        next.y1 = Math.max(next.y1, by + PAD);
      }
      plot(i, bx + p.ox[i], by + p.oy[i]);
    }
    dirty = next;
    return true;
  }

  let settledLayer = null; // canvas of the settled hands; drawImage is cheaper than redrawing
  let basePix = null; // the same picture as a pixel buffer, the starting point for hover frames

  function draw(now) {
    const t = (now - built) / 1000;
    const assembling = !reduceMotion && now - built < ASSEMBLE_MS + 1300;

    if (assembling || !settledLayer) {
      drawAll(now);
      ctx.putImageData(frame, 0, 0);
      if (!assembling) {
        basePix = pix.slice();
        dirty = null;
        settledLayer = document.createElement("canvas");
        settledLayer.width = frame.width;
        settledLayer.height = frame.height;
        settledLayer.getContext("2d").putImageData(frame, 0, 0);
      }
    } else if (!reduceMotion && (pointer.active || dirty) && drawHover()) {
      ctx.putImageData(frame, 0, 0);
    } else {
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.drawImage(settledLayer, 0, 0);
      ctx.restore();
    }

    for (const s of stars) {
      const a = s.b * (0.55 + 0.45 * Math.sin(t * s.speed + s.phase));
      ctx.fillStyle = `rgba(255,255,255,${a.toFixed(3)})`;
      ctx.fillRect(s.x, s.y, 1.5, 1.5);
    }

    // data flowing: the agent's code into the terminal, the verdict out to the human
    const fk = reduceMotion ? 0 : Math.min(1, Math.max(0, (now - built - ASSEMBLE_MS - 400) / 600));
    if (fk > 0) {
      const flow = (from, to, rgb, n, speed) => {
        for (let i = 0; i < n; i++) {
          const f = (t * speed + i / n) % 1;
          const x = from[0] + (to[0] - from[0]) * f;
          const y = from[1] + (to[1] - from[1]) * f;
          ctx.fillStyle = `rgba(${rgb},${(Math.sin(f * Math.PI) * fk).toFixed(3)})`;
          ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
        }
      };
      const r = term.getBoundingClientRect();
      const sr = stage.getBoundingClientRect();
      const termL = r.left - sr.left;
      const termR = r.right - sr.left;
      if (termL > tips.left[0] + 8) {
        flow([tips.left[0] + 6, tips.left[1]], [termL, tips.left[1]], "255,255,255", 3, 0.5);
        const out = verdict === "pass" ? "74,222,128" : verdict === "block" ? "248,113,113" : "255,255,255";
        flow([termR, tips.right[1]], [tips.right[0] - 6, tips.right[1]], out, 3, 0.5);
      }
    }
  }

  function loop(now) {
    draw(now);
    requestAnimationFrame(loop);
  }

  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => maps && build(), 150);
  });

  // ---------------------------------------------------------------- terminal script
  // Output mirrors what `vericode check` really prints (shortened).
  const SCRIPT = [
    { type: "cmd", text: "vericode check" },
    { type: "out", cls: "t-block", text: "BLOCK main.py:2  package_exists  fastapi-auth-helpers", verdict: "block" },
    { type: "out", cls: "t-dim", text: "      Not found on PyPI (HTTP 404)" },
    { type: "out", text: "Verdict: BLOCK  (1 block, 0 warn)" },
    { type: "pause", ms: 1200 },
    { type: "out", cls: "t-comment", text: "# agent removes the made-up import" },
    { type: "cmd", text: "vericode check" },
    { type: "out", cls: "t-pass", text: "Verdict: PASS  (0 block, 0 warn)", verdict: "pass" },
    { type: "pause", ms: 3800 },
  ];
  const body = term.querySelector(".term-body");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function line(cls) {
    const d = document.createElement("div");
    d.className = "term-line " + (cls || "");
    body.append(d);
    return d;
  }

  async function runScript() {
    for (;;) {
      body.replaceChildren();
      verdict = "idle";
      for (const step of SCRIPT) {
        if (step.type === "pause") {
          await sleep(step.ms);
        } else if (step.type === "cmd") {
          const d = line("t-cmd");
          d.append(Object.assign(document.createElement("span"), { className: "t-prompt", textContent: "$ " }));
          const txt = document.createElement("span");
          d.append(txt);
          const caret = Object.assign(document.createElement("span"), { className: "t-caret" });
          d.append(caret);
          await sleep(500);
          for (const ch of step.text) {
            txt.textContent += ch;
            await sleep(55 + Math.random() * 45);
          }
          await sleep(350);
          caret.remove();
        } else {
          await sleep(260);
          line(step.cls).textContent = step.text;
          if (step.verdict) verdict = step.verdict;
        }
      }
    }
  }

  function staticScript() {
    for (const step of SCRIPT) {
      if (step.type === "cmd") line("t-cmd").textContent = "$ " + step.text;
      else if (step.type === "out") line(step.cls).textContent = step.text;
    }
    verdict = "pass";
  }

  async function start() {
    if (reduceMotion) staticScript();
    else runScript();
    try {
      if (document.fonts && document.fonts.ready) await document.fonts.ready;
      maps = await loadMaps();
    } catch (err) {
      console.error("hero: could not load hand maps", err);
      return;
    }
    build();
    if (!reduceMotion) requestAnimationFrame(loop);
  }

  start();
})();
