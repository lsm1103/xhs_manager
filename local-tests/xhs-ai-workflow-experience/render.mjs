import { chromium } from 'playwright';
import { resolve } from 'node:path';
import { mkdir } from 'node:fs/promises';

const root = resolve(process.cwd());
const output = resolve(root, 'output');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
});
const page = await browser.newPage({ deviceScaleFactor: 1 });
await page.goto(`file://${resolve(root, 'index.html')}`, { waitUntil: 'networkidle' });
await page.evaluate(() => document.fonts.ready);
for (let number = 1; number <= 6; number += 1) {
  const id = `xhs-${String(number).padStart(2, '0')}`;
  await page.locator(`#${id}`).screenshot({ path: resolve(output, `xhs-${String(number).padStart(2, '0')}-${number === 1 ? 'cover' : 'workflow'}.png`) });
}
await browser.close();
