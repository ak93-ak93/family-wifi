// Browser check of the dashboard card with a mock Home Assistant.
//   node tests/card/run.js [screenshot-dir]     (needs Playwright + Chromium)
const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const dir = __dirname;
  const out = process.argv[2] || dir;
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 430, height: 900 }, deviceScaleFactor: 2 });
  const errors = [];
  p.on('pageerror', (e) => errors.push(String(e)));
  p.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  await p.goto('file://' + path.join(dir, 'preview.html'));
  const root = p.locator('family-wifi-card');
  const names = await root.locator('.name').allTextContents();
  const shot = async (file) => p.locator('family-wifi-card').screenshot({ path: path.join(out, file) });
  await shot('light.png');
  // Interactions
  await root.locator('button[data-act="pause"][data-target="Alex"][data-value="30 min"]').click();
  await root.locator('button[data-act="internet"][data-target="Sam"]').click();
  await root.locator('button[data-act="open"][data-target="Alex"][data-value="sites"]').click();
  await root.locator('.panel button[data-act="option"][data-value="Video"]').click();
  await root.locator('.panel button[data-act="duration"][data-value="until 7am"]').click();
  await shot('panel.png');
  await root.locator('.panel button[data-act="apply"]').click();
  await root.locator('button[data-act="restore"][data-target="Sam"]').click();
  await root.locator('button[data-act="extra"][data-target="Sam"]').click();
  const alexHasExtra = await root.locator('button[data-act="extra"][data-target="Alex"]').count();
  await p.waitForTimeout(100);
  const calls = await p.evaluate(() => window.calls);
  const toasts = await p.evaluate(() => window.toasts);
  await p.evaluate(() => document.body.classList.add('dark'));
  await shot('dark.png');
  const expected = [
    ['family_wifi', 'pause', { target: 'Alex', duration: '30 min' }],
    ['switch', 'turn_on', { entity_id: 'switch.sam_internet' }],
    ['family_wifi', 'block_websites', { target: 'Alex', websites: 'Video', duration: 'until 7am' }],
    ['family_wifi', 'restore', { target: 'Sam' }],
    ['button', 'press', { entity_id: 'button.sam_30_min' }],
  ];
  const ok = JSON.stringify(calls) === JSON.stringify(expected) && errors.length === 0 && names.join() === 'Alex,Sam' && alexHasExtra === 0;
  console.log(JSON.stringify({ ok, names, calls, toasts, errors }, null, 1));
  await b.close();
  process.exit(ok ? 0 : 1);
})();
