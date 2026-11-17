// Открывает render.html в Chrome без окна и сохраняет кадр в PNG.
// node render_shot.mjs <url> <out.png>
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url, out] = process.argv.slice(2);
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const port = 9334;
const profile = mkdtempSync(join(tmpdir(), "jaw-render-"));
const chrome = spawn(CHROME, [
  "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "about:blank",
], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const finish = (code) => {
  chrome.kill();
  setTimeout(() => { rmSync(profile, { recursive: true, force: true }); process.exit(code); }, 300);
};

let ws;
for (let i = 0; i < 50 && !ws; i++) {
  try {
    const page = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((t) => t.type === "page");
    if (page) { ws = new WebSocket(page.webSocketDebuggerUrl); break; }
  } catch {}
  await sleep(200);
}
if (!ws) { console.error("Chrome не запустился"); finish(1); }
await new Promise((r) => ws.addEventListener("open", r));
let id = 0;
const pending = new Map();
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") console.error("Ошибка на странице:", m.params.exceptionDetails.exception?.description);
});
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (expr) => (await send("Runtime.evaluate", { expression: expr, returnByValue: true })).result?.result?.value;

await send("Runtime.enable");
await send("Page.navigate", { url });
let png;
for (let i = 0; i < 120 && !png; i++) { await sleep(500); png = await ev("window.__png"); }
if (!png) { console.error("Рендер не получился за 60 с"); finish(1); }
else {
  writeFileSync(out, Buffer.from(png.split(",")[1], "base64"));
  console.log("Кадр:", out);
  finish(0);
}
