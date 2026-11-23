const ver = await (await fetch("http://127.0.0.1:9222/json/version")).json();
const ws = new WebSocket(ver.webSocketDebuggerUrl); let id=0; const p=new Map();
const send=(m,params={},s)=>new Promise(r=>{const i=++id;p.set(i,r);ws.send(JSON.stringify({id:i,method:m,params,sessionId:s}))});
ws.onmessage=e=>{const m=JSON.parse(e.data); if(m.id&&p.has(m.id)){p.get(m.id)(m.result||m.error);p.delete(m.id)}};
ws.onopen=async()=>{ console.log(JSON.stringify(await send("Browser.setDownloadBehavior",{behavior:"allow",downloadPath:process.env.HOME+"/Downloads",eventsEnabled:true}))); process.exit(0) };
