const { chromium } = require('playwright');
const path = require('path');

(async () => {
  const outDir = path.join(__dirname, 'video');
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 800, height: 740 },
    recordVideo: {
      dir: outDir,
      size: { width: 800, height: 740 }
    }
  });

  const page = await context.newPage();
  await page.goto('http://localhost:7788');
  await page.waitForLoadState('networkidle');

  // Let knights patrol for 5s
  await page.waitForTimeout(5000);

  // Trigger scan
  await page.click('#scb');

  // Wait for full scan sequence (gather→scan→vote→approve→disperse = ~13s)
  await page.waitForTimeout(13000);

  // Patrol a bit more after
  await page.waitForTimeout(4000);

  await context.close();
  await browser.close();

  const fs = require('fs');
  const files = fs.readdirSync(outDir).filter(f => f.endsWith('.webm'));
  if (files.length > 0) {
    const src = path.join(outDir, files[files.length - 1]);
    const dst = path.join(__dirname, 'war-room.webm');
    fs.renameSync(src, dst);
    console.log('Saved: ' + dst);
  }
})();
