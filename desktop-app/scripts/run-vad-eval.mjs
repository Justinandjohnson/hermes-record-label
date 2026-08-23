import { access } from "node:fs/promises";
import { chromium } from "playwright-core";

function readArg(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const url = readArg("--url", "http://localhost:8086/?vad_eval=1");
const candidates = process.platform === "win32"
  ? [
      "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
      "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    ]
  : process.platform === "darwin"
    ? ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    : ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"];

let executablePath;
for (const candidate of candidates) {
  try {
    await access(candidate);
    executablePath = candidate;
    break;
  } catch {
    // Try the next installed browser.
  }
}
if (!executablePath) throw new Error("Chrome or Edge is required for the browser VAD eval");

const browser = await chromium.launch({
  executablePath,
  headless: true,
  args: ["--autoplay-policy=no-user-gesture-required"],
});
try {
  const page = await browser.newPage();
  const browserErrors = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.locator("#root[data-vad-eval]").waitFor({ timeout: 20_000 });
  const result = JSON.parse(await page.locator("#root").innerText());
  result.browserErrors = browserErrors;
  console.log(JSON.stringify(result, null, 2));
  if (!result.passed || browserErrors.length) process.exitCode = 1;
} finally {
  await browser.close();
}
