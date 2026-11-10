// 方案页 kit v2：截止倒计时；可编辑正文框（存到 ../api/doc/NAME，每次保存都留一份版本），带自己的撤销/重做（手机上没有 ⌘Z）。
(function () {
  "use strict";
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
  const today = new Date(); today.setHours(0, 0, 0, 0);
  document.querySelectorAll("[data-until]").forEach((el) => {
    const [y, m, d] = el.dataset.until.split("-").map(Number);
    el.textContent = String(Math.round((new Date(y, m - 1, d) - today) / 86400000));
  });

  const box = document.querySelector(".draft[data-doc]");
  if (!box) return;
  const url = "/api/doc/" + box.dataset.doc;
  const st = document.querySelector(".draft-bar .st");
  const show = (t, dirty) => { if (st) { st.textContent = t; st.classList.toggle("dirty", !!dirty); } };
  let timer = null, saving = false, again = false;

  async function save() {
    if (saving) { again = true; return; }
    saving = true; clearTimeout(timer); show("保存中…", true);
    try {
      const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ html: box.innerHTML }) });
      if (!r.ok) throw new Error(String(r.status));
      const j = await r.json();
      show("已保存 " + j.at.slice(5, 16));
    } catch (e) { show("没保存上，按 Ctrl+S 再试", true); }
    saving = false;
    if (again) { again = false; save(); }
  }

  fetch(url, { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => { if (j && j.html) { box.innerHTML = j.html; show("服务器版本 " + j.at.slice(5, 16)); } else show("初版"); past = [box.innerHTML]; future = []; paint(); })
    .catch(() => show("连不上服务器，改动不会保存", true));

  box.contentEditable = "true";
  box.spellcheck = false;
  const changed = () => { show("有改动", true); clearTimeout(timer); timer = setTimeout(save, 2000); };

  // 历史：连续打字 800ms 内算一步；past 的最后一项总是当前内容
  let past = [box.innerHTML], future = [], burst = null;
  const bar = document.querySelector(".draft-bar");
  const btn = bar && bar.querySelector("button");
  const mk = (t, f) => { const b = document.createElement("button"); b.type = "button"; b.className = "hist"; b.textContent = t;
    b.addEventListener("pointerdown", (e) => e.preventDefault()); b.addEventListener("click", f); return b; };   // 不抢焦点，手机键盘不收起
  const bUndo = mk("↶ 撤销", () => undo()), bRedo = mk("↷ 重做", () => redo());
  if (bar) bar.insertBefore(bUndo, btn || null), bar.insertBefore(bRedo, btn || null);
  function paint() { bUndo.disabled = past.length < 2; bRedo.disabled = !future.length; }
  function record() {
    const html = box.innerHTML;
    if (burst) past[past.length - 1] = html; else { past.push(html); if (past.length > 200) past.shift(); }
    future = []; clearTimeout(burst); burst = setTimeout(() => (burst = null), 800); paint();
  }
  function restore(html) { const y = window.scrollY; box.innerHTML = html; window.scrollTo(0, y); burst = null; paint(); changed(); }
  function undo() { if (past.length < 2) return; future.push(past.pop()); restore(past[past.length - 1]); }
  function redo() { if (!future.length) return; const h = future.pop(); past.push(h); restore(h); }
  paint();

  box.addEventListener("input", () => { record(); changed(); });
  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    const k = e.key.toLowerCase();
    if (k === "s") { e.preventDefault(); save(); }
    else if (box.contains(document.activeElement) || document.activeElement === box) {
      if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); redo(); }
    }
  });
  if (btn) btn.addEventListener("click", save);
})();
