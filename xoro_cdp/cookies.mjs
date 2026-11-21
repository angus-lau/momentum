// Print the debug Chrome's cookies for a host as one Cookie header (empty if none / Chrome not running).
// usage: node cookies.mjs momentum.xoro.one
const host = process.argv[2];
let ver; for (const b of ["http://127.0.0.1:9222", "http://[::1]:9222"]) { try { const r = await fetch(b + "/json/version"); if (r.ok) { ver = await r.json(); break; } } catch {} }
if (!ver) process.exit(0);
const ws = new WebSocket(ver.webSocketDebuggerUrl);
ws.onopen = () => ws.send(JSON.stringify({id: 1, method: "Storage.getCookies"}));
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id !== 1) return;
  const c = (m.result?.cookies || []).filter(c => host.endsWith(c.domain.replace(/^\./, "")));
  process.stdout.write(c.map(c => c.name + "=" + c.value).join("; "), () => process.exit(0)); };
ws.onerror = () => process.exit(0);
setTimeout(() => process.exit(0), 5000);
