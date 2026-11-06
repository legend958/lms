import { chromium } from "@playwright/test";

const base = process.env.PROBE_BASE ?? "http://localhost:3000";
const channel = process.env.PROBE_CHANNEL || undefined;
const encoding = process.env.PROBE_ENCODING || undefined;
const browser = await chromium.launch(channel ? { channel } : {});
const page = await browser.newPage();
if (encoding) {
  await page.route("**/_next/static/**", (route) =>
    route.continue({
      headers: { ...route.request().headers(), "accept-encoding": encoding },
    }),
  );
}
const pending = new Map();
page.on("request", (request) => pending.set(request.url(), request.resourceType()));
page.on("requestfinished", (request) => pending.delete(request.url()));
page.on("requestfailed", (request) => {
  pending.delete(request.url());
  console.log("FAILED", request.url(), request.failure()?.errorText);
});
const started = Date.now();
try {
  await page.goto(`${base}/login`, { waitUntil: "load", timeout: 15000 });
  console.log("load ok in", Date.now() - started, "ms");
} catch {
  console.log("load FAILED after", Date.now() - started, "ms");
}
await page.waitForTimeout(2000);
console.log("pending:", [...pending.keys()].map((url) => url.split("/").pop()));
await browser.close();
