// open a URL in a new tab of the debug Chrome; prints targetId
let ver; for (const b of ["http://[::1]:9222", "http://127.0.0.1:9222"]) { try { const r = await fetch(b + "/json/version"); if (r.ok) { ver = await r.json(); break; } } catch {} }
const ws = new WebSocket(ver.webSocketDebuggerUrl);
ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: "Target.createTarget", params: { url: process.argv[2] } }));
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id === 1) { console.log(JSON.stringify(m.result)); process.exit(0); } };
