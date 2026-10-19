// Evaluate JS inside the first frame/page whose URL matches a pattern (cross-origin iframes included).
// usage: node cdp_eval.mjs <url-substring> <js-file-or-expression>
import fs from "node:fs";

const [pat, src] = process.argv.slice(2);
const expr = fs.existsSync(src) ? fs.readFileSync(src, "utf8") : src;
let ver;
for (const b of ["http://[::1]:9222", "http://127.0.0.1:9222"]) { try { const r = await fetch(b + "/json/version"); if (r.ok) { ver = await r.json(); break; } } catch {} }
const ws = new WebSocket(ver.webSocketDebuggerUrl);
let id = 0; const pending = new Map();
const send = (method, params = {}, sessionId) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result || m.error); pending.delete(m.id); } };
ws.onopen = async () => {
  const { targetInfos } = await send("Target.getTargets");
  const t = targetInfos.find((x) => (x.type === "iframe" || x.type === "page") && x.url.includes(pat));
  if (!t) { console.log("no target matching", pat, targetInfos.filter(x=>/page|iframe/.test(x.type)).map((x) => x.type + " " + x.url.split("?")[0])); process.exit(1); }
  const { sessionId } = await send("Target.attachToTarget", { targetId: t.targetId, flatten: true });
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }, sessionId);
  console.log(JSON.stringify(r.exceptionDetails ? r.exceptionDetails : (r.result ? r.result.value : r), null, 1));
  process.exit(0);
};
