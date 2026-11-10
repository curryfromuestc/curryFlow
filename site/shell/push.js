// 首页的通知开关（标准 Web Push）。状态：未开启 / 已开启 / 这里开不了（写明原因和该怎么做）。
// 开启 = 申请权限 → 用服务器的 VAPID 公钥向浏览器的推送服务订阅 → 把订阅交给服务器；收和弹在 sw.js。
(function () {
  "use strict";
  const bell = document.getElementById("bell"), box = document.getElementById("push");
  if (!bell || !box) return;
  const msg = box.querySelector(".msg");
  const btn = { on: box.querySelector('[data-a="on"]'), test: box.querySelector('[data-a="test"]'), off: box.querySelector('[data-a="off"]') };
  const ua = navigator.userAgent;
  const ios = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone = navigator.standalone === true || (window.matchMedia && matchMedia("(display-mode: standalone)").matches);
  let key = null, reg = null, sub = null;

  // state: off 未开启 / on 已开启 / no 开不了 / wait 正在查
  function paint(state, text, label) {
    box.dataset.s = bell.dataset.s = state;
    bell.textContent = "通知：" + (label || { off: "未开启", on: "已开启", no: "不可用", wait: "…" }[state]);
    btn.on.hidden = state !== "off"; btn.test.hidden = btn.off.hidden = state !== "on";
    msg.textContent = text || { off: "开启后，机器告警、作业完成这类消息会推到这台设备。", on: "这台设备会收到通知。", wait: "检查中…" }[state] || "";
  }
  function busy(b) { Object.values(btn).forEach((x) => { x.disabled = b; }); }
  const timeout = (p, ms, why) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(why)), ms))]);
  const bytes = (b64u) => Uint8Array.from(atob(b64u.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
  const b64u = (buf) => btoa(String.fromCharCode.apply(null, new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

  // 出错时抛的 Error 一律带 e.data（服务器的回答，没有就是 {}）
  async function api(method, path, body) {
    let r;
    try {
      r = await fetch(path, { method, cache: "no-store", headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    } catch (err) {
      const e = new Error("连不上服务器，检查网络后再试（" + (err && err.message ? err.message : err) + "）");
      e.data = {};
      throw e;
    }
    let d = null;
    try { d = await r.json(); } catch (_) { /* 登录过期时拿到的是登录页 */ }
    if (r.ok && d) return d;
    const e = new Error(d && d.error ? d.error : r.ok ? "登录过期了，刷新页面重新登录" : "服务器返回 " + r.status);
    e.data = d || {};
    throw e;
  }

  // 这个环境开不了的原因；能开就返回 null
  function blocked() {
    if (!window.isSecureContext) return ["页面不是 https 打开的，浏览器不给网页通知。"];
    if (ios && !standalone) return ["iPhone 上网页通知只在主屏幕应用里可用：点分享按钮 →“添加到主屏幕”，再从主屏幕的 curryPlan 图标打开，回到这里开启。", "先加到主屏幕"];
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window))
      return [ios ? "这个系统版本不支持网页通知，需要 iOS 16.4 或更新。" : "这个浏览器不支持网页推送（微信等应用的内置浏览器都不行），换 Chrome、Edge、Firefox 或 Safari 打开。"];
    if (Notification.permission === "denied") return [ios ? "通知权限被关掉了：到 设置 → 通知 → curryPlan 里打开，再回来。" : "这个网站的通知权限被拒绝了：点地址栏左边的图标 → 网站设置 → 通知 → 允许，再刷新。", "权限被拒绝"];
    return null;
  }

  const subscribe = () => timeout(reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes(key) }), 30000,
    "浏览器向推送服务登记超时" + (/Android/.test(ua) ? "：Android 的 Chrome 走谷歌推送，手机要能连上谷歌" : ""));

  // 把浏览器手里的订阅交给服务器。推送服务说过它已失效（服务器记着，回 gone）：退订，重新订阅拿新端点，再交一次。
  // 返回服务器的回答；sub 换成服务器收下的那个。重新订阅失败就抛出，sub 为 null（这台设备此时没有订阅）。
  async function register() {
    try { return await api("POST", "/api/push/subscribe", sub.toJSON()); }
    catch (e) {
      if (!e.data.gone) throw e;
      await sub.unsubscribe().catch(() => {}); sub = null;
      sub = await subscribe();
      return await api("POST", "/api/push/subscribe", sub.toJSON());
    }
  }

  async function init() {
    const no = blocked();
    if (no) return paint("no", no[0], no[1]);
    paint("wait");
    try {
      reg = await timeout(navigator.serviceWorker.ready, 10000, "service worker 没起来，刷新一下再试");
      key = (await api("GET", "/api/push/key")).key;
      sub = await reg.pushManager.getSubscription();
      const had = sub && sub.options && sub.options.applicationServerKey;   // 有的浏览器不给 options，那就不比
      if (had && b64u(had) !== key) {
        await sub.unsubscribe(); sub = null;   // 服务器换过密钥，旧订阅已经收不到
      }
      if (!sub) return paint("off");
      // 每次打开都交一遍：服务器那边丢了也能补上；推送服务注销了的换一个新的；顺便拿到上次发送的结果
      let r;
      try { r = await register(); }
      catch (e) {
        if (!sub) return paint("off", "这台设备原来的订阅已被推送服务注销，重新订阅没成（" + e.message + "），点“开启通知”再来一次。");
        throw e;
      }
      paint("on", r.last && !r.last.ok ? "已开启，但上次没发出去：" + r.last.reason : "");
    } catch (e) { paint("no", "现在查不了通知状态：" + e.message); }
  }

  btn.on.onclick = async () => {
    busy(true);
    try {
      // 申请权限必须是点击之后的第一步，iOS 只认用户手势里的申请
      const perm = await Notification.requestPermission();
      if (perm !== "granted") { const no = blocked(); return no ? paint("no", no[0], no[1]) : paint("off", "没有拿到通知权限，再点一次开启并选“允许”。"); }
      sub = await subscribe();   // 浏览器已有订阅时给的是那个旧的；它若已失效，register 会换新的
      await register();
      paint("on", "已开启。点“发一条测试”确认能收到。");
    } catch (e) {
      if (sub) { await sub.unsubscribe().catch(() => {}); sub = null; }
      paint("off", "没开成：" + e.message);
    } finally { busy(false); }
  };

  btn.test.onclick = async () => {
    busy(true); msg.textContent = "发送中…";
    try {
      const r = await api("POST", "/api/push/test", { endpoint: sub.endpoint });
      paint("on", "已交给" + r.service + "推送（返回 " + r.status + "），几秒内应该弹出来；没弹就看系统里这个应用的通知开关。");
    } catch (e) {
      if (e.data && e.data.removed) { await sub.unsubscribe().catch(() => {}); sub = null; paint("off", "没发出去：" + e.message); }
      else paint("on", "没发出去：" + e.message);
    } finally { busy(false); }
  };

  btn.off.onclick = async () => {
    busy(true);
    const endpoint = sub.endpoint;
    try {
      await sub.unsubscribe(); sub = null;
      await api("POST", "/api/push/unsubscribe", { endpoint });
      paint("off", "已关闭，这台设备不再收通知。");
    } catch (e) {
      paint(sub ? "on" : "off", sub ? "没关掉：" + e.message : "这台设备已退订，但服务器那边没删掉（" + e.message + "）；它下次发送时会发现订阅失效并自己删。");
    } finally { busy(false); }
  };

  bell.onclick = () => { box.hidden = !box.hidden; bell.setAttribute("aria-expanded", String(!box.hidden)); };
  init();
})();
