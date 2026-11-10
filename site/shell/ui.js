// curryplan 页面通用交互：评论（选中实体，文字或手绘）与提问（AI 讲解，流式）。
// 用法：页面引入 /ui.css 与 /ui.js。可评论的实体：带 data-c 的元素，或图、表格行、段落、标题、列表项。
(function () {
  "use strict";
  const PAGE = (location.pathname.replace(/^\/+|\/+$/g, "") || "home").toLowerCase().replace(/[^a-z0-9_/-]/g, "-");
  const PICK = "[data-c], figure, .card, img, video, svg.chart, svg.anim, tr, p, li, h1, h2, h3, pre";
  const esc = (s) => String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/^#+ (.*)$/gm, "<b>$1</b>").replace(/^[-*] (.*)$/gm, "• $1").replace(/\n/g, "<br>");
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

  function anchorOf(node) {
    if (node.dataset && node.dataset.c) return "c:" + node.dataset.c;
    if (node.id) return "id:" + node.id;
    const path = [];
    for (let n = node; n && n !== document.body; n = n.parentElement) {
      let i = 0; for (let s = n; (s = s.previousElementSibling);) i++;
      path.unshift(n.tagName.toLowerCase() + ":" + i);
      if (n.id) { path[0] = "#" + n.id; break; }
    }
    return "p:" + path.join(">");
  }
  function findAnchor(a) {
    if (a.startsWith("c:")) return document.querySelector('[data-c="' + CSS.escape(a.slice(2)) + '"]');
    if (a.startsWith("id:")) return document.getElementById(a.slice(3));
    let n = document.body;
    for (const step of a.slice(2).split(">")) {
      if (step.startsWith("#")) { n = document.getElementById(step.slice(1)); }
      else { const i = +step.split(":")[1]; n = n && n.children[i]; }
      if (!n) return null;
    }
    return n;
  }
  const labelOf = (n) => {
    const t = (n.getAttribute("aria-label") || n.getAttribute("alt") || n.innerText || "").trim().replace(/\s+/g, " ");
    return t.slice(0, 40) || n.tagName.toLowerCase();
  };

  // ---------- 外壳 ----------
  const dock = el("div", "ui-dock");
  const bAsk = el("button", "ui-btn", "问"), bCom = el("button", "ui-btn", "评");
  bAsk.type = bCom.type = "button";
  dock.append(bCom, bAsk);
  const layer = el("div", "ui-pins");
  const sheet = el("div", "ui-sheet"); sheet.hidden = true;
  const hint = el("div", "ui-hint", '<span></span><button type=button class="ui-whole" hidden>问整页</button><button type=button class="ui-cancel">取消</button>'); hint.hidden = true;
  document.body.append(layer, dock, sheet, hint);
  const closeSheet = () => { sheet.hidden = true; sheet.innerHTML = ""; document.querySelectorAll(".ui-focus").forEach((n) => n.classList.remove("ui-focus")); };

  // ---------- 评论 ----------
  let comments = [];
  const api = (p, body) => fetch("/api/" + p + "/" + PAGE, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : { cache: "no-store" }).then((r) => r.json());
  const load = () => api("c").then((c) => { comments = c || []; pins(); }).catch(() => {});
  function pins() {
    layer.innerHTML = "";
    const by = {};
    comments.forEach((c) => (by[c.anchor] = by[c.anchor] || []).push(c));
    Object.entries(by).forEach(([a, list]) => {
      const n = findAnchor(a); if (!n) return;
      const r = n.getBoundingClientRect();
      const pin = el("button", "ui-pin", String(list.length)); pin.type = "button";
      pin.style.top = r.top + window.scrollY - 8 + "px";
      pin.style.left = Math.min(r.right + window.scrollX - 10, document.documentElement.clientWidth - 30) + "px";
      pin.onclick = () => openThread(n, a);
      layer.append(pin);
    });
  }
  let raf = 0;
  window.addEventListener("resize", () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(pins); });

  let pickFor = "com";
  function startPick(mode) {
    pickFor = mode; closeSheet();
    hint.querySelector("span").textContent = mode === "ask" ? "点一个想问的地方" : "点一个想评论的地方";
    hint.querySelector(".ui-whole").hidden = mode !== "ask";
    document.body.classList.add("ui-picking"); hint.hidden = false;
  }
  function stopPick() { document.body.classList.remove("ui-picking"); hint.hidden = true; }
  const picking = () => document.body.classList.contains("ui-picking");
  hint.querySelector(".ui-cancel").onclick = stopPick;
  hint.querySelector(".ui-whole").onclick = () => { stopPick(); openAsk(null); };
  document.addEventListener("click", (e) => {
    if (!picking() || e.target.closest(".ui-dock, .ui-sheet, .ui-hint, .ui-pins")) return;
    const n = e.target.closest(PICK); if (!n) return;
    e.preventDefault(); e.stopPropagation(); stopPick();
    if (pickFor === "ask") openAsk(n); else openThread(n, anchorOf(n));
  }, true);
  bCom.onclick = () => (picking() && pickFor === "com" ? stopPick() : startPick("com"));

  function openThread(node, anchor) {
    closeSheet(); node.classList.add("ui-focus");
    node.scrollIntoView({ block: "nearest", behavior: "smooth" });
    const list = comments.filter((c) => c.anchor === anchor);
    sheet.innerHTML = `<div class="ui-head"><b>${esc(labelOf(node))}</b><button type="button" class="ui-x">关闭</button></div>
      <div class="ui-list">${list.map((c) => `<div class="ui-c">${c.text ? `<div>${esc(c.text)}</div>` : ""}${c.sketch ? `<img src="${c.sketch}" alt="手绘">` : ""}
      <div class="ui-meta">${esc(c.at.slice(5, 16))} <button type="button" data-del="${esc(c.id)}">删除</button></div></div>`).join("")}</div>
      <div class="ui-tabs"><button type="button" class="on" data-t="text">文字</button><button type="button" data-t="draw">画图</button></div>
      <textarea class="ui-ta" rows="3" placeholder="写评论"></textarea>
      <div class="ui-draw" hidden></div>
      <div class="ui-acts"><button type="button" class="ui-ask2">问这个</button><button type="button" class="ui-save">保存</button></div>`;
    sheet.hidden = false;
    sheet.querySelector(".ui-x").onclick = closeSheet;
    sheet.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => api("c", { del: b.dataset.del }).then((c) => { comments = c; pins(); openThread(node, anchor); })));
    const ta = sheet.querySelector(".ui-ta"), draw = sheet.querySelector(".ui-draw");
    const bg = node.tagName === "IMG" ? node : node.querySelector && node.querySelector(":scope > img");
    const pad = makePad(draw, bg);
    sheet.querySelectorAll(".ui-tabs button").forEach((b) => (b.onclick = () => {
      sheet.querySelectorAll(".ui-tabs button").forEach((x) => x.classList.toggle("on", x === b));
      const d = b.dataset.t === "draw"; draw.hidden = !d; ta.hidden = d;
    }));
    sheet.querySelector(".ui-save").onclick = () => {
      const text = ta.value.trim(), sketch = pad.empty() ? "" : pad.png();
      if (!text && !sketch) return;
      api("c", { anchor, label: labelOf(node), text, sketch }).then((c) => { comments = c; pins(); openThread(node, anchor); });
    };
    sheet.querySelector(".ui-ask2").onclick = () => openAsk(node, ta.value.trim());
  }

  // ---------- 画板 ----------
  const COLORS = ["#151A22", "#C2602A", "#2747B0", "#1F8A6E", "#B3263A", "#8A8F99"];
  const TOOLS = [["pen", "笔"], ["eraser", "橡皮"], ["line", "直线"], ["arrow", "箭头"], ["rect", "方框"], ["ellipse", "圆"], ["text", "文字"]];
  function makePad(box, bgImg) {
    box.innerHTML = `<div class="ui-pad-tools">${TOOLS.map(([k, t], i) => `<button type="button" data-tool="${k}"${i ? "" : ' class="on"'}>${t}</button>`).join("")}</div>
      <div class="ui-pad-tools">${COLORS.map((c, i) => `<span class="ui-col${i ? "" : " on"}" style="--c:${c}" data-col="${c}"></span>`).join("")}
      <span class="ui-sep"></span>${[3, 6, 12].map((w, i) => `<span class="ui-w${i ? "" : " on"}" data-w="${w}"><i style="width:${w + 2}px;height:${w + 2}px"></i></span>`).join("")}
      <span class="ui-sep"></span><button type="button" data-act="undo">撤销</button><button type="button" data-act="redo">重做</button><button type="button" data-act="clear">清空</button>
      ${bgImg ? '<label class="ui-bgt"><input type="checkbox" checked> 原图</label>' : ""}</div>
      <canvas width="900" height="560"></canvas>`;
    const cv = box.querySelector("canvas"), ctx = cv.getContext("2d");
    let tool = "pen", color = COLORS[0], width = 3, items = [], undone = [], cur = null, useBg = !!bgImg, img = null;
    if (bgImg) {
      img = new Image(); img.crossOrigin = "anonymous"; img.onload = () => { const h = Math.round(900 * img.naturalHeight / img.naturalWidth); cv.height = Math.max(200, Math.min(h, 1400)); render(); }; img.src = bgImg.currentSrc || bgImg.src;
      box.querySelector(".ui-bgt input").onchange = (e) => { useBg = e.target.checked; render(); };
    }
    const arrowHead = (x0, y0, x1, y1, w) => { const a = Math.atan2(y1 - y0, x1 - x0), L = 12 + w * 2;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x1 - L * Math.cos(a - 0.45), y1 - L * Math.sin(a - 0.45));
      ctx.moveTo(x1, y1); ctx.lineTo(x1 - L * Math.cos(a + 0.45), y1 - L * Math.sin(a + 0.45)); ctx.stroke(); };
    function drawItem(it) {
      ctx.strokeStyle = ctx.fillStyle = it.c; ctx.lineWidth = it.w; ctx.lineCap = ctx.lineJoin = "round";
      const [a, b] = [it.p[0], it.p[it.p.length - 1]];
      if (it.k === "pen") { ctx.beginPath(); it.p.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.stroke(); }
      else if (it.k === "line" || it.k === "arrow") { ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); if (it.k === "arrow") arrowHead(a[0], a[1], b[0], b[1], it.w); }
      else if (it.k === "rect") ctx.strokeRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
      else if (it.k === "ellipse") { ctx.beginPath(); ctx.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.abs(b[0] - a[0]) / 2 + .1, Math.abs(b[1] - a[1]) / 2 + .1, 0, 0, 7); ctx.stroke(); }
      else if (it.k === "text") { ctx.font = `600 ${14 + it.w * 3}px ${getComputedStyle(document.body).fontFamily}`; ctx.textBaseline = "top"; it.t.split("\n").forEach((ln, i) => ctx.fillText(ln, a[0], a[1] + i * (18 + it.w * 3))); }
    }
    function render() {
      ctx.fillStyle = "#FFFFFF"; ctx.fillRect(0, 0, cv.width, cv.height);
      if (img && useBg && img.complete && img.naturalWidth) ctx.drawImage(img, 0, 0, cv.width, cv.height);
      items.forEach(drawItem);
    }
    const near = (it, x, y) => { const r = 10 + it.w; if (it.k === "text") { const [a] = it.p; return x >= a[0] - r && y >= a[1] - r && x <= a[0] + 220 && y <= a[1] + 40; }
      const pts = it.k === "pen" ? it.p : (() => { const [a, b] = [it.p[0], it.p[it.p.length - 1]]; const out = []; for (let i = 0; i <= 40; i++) out.push([a[0] + (b[0] - a[0]) * i / 40, a[1] + (b[1] - a[1]) * i / 40]);
        if (it.k === "rect" || it.k === "ellipse") { for (let i = 0; i <= 40; i++) { out.push([a[0] + (b[0] - a[0]) * i / 40, b[1]], [a[0], a[1] + (b[1] - a[1]) * i / 40], [b[0], a[1] + (b[1] - a[1]) * i / 40], [a[0] + (b[0] - a[0]) * i / 40, a[1]]); } } return out; })();
      return pts.some(([px, py]) => (px - x) ** 2 + (py - y) ** 2 < r * r); };
    const pos = (e) => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height]; };
    const erase = (p) => { const before = items.length; items = items.filter((it) => !near(it, p[0], p[1])); if (items.length !== before) { undone = []; render(); } };
    cv.onpointerdown = (e) => {
      cv.setPointerCapture(e.pointerId); const p = pos(e);
      if (tool === "eraser") { cur = { k: "eraser" }; erase(p); return; }
      if (tool === "text") { const input = document.createElement("textarea"); input.className = "ui-tin";
        const r = cv.getBoundingClientRect(); input.style.left = e.clientX - r.left + "px"; input.style.top = e.clientY - r.top + "px"; input.style.color = color;
        box.style.position = "relative"; box.append(input); setTimeout(() => input.focus(), 0);
        const done = () => { const v = input.value.trim(); input.remove(); if (v) { items.push({ k: "text", c: color, w: width / 3, p: [p], t: v }); undone = []; render(); } };
        input.onblur = done; input.onkeydown = (ev) => { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); input.blur(); } }; return; }
      cur = { k: tool, c: color, w: width, p: [p] }; items.push(cur); undone = [];
    };
    cv.onpointermove = (e) => { if (!cur) return; const p = pos(e);
      if (cur.k === "eraser") return erase(p);
      if (cur.k === "pen") cur.p.push(p); else cur.p[1] = p; render(); };
    cv.onpointerup = cv.onpointercancel = () => (cur = null);
    box.querySelectorAll("[data-tool]").forEach((b) => (b.onclick = () => { tool = b.dataset.tool; box.querySelectorAll("[data-tool]").forEach((x) => x.classList.toggle("on", x === b)); cv.style.cursor = tool === "eraser" ? "cell" : "crosshair"; }));
    box.querySelectorAll("[data-col]").forEach((s) => (s.onclick = () => { color = s.dataset.col; box.querySelectorAll("[data-col]").forEach((x) => x.classList.toggle("on", x === s)); }));
    box.querySelectorAll("[data-w]").forEach((s) => (s.onclick = () => { width = +s.dataset.w; box.querySelectorAll("[data-w]").forEach((x) => x.classList.toggle("on", x === s)); }));
    box.querySelector('[data-act="undo"]').onclick = () => { if (items.length) { undone.push(items.pop()); render(); } };
    box.querySelector('[data-act="redo"]').onclick = () => { if (undone.length) { items.push(undone.pop()); render(); } };
    box.querySelector('[data-act="clear"]').onclick = () => { if (items.length) { undone = items.reverse(); items = []; render(); } };
    render();
    return { empty: () => !items.length, png: () => cv.toDataURL("image/png") };
  }

  // ---------- 提问 ----------
  let history = null;
  function openAsk(focus, preset) {
    closeSheet(); if (focus) focus.classList.add("ui-focus");
    sheet.innerHTML = `<div class="ui-head"><b>问${focus ? "：" + esc(labelOf(focus)) : "整页"}</b><span><button type="button" class="ui-x ui-new">新对话</button><button type="button" class="ui-x ui-repick">换个地方</button><button type="button" class="ui-x ui-close">关闭</button></span></div>
      <div class="ui-chat"></div>
      <form class="ui-qf"><textarea rows="2" placeholder="问点什么"></textarea><button type="submit">发送</button></form>
      <div class="ui-model">模型 <button type="button" data-m="flash">flash</button><button type="button" data-m="pro">pro</button></div>`;
    sheet.hidden = false;
    const chat = sheet.querySelector(".ui-chat"), f = sheet.querySelector(".ui-qf"), q = f.querySelector("textarea");
    q.value = preset || "";
    sheet.querySelector(".ui-close").onclick = closeSheet;
    sheet.querySelector(".ui-repick").onclick = () => startPick("ask");
    let model = "flash";
    try { model = localStorage.getItem("cp-model") || "flash"; } catch (e) {}
    const paintModel = () => sheet.querySelectorAll("[data-m]").forEach((b) => b.classList.toggle("on", b.dataset.m === model));
    sheet.querySelectorAll("[data-m]").forEach((b) => (b.onclick = () => { model = b.dataset.m; paintModel(); try { localStorage.setItem("cp-model", model); } catch (e) {} }));
    paintModel();
    sheet.querySelector(".ui-new").onclick = () => api("ask", { clear: true }).then(() => { history = []; show(); q.focus(); });
    if (focus) focus.scrollIntoView({ block: "nearest", behavior: "smooth" });
    if (!preset) setTimeout(() => q.focus(), 0);
    const add = (who, html) => { const m = el("div", "ui-m " + who, html); chat.append(m); chat.scrollTop = chat.scrollHeight; return m; };
    const stepsHtml = (st) => (st && st.length ? `<details class="ui-steps"><summary>查了 ${st.length} 处代码</summary>${st.map((x) => `<div>${esc(x)}</div>`).join("")}</details>` : "");
    const show = () => { chat.innerHTML = ""; (history || []).slice(-20).forEach((h) => { add("q", esc(h.q)); add("a", stepsHtml(h.steps) + md(h.a)); }); };
    if (history) show(); else api("ask").then((h) => { history = h || []; show(); }).catch(() => { history = []; });
    f.onsubmit = async (e) => {
      e.preventDefault();
      const question = q.value.trim(); if (!question) return;
      q.value = ""; add("q", esc(question)); const out = add("a", "…");
      const main = document.querySelector("main") || document.body;
      let context = main.innerText;
      if (focus) {
        const box = focus.closest(".card, figure, section, .kpi") || focus;
        const alt = [focus, ...focus.querySelectorAll("img[alt], [aria-label]")].map((n) => n.getAttribute("alt") || n.getAttribute("aria-label") || "").filter(Boolean).join("；");
        context = "【读者点选的部分：" + labelOf(focus) + "】\n" + (alt ? "说明：" + alt + "\n" : "") + focus.innerText + (box !== focus ? "\n\n【所在卡片】\n" + box.innerText : "") + "\n\n【整页】\n" + context;
      }
      let text = "", steps = [];
      const paint = () => { out.innerHTML = (steps.length ? `<div class="ui-steps live">${steps.slice(-3).map((x) => `<div>${esc(x)}</div>`).join("")}</div>` : "") + (text ? md(text) : "…"); chat.scrollTop = chat.scrollHeight; };
      try {
        const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ page: PAGE, title: document.title, context, question, model, history: (history || []).slice(-6) }) });
        const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "";
        for (;;) {
          const { done, value } = await reader.read(); if (done) break;
          buf += dec.decode(value, { stream: true });
          let i; while ((i = buf.indexOf("\n\n")) >= 0) {
            const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
            if (!line.startsWith("data:")) continue;
            const d = JSON.parse(line.slice(5));
            if (d.s) { steps.push(d.s); paint(); }
            if (d.t) { text += d.t; paint(); }
            if (d.done && !d.error && !out.dataset.err) out.innerHTML = stepsHtml(steps) + md(text);
            if (d.error) { out.dataset.err = 1; out.innerHTML = stepsHtml(steps) + md(text) + "<br><i>出错：" + esc(d.error) + "</i>"; }
          }
        }
        (history = history || []).push({ q: question, a: text, steps });
      } catch (err) { out.innerHTML = "<i>连不上，稍后再试</i>"; }
    };
  }
  bAsk.onclick = () => (picking() && pickFor === "ask" ? stopPick() : startPick("ask"));

  if (document.readyState === "complete") load(); else window.addEventListener("load", load);
})();
