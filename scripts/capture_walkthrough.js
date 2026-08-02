/*
 * Regenerate the homepage walkthrough screenshots in frontend/img/.
 *
 * These are real captures of the running search UI, so they must be refreshed
 * whenever the search panel, filter chips, or result card markup changes.
 *
 * Both themes are captured: <name>.png for light and <name>-dark.png for dark.
 * home.html points at both through data-light/data-dark and theme.js swaps
 * them, so a light screenshot never glares on a dark page.
 *
 * Usage (with the stack already running on http://127.0.0.1:8080):
 *
 *   npm install puppeteer-core
 *   node scripts/capture_walkthrough.js
 *
 * Set CHROME_PATH if Chrome is not at the default Windows location, and
 * BASE_URL to capture against a different host.
 */
const puppeteer = require("puppeteer-core");
const path = require("path");

const CHROME = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const BASE = process.env.BASE_URL || "http://127.0.0.1:8080";
const OUT = path.join(__dirname, "..", "frontend", "img");

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Capture the region spanning one or more elements, with a little breathing room.
async function shotRegion(page, selectors, file, pad = 10) {
  const boxes = [];
  for (const selector of selectors) {
    const element = await page.$(selector);
    if (!element) {
      throw new Error(`missing selector: ${selector}`);
    }
    boxes.push(await element.boundingBox());
  }
  const x = Math.min(...boxes.map((box) => box.x));
  const y = Math.min(...boxes.map((box) => box.y));
  const right = Math.max(...boxes.map((box) => box.x + box.width));
  const bottom = Math.max(...boxes.map((box) => box.y + box.height));
  await page.screenshot({
    path: path.join(OUT, file),
    clip: { x: x - pad, y: y - pad, width: right - x + pad * 2, height: bottom - y + pad * 2 },
  });
  console.log(`  wrote ${file}`);
}

async function captureTheme(browser, theme) {
  const suffix = theme === "dark" ? "-dark" : "";
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 1200, deviceScaleFactor: 2 });
  // Seed the stored choice so the page paints in the right theme immediately.
  await page.evaluateOnNewDocument((value) => {
    try {
      localStorage.setItem("course-search-theme", value);
    } catch (error) {
      /* storage unavailable */
    }
  }, theme);

  const filtered =
    `${BASE}/courses?q=calculus&prefix=MATH&term=fall` +
    `&location=${encodeURIComponent("College Station")}` +
    `&graduation_requirement=${encodeURIComponent("Univ Req-Writing Intensive")}`;

  console.log(`${theme}:`);

  // Steps 1, 2 and 4 come from the default panel.
  await page.goto(`${BASE}/courses`, { waitUntil: "networkidle0" });
  await page.waitForSelector("#term option", { timeout: 15000 });
  await wait(1200); // the live term labels arrive with /health
  await page.click("#query");
  await page.type("#query", "prerequisite calculus", { delay: 12 });
  await wait(300);
  await shotRegion(page, [".field-search"], `step1-search${suffix}.png`);
  await shotRegion(page, [".field-term"], `step2-term${suffix}.png`);

  await page.click("#more-filters > summary");
  await wait(600);
  await shotRegion(page, ["#more-filters"], `step4-more-filters${suffix}.png`);

  // Steps 3, 5 and 6 need a search with filters actually applied.
  await page.goto(filtered, { waitUntil: "networkidle0" });
  await page.waitForSelector("#results article", { timeout: 20000 });
  await wait(1200);
  await shotRegion(page, [".field-prefix", ".field-rank"], `step3-filters${suffix}.png`);
  await shotRegion(page, ["#active-filters"], `step5-chips${suffix}.png`);
  await shotRegion(page, ["#results article:first-child"], `step6-result${suffix}.png`);

  await page.close();
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--hide-scrollbars"],
  });
  await captureTheme(browser, "light");
  await captureTheme(browser, "dark");
  await browser.close();
  console.log("done");
})().catch((error) => {
  console.error("capture failed:", error.message);
  process.exit(1);
});
