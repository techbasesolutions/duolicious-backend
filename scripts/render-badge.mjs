// Render circular social badge PNGs (brand-color circle + white official
// glyph + subtle ring) for the referral-community email's social row.
// Reuses the proven CDP transparency + bbox-clip flow from
// render-title-png.mjs, but spawns a FRESH chrome per badge (a single shared
// session hangs after the 3rd data-URL navigation).
//
//   node scripts/render-badge.mjs
//
// Outputs ../ahavah-web/public/email/badge-{instagram,threads,facebook}.png

import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const SIZE = 240; // rendered large (headless Chrome mis-lays-out tiny
                  // windows); the email displays it at 56px via width=.
const OUT_DIR = resolve("../ahavah-web/public/email");

const BADGES = [
  { name: "instagram", port: 9461, bg: "radial-gradient(circle at 30% 107%, #fdf497 0%, #fdf497 5%, #fd5949 45%, #d6249f 60%, #285AEB 90%)" },
  { name: "threads", port: 9462, bg: "#000000" },
  { name: "facebook", port: 9463, bg: "#0866FF" },
];

const CHROME_CANDIDATES = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
];
const BROWSER = CHROME_CANDIDATES.find((p) => existsSync(p));
if (!BROWSER) { console.error("No Chrome/Edge found"); process.exit(2); }

async function svgFor(name) {
  const r = await fetch(`https://cdn.simpleicons.org/${name}`);
  return (await r.text()).replace(/fill="#[0-9A-Fa-f]{3,8}"/, 'fill="#ffffff"');
}

function htmlFor(bg, svg) {
  return `<!doctype html><html><head><meta charset="utf-8"/><style>
html,body{margin:0;padding:0;background:transparent;}
#root{width:${SIZE}px;height:${SIZE}px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  box-shadow: inset 0 0 0 2px rgba(255,255,255,0.22);
  background:${bg};}
#root svg{width:${Math.round(SIZE * 0.5)}px;height:${Math.round(SIZE * 0.5)}px;display:block;fill:#ffffff;}
</style></head><body><div id="root">${svg}</div></body></html>`;
}

async function renderOne(badge) {
  const svg = await svgFor(badge.name);
  const html = htmlFor(badge.bg, svg);
  const profile = mkdtempSync(join(tmpdir(), "cprof-"));
  const child = spawn(BROWSER, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    `--remote-debugging-port=${badge.port}`, `--user-data-dir=${profile}`,
    `--force-device-scale-factor=1`,
    `--window-size=800,400`, "about:blank",
  ], { stdio: ["ignore", "ignore", "ignore"] });

  let wsUrl;
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${badge.port}/json/version`);
      if (r.ok) { wsUrl = (await r.json()).webSocketDebuggerUrl; break; }
    } catch {}
    await new Promise((r) => setTimeout(r, 200));
  }
  if (!wsUrl) throw new Error("CDP did not start for " + badge.name);

  const ws = new WebSocket(wsUrl);
  await new Promise((r) => ws.addEventListener("open", r));
  let id = 0;
  const pending = new Map();
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id != null && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
    }
  });
  const raw = (method, params = {}, sessionId) => {
    const i = ++id;
    return new Promise((res, rej) => { pending.set(i, { resolve: res, reject: rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
  };
  const { targetInfos } = await raw("Target.getTargets");
  const t = targetInfos.find((x) => x.type === "page");
  const { sessionId } = await raw("Target.attachToTarget", { targetId: t.targetId, flatten: true });
  const s = (m, p) => raw(m, p, sessionId);

  await s("Page.enable");
  await s("Runtime.enable");
  await s("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });
  await s("Page.navigate", { url: "data:text/html;base64," + Buffer.from(html).toString("base64") });
  for (let i = 0; i < 50; i++) {
    const r = await s("Runtime.evaluate", { expression: "document.readyState" });
    if (r.result && r.result.value === "complete") break;
    await new Promise((r) => setTimeout(r, 50));
  }
  await new Promise((r) => setTimeout(r, 300));
  // Clip to the #root bbox (the proven flow from render-title-png.mjs).
  const box = await s("Runtime.evaluate", {
    expression: "(function(){var b=document.getElementById('root').getBoundingClientRect();return JSON.stringify({x:b.left,y:b.top,w:b.width,h:b.height});})()",
  });
  const { x, y, w, h } = JSON.parse(box.result.value);
  const shot = await s("Page.captureScreenshot", { format: "png", clip: { x, y, width: w, height: h, scale: 1 } });
  const out = join(OUT_DIR, `badge-${badge.name}.png`);
  writeFileSync(out, Buffer.from(shot.data, "base64"));
  console.error(`saved ${out} (${Math.round(w)}x${Math.round(h)})`);
  ws.close();
  child.kill();
}

for (const b of BADGES) {
  await renderOne(b);
}
process.exit(0);
