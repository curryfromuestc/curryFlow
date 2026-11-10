// A minimal Chrome DevTools Protocol client (node --experimental-websocket) for the stage scripts.
export async function connect(port) {
  const ver = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const ws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = bad; });
  let id = 0; const wait = new Map(), events = [];
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && wait.has(d.id)) { const [ok, bad] = wait.get(d.id); wait.delete(d.id); d.error ? bad(new Error(d.error.message)) : ok(d.result); } else events.push(d); };
  const send = (method, params = {}, sessionId) => new Promise((ok, bad) => { wait.set(++id, [ok, bad]); ws.send(JSON.stringify({ id, method, params, sessionId })); });
  const page = async (url) => {
    const { targetId } = await send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
    await send("Page.enable", {}, sessionId); await send("Runtime.enable", {}, sessionId);
    const ev = async (expr) => { const r = await send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true, userGesture: true }, sessionId);
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception ? r.exceptionDetails.exception.description : r.exceptionDetails.text); return r.result.value; };
    const go = async (u) => { await send("Page.navigate", { url: u }, sessionId); await sleep(1500); };
    const shot = async (file) => { const r = await send("Page.captureScreenshot", { format: "png" }, sessionId); (await import("node:fs")).writeFileSync(file, Buffer.from(r.data, "base64")); };
    if (url) await go(url);
    return { ev, go, shot, sessionId, targetId, send: (m, p) => send(m, p, sessionId) };
  };
  return { send, page, events, close: () => ws.close() };
}
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export async function until(fn, ms, what) { const t = Date.now(); for (;;) { const v = await fn(); if (v) return v; if (Date.now() - t > ms) throw new Error("timed out waiting for: " + what); await sleep(300); } }
// one line per check; a failed check makes the stage exit 1
export function must(ok, what, detail) { console.log((ok ? "ok    " : "FAIL  ") + what + (detail === undefined ? "" : "  " + (typeof detail === "string" ? detail : JSON.stringify(detail)))); if (!ok) process.exitCode = 1; }
