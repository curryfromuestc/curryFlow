// 首页：读 /catalog.json，按项目分组，最新的在上；搜索和分类过滤都在本地做。
(function () {
  "use strict";
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
  const list = document.getElementById("list"), q = document.getElementById("q");
  let cat = null, kind = "";
  const esc = (s) => String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function render() {
    if (!cat) return;
    const term = q.value.trim().toLowerCase();
    const html = cat.projects.map((p) => {
      const pages = cat.pages.filter((g) => g.project === p.id && (!kind || g.kind === kind) &&
        (!term || (g.title + " " + g.say).toLowerCase().includes(term)));
      if (!pages.length) return "";
      return `<section class="proj"><h2>${esc(p.name)} <small>${pages.length} 页</small></h2><div class="rows">` +
        pages.map((g) => `<a class="row" href="${esc(g.url)}"><span class="t">${esc(g.title)}</span>` +
          `<span class="m">${esc(g.updated.slice(5, 10))}<br><span class="k">${esc(g.kind)}</span></span></a>`).join("") + `</div></section>`;
    }).join("");
    list.innerHTML = html || '<p class="empty">没有匹配的页面</p>';
  }
  fetch("/catalog.json", { cache: "no-store" }).then((r) => r.json()).then((c) => {
    cat = c; render();
    navigator.serviceWorker && navigator.serviceWorker.ready.then((reg) => reg.active && reg.active.postMessage({ prefetch: c.pages.map((g) => g.url) }));
  }).catch(() => { list.innerHTML = '<p class="empty">读不到目录，检查网络</p>'; });
  q.addEventListener("input", render);
  document.getElementById("seg").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    kind = b.dataset.k; document.querySelectorAll("#seg button").forEach((x) => x.classList.toggle("on", x === b)); render();
  });
})();
