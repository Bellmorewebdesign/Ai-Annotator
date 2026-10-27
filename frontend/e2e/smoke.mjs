// End-to-end smoke test: real browser, real API and worker, scripted AI (test mode).
//
// Prerequisites: the backend running with ANNOTATOR_ENV=test ANNOTATOR_AI_PROVIDER=fake,
// the worker running, and the frontend served at E2E_BASE_URL (default http://localhost:3000).
// Usage: npm run e2e -- path/to/document.pdf
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.env.E2E_BASE_URL ?? "http://localhost:3000";
const doc = process.argv[2];
const shots = process.env.E2E_SCREENSHOTS ?? "test-results";
if (!doc) {
  console.error("Usage: npm run e2e -- <document.pdf>");
  process.exit(2);
}
mkdirSync(shots, { recursive: true });

function check(cond, message) {
  if (!cond) throw new Error(`E2E check failed: ${message}`);
  console.log(`  ok  ${message}`);
}

const browser = await chromium.launch();
const errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

  await page.goto(base);
  await page.getByText("Upload a document").first().waitFor();
  check(await page.getByText("Test mode").isVisible(), "test mode is clearly labelled");

  await page.setInputFiles('input[aria-label="Choose a document"]', doc);
  await page.getByText(/\d+ pages?/).first().waitFor();
  check(true, "document accepted and page count shown");
  await page.fill("#instructions", "Highlight the main claims in yellow and evidence in green.");
  await page.getByRole("button", { name: "Annotate document" }).click();
  await page.waitForURL(/\/jobs\/[A-Za-z0-9_-]{22}$/);
  check(true, "job created and job URL opened");

  await page.getByText("Your document is ready.").waitFor({ timeout: 90_000 });
  check(true, "job completed");
  await page.locator("canvas[aria-label='Page 1']").waitFor();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${shots}/results.png` });

  const annotated = await page.request.get(
    await page.getByRole("link", { name: "Download annotated PDF" }).getAttribute("href"),
  );
  const body = await annotated.body();
  check(annotated.ok() && body.subarray(0, 5).toString() === "%PDF-", "annotated PDF downloads");
  check(body.includes(Buffer.from("/Highlight")), "download contains real highlight annotations");

  await page.getByRole("tab", { name: /Annotations/ }).click();
  const items = await page.locator("[role=tabpanel] li").count();
  check(items > 0, `annotation list shows ${items} items`);

  // Leave and return: the job must still be there.
  const url = page.url();
  await page.goto(base);
  await page.getByText("Recent documents").waitFor();
  await page.goto(url);
  await page.getByText("Your document is ready.").waitFor();
  check(true, "completed job reopens from its URL");

  check(errors.length === 0, `no browser errors (${errors.join(" | ")})`);
  console.log("E2E passed");
} finally {
  await browser.close();
}
