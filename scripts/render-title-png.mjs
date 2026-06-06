// Render a single Ultra-typeface title image to PNG via headless Chrome
// + CDP. Used for emails/*.py title_image() asset pairs. Usage:
//
//   node scripts/render-title-png.mjs \
//     --text "Bring someone with you" \
//     --color "#0F0B1F" \
//     --accent "#BC96FF" \
//     --out "../ahavah-web/public/email/title-referral.png"
//
// The accent color is applied to the trailing period (matches the
// family convention). Width 1056 (= 528 * 2x raster). Transparent bg.

import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, cur, i, arr) => {
    if (cur.startsWith("--")) acc.push([cur.slice(2), arr[i + 1]]);
    return acc;
  }, []),
);
if (!args.text || !args.color || !args.out) {
  console.error("usage: --text TEXT --color #HEX --accent #HEX --out PATH");
  process.exit(2);
}
const ACCENT = args.accent || args.color;
const TEXT = args.text;
const COLOR = args.color;
const OUT = resolve(args.out);
// Chrome viewport must be wider than the natural rendered text width
// or the captureScreenshot clip outside the viewport comes back
// transparent — producing PNGs cropped to the viewport edge instead
// of the bounding box. 2400px easily fits 4-word Ultra headlines at
// 92px. The captured region matches the actual text bbox, which the
// email template then scales down via width="528".
const WIDTH = 2400;

const CHROME_CANDIDATES = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
];
const BROWSER = CHROME_CANDIDATES.find((p) => existsSync(p));
if (!BROWSER) {
  console.error("No Chrome/Edge found");
  process.exit(2);
}

const html = `<!doctype html><html><head><meta charset="utf-8"/>
<link href="https://fonts.googleapis.com/css2?family=Ultra&display=block" rel="stylesheet"/>
<style>
html,body{margin:0;padding:0;background:transparent;}
#root{display:inline-block;font-family:'Ultra',serif;font-size:92px;
       line-height:1.0;color:${COLOR};padding:24px;white-space:nowrap;}
.acc{color:${ACCENT};}
</style></head>
<body><div id="root">${TEXT.replace(/\.$/, "")}<span class="acc">.</span></div>
<script>window.__ready=false;document.fonts.ready.then(()=>{window.__ready=true});</script>
</body></html>`;

const profile = mkdtempSync(join(tmpdir(), "render-title-"));
const PORT = 9444;
const child = spawn(
  BROWSER,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--hide-scrollbars",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    `--window-size=${WIDTH},400`,
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);

async function waitForCdp() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) return (await r.json()).webSocketDebuggerUrl;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("CDP did not start");
}

const wsUrl = await waitForCdp();
const ws = new WebSocket(wsUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0;
const pending = new Map();
const events = new Map();
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id != null) {
    const p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id);
    m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
  } else if (m.method) {
    (events.get(m.method) || []).forEach((cb) => cb(m.params));
  }
});
const send = (method, params = {}, sessionId) => {
  const i = ++id;
  return new Promise((res, rej) => {
    pending.set(i, { resolve: res, reject: rej });
    ws.send(JSON.stringify({ id: i, method, params, sessionId }));
  });
};
const on = (m, cb) => {
  if (!events.has(m)) events.set(m, []);
  events.get(m).push(cb);
};

const { targetInfos } = await send("Target.getTargets");
const t = targetInfos.find((x) => x.type === "page");
const { sessionId } = await send("Target.attachToTarget", {
  targetId: t.targetId,
  flatten: true,
});
const s = (m, p) => send(m, p, sessionId);

await s("Page.enable");
await s("Runtime.enable");
await s("Emulation.setDefaultBackgroundColorOverride", {
  color: { r: 0, g: 0, b: 0, a: 0 },
});
await s("Page.navigate", { url: "data:text/html;base64," + Buffer.from(html).toString("base64") });
await new Promise((r) => on("Page.loadEventFired", r));
// Wait for the Ultra webfont to load
for (let i = 0; i < 40; i++) {
  const r = await s("Runtime.evaluate", { expression: "window.__ready === true" });
  if (r.result && r.result.value === true) break;
  await new Promise((r) => setTimeout(r, 100));
}

// Get the #root bounding box
const box = await s("Runtime.evaluate", {
  expression:
    "(function(){var b=document.getElementById('root').getBoundingClientRect();return JSON.stringify({x:b.left,y:b.top,w:b.width,h:b.height});})()",
});
const { x, y, w, h } = JSON.parse(box.result.value);

const shot = await s("Page.captureScreenshot", {
  format: "png",
  clip: { x, y, width: w, height: h, scale: 1 },
});
writeFileSync(OUT, Buffer.from(shot.data, "base64"));
console.error(`saved ${OUT} (${Math.round(w)}x${Math.round(h)})`);

child.kill();
ws.close();
process.exit(0);
