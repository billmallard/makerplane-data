// SPDX-License-Identifier: Apache-2.0
// Drives inspect.html in headless Chromium and prints what the page shows.
//   CHROME=<chrome-headless-shell> PLAYWRIGHT_CORE=<playwright-core dir> \
//     node drive.mjs http://127.0.0.1:8771 "/inspect.html?..." out.png [sql]
const { chromium } = await import(process.env.PLAYWRIGHT_CORE + "/index.mjs");
const [base, url, out, sql] = process.argv.slice(2);
const exe = process.env.CHROME;
const b = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
const p = await b.newPage({ viewport: { width: 1200, height: 1000 } });
const errs = []; p.on("pageerror", (e) => errs.push(e.message)); p.on("console", (m) => m.type() === "error" && errs.push(m.text()));
// Production serves the page and the packs from one origin; locally the
// manifest's absolute pack URLs are routed to the local proxy.
await p.route("https://navdata.aerocommons.org/packs/**", async (route) => {
  const r = await fetch(base + new URL(route.request().url()).pathname);
  route.fulfill({ status: r.status, body: Buffer.from(await r.arrayBuffer()) });
});
await p.goto(base + url);
await p.waitForFunction(() => document.querySelector("#status")?.textContent || document.querySelector("li.bad"), null, { timeout: 240000 });
if (sql) { await p.fill("#sql", sql); await p.click("#run"); await p.waitForTimeout(500); }
const r = await p.evaluate(() => ({
  trust: [...document.querySelectorAll("#trust li")].map((l) => (l.className === "bad" ? "FAIL: " : "ok: ") + l.textContent),
  status: document.querySelector("#status").textContent,
  queryShown: !document.querySelector("#qsec").hidden,
  err: document.querySelector("#result .err")?.textContent || "",
  head: [...document.querySelectorAll("#result th")].map((t) => t.textContent).slice(0, 8),
  row1: [...(document.querySelectorAll("#result tr")[1]?.cells || [])].map((t) => t.textContent).slice(0, 8),
  share: document.querySelector("#share").getAttribute("href"),
}));
console.log(JSON.stringify({ ...r, errs }, null, 1));
if (out) await p.screenshot({ path: out, fullPage: false });
await b.close();
