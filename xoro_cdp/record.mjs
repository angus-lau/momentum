// Record what the user does in Xoro on the debug Chrome: page navigations + every /WebServices/ call with its body.
// usage: node record.mjs <out.jsonl>   (runs until killed)
import fs from "node:fs";
const out = process.argv[2]; const log = o => fs.appendFileSync(out, JSON.stringify(Object.assign({t: new Date().toISOString()}, o)) + "\n");
const ver = await (await fetch("http://127.0.0.1:9222/json/version")).json();
const ws = new WebSocket(ver.webSocketDebuggerUrl); let id = 0;
const send = (method, params = {}, sessionId) => ws.send(JSON.stringify({id: ++id, method, params, sessionId}));
const sessions = new Set();
ws.onopen = () => send("Target.setDiscoverTargets", {discover: true});
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.method === "Target.targetCreated" || m.method === "Target.targetInfoChanged") {
    const t = m.params.targetInfo;
    if (t.type === "page" && t.url.includes("xoro.one") && !t.attached) send("Target.attachToTarget", {targetId: t.targetId, flatten: true});
  }
  if (m.method === "Target.attachedToTarget" && !sessions.has(m.params.sessionId)) {
    sessions.add(m.params.sessionId); send("Network.enable", {}, m.params.sessionId); send("Page.enable", {}, m.params.sessionId);
  }
  if (m.method === "Page.frameNavigated" && !m.params.frame.parentId) log({nav: m.params.frame.url});
  if (m.method === "Network.requestWillBeSent") {
    const r = m.params.request;
    if (/\/WebServices\//.test(r.url) && r.method === "POST") log({call: r.url.replace(/^https:\/\/[^/]+/, ""), body: (r.postData || "").slice(0, 20000)});
  }
};
