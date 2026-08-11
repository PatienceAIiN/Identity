// Real-browser check of the reported bug: after sign out, does Back show a
// signed-in page? Drives Chrome over CDP, no external packages.
const http = require('http');
const { spawn } = require('child_process');
const os = require('os'), fs = require('fs'), path = require('path');

const B = 'http://127.0.0.1:8000';
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'chr-'));
const chrome = spawn('google-chrome', [
  '--headless=new', '--disable-gpu', '--no-sandbox',
  '--remote-debugging-port=9333', `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' });

const get = (p) => new Promise((res, rej) =>
  http.get(`http://127.0.0.1:9333${p}`, r => { let d=''; r.on('data',c=>d+=c); r.on('end',()=>res(JSON.parse(d))); }).on('error', rej));

(async () => {
  for (let i = 0; i < 50; i++) { try { await get('/json/version'); break; } catch { await new Promise(r=>setTimeout(r,200)); } }
  const targets = await get('/json/list');
  // Pick the real page target — extension background pages also appear here and
  // attaching to one makes every assertion meaningless.
  const page = targets.find(t => t.type === 'page' && /^(about:blank|http)/.test(t.url));
  if (!page) { console.log('NO_PAGE_TARGET', JSON.stringify(targets.map(t=>[t.type,t.url]))); process.exit(2); }
  const wsUrl = page.webSocketDebuggerUrl;

  const WebSocket = require('ws');
  let ws;
  try { ws = new WebSocket(wsUrl); } catch (e) { console.log('NO_WS'); process.exit(2); }
  let id = 0; const pending = new Map();
  const send = (method, params={}) => new Promise(r => { const i=++id; pending.set(i,r); ws.send(JSON.stringify({id:i,method,params})); });
  await new Promise(r => ws.on('open', r));
  ws.on('message', (m) => { const d = JSON.parse(m); if (d.id && pending.has(d.id)) { pending.get(d.id)(d.result); pending.delete(d.id); } });

  const evalJs = async (expr) => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.value;
  const nav = async (url) => { await send('Page.navigate', { url }); await new Promise(r=>setTimeout(r,1800)); };

  await send('Page.enable'); await send('Runtime.enable');

  // sign in through the real form path
  await nav(`${B}/app/auth.html`);
  await evalJs(`(async()=>{await fetch('/v1/auth/signin',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'include',body:JSON.stringify({email:${JSON.stringify(process.argv[2])},password:'a-long-password-123'})});return 1})()`);
  await nav(`${B}/app/codes.html`);
  const signedIn = await evalJs(`document.getElementById('count')?.textContent ?? ''`);
  const visible1 = await evalJs(`getComputedStyle(document.documentElement).visibility`);
  console.log('on codes while signed in: visibility=' + visible1 + ' count="' + signedIn + '"');

  await nav(`${B}/app/profile.html`);
  await evalJs(`(async()=>{await fetch('/v1/auth/signout',{method:'POST',credentials:'include'});location.replace('/');return 1})()`);
  await new Promise(r=>setTimeout(r,1500));
  console.log('after signout, url=' + await evalJs('location.pathname'));

  // THE TEST: go back in history
  await send('Page.navigateToHistoryEntry', {}).catch(()=>{});
  await evalJs('history.back()');
  await new Promise(r=>setTimeout(r,2500));
  const url = await evalJs('location.pathname');
  const vis = await evalJs('getComputedStyle(document.documentElement).visibility');
  const meStatus = await evalJs(`fetch('/v1/me',{credentials:'include'}).then(r=>String(r.status))`);
  console.log('after Back: url=' + url + ' visibility=' + vis + ' /v1/me=' + meStatus);
  // A leak means: still on an app page, visible, while the session is dead.
  const PRIVATE = ['/app/codes.html', '/app/new.html', '/app/scan.html', '/app/profile.html'];
  const leaked = PRIVATE.some(p => url === p) && vis === 'visible' && meStatus === '401';

  console.log(leaked ? 'RESULT: LEAK — signed-in page visible after logout' : 'RESULT: OK — no signed-in page after logout');
  ws.close(); chrome.kill();
  process.exit(leaked ? 1 : 0);
})().catch(e => { console.log('ERR', e.message); chrome.kill(); process.exit(3); });
