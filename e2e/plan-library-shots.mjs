// Screenshots of the ready-made plan library, in the real web app against the local stack.
//
//   cd e2e && PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers APP_URL=http://localhost:5173 node plan-library-shots.mjs <outDir>
//
// Needs: the api on :8000 with DEV_ECHO_OTP=1 and no mailer (the sign-up form's
// "Use this code" button is how the run signs in), Vite on :5173 with
// VITE_API_BASE_URL pointing at that api. Signs up a fresh practice, screenshots the
// New-project picker, then creates one project per plan and captures its Plan (2D)
// and 3D tabs. Chromium is launched in the NEW headless mode: the pinned Playwright
// 1.48 asks for the old one, which recent Chromium builds removed.
import { chromium } from '@playwright/test';

const APP = process.env.APP_URL ?? 'http://localhost:5173';
const OUT = process.argv[2] ?? './shots';
const PLANS = [
  ['blr-30x40-g1-3bhk', /Bengaluru 30 × 40, G\+1 3BHK/],
  ['hyd-30x40-g1-3bhk', /Hyderabad 30 × 40, G\+1 3BHK/],
  ['blr-30x50-g2-3bhk', /Bengaluru 30 × 50, G\+2 3BHK/],
  ['blr-40x60-g2-4bhk', /Bengaluru 40 × 60, G\+2 4BHK/],
];

const GL = [
  '--use-gl=angle',
  '--use-angle=swiftshader',
  '--enable-unsafe-swiftshader',
  '--ignore-gpu-blocklist',
];
async function launch() {
  const attempts = [
    { label: 'new headless via channel', opts: { channel: 'chromium', args: GL } },
    { label: 'new headless via flag', opts: { headless: false, args: ['--headless=new', ...GL] } },
    {
      label: 'sandbox chromium binary',
      opts: {
        executablePath: process.env.CHROME_BIN ?? '/opt/pw-browsers/chromium',
        headless: false,
        args: ['--headless=new', ...GL],
      },
    },
  ];
  for (const a of attempts) {
    try {
      const b = await chromium.launch(a.opts);
      console.log('  launched: ' + a.label);
      return b;
    } catch (e) {
      console.log('  launch failed (' + a.label + '): ' + String(e).split('\n')[0].slice(0, 120));
    }
  }
  throw new Error('no launch option worked');
}
const browser = await launch();
let page;
async function main() {
  page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', (e) => console.log('  pageerror:', String(e).slice(0, 200)));
  const log = (m) => console.log('  ' + m);

  // 1 — sign up a fresh practice through the UI (dev OTP echo shows "Use this code")
  await page.goto(APP + '/');
  await page
    .getByRole('button', { name: /create an account/i })
    .first()
    .click();
  const email = `shots-${Date.now().toString(36)}@studio.test`;
  await page.getByLabel(/practice name/i).fill('Plan Library Studio');
  await page.getByLabel(/your name/i).fill('Ar. Screenshot');
  await page.getByLabel(/work email/i).fill(email);
  await page
    .getByRole('button', { name: /create account|sign up|continue/i })
    .first()
    .click();
  await page.getByRole('button', { name: /use this code/i }).click({ timeout: 20_000 });
  await page.waitForURL((u) => !/\/login/.test(u.pathname), { timeout: 20_000 });
  log('signed up as ' + email);

  // 2 — the picker
  await page
    .getByRole('button', { name: /new project/i })
    .first()
    .click();
  await page
    .getByRole('radiogroup', { name: /start from a template/i })
    .waitFor({ timeout: 20_000 });
  await page.waitForTimeout(800);
  const dialog = page.getByRole('dialog').first();
  await dialog.screenshot({ path: `${OUT}/01-template-picker.png` });
  log('picker: ' + (await page.getByRole('radio').count()) + ' cards');
  await page.keyboard.press('Escape');

  // 3 — one project per plan: Plan tab (2D) and 3D tab
  let n = 2;
  for (const [id, name] of PLANS) {
    await page.goto(APP + `/?new=1&template=${id}`);
    await page
      .getByRole('radiogroup', { name: /start from a template/i })
      .waitFor({ timeout: 20_000 });
    const card = page.getByRole('radio').filter({ hasText: name }).first();
    await card.click();
    await page.getByLabel(/project name/i).fill(`Library ${id}`);
    await page.getByRole('button', { name: /^create project$/i }).click();
    await page.waitForURL(/\/projects\/[0-9a-f-]{36}/, { timeout: 30_000 });
    const projectId = page.url().match(/\/projects\/([0-9a-f-]{36})/)[1];
    await page.goto(`${APP}/projects/${projectId}/plan`);
    await page.locator('canvas').first().waitFor({ timeout: 30_000 });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${OUT}/${String(n).padStart(2, '0')}-${id}-plan.png` });
    await page.goto(`${APP}/projects/${projectId}/3d`);
    await page.locator('canvas').first().waitFor({ timeout: 30_000 });
    await page.waitForTimeout(3500);
    await page.screenshot({ path: `${OUT}/${String(n).padStart(2, '0')}-${id}-3d.png` });
    log(`${id}: project ${projectId} — plan + 3d captured`);
    n += 1;
  }
}
try {
  await main();
} catch (e) {
  console.log('  FAILED: ' + String(e).split('\n')[0].slice(0, 200));
  try {
    await page.screenshot({ path: `${OUT}/zz-failure.png` });
    const text = (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 600);
    console.log('  url: ' + page.url());
    console.log('  page text: ' + text);
  } catch (diagErr) {
    console.log('  (no diagnostics: ' + String(diagErr).slice(0, 80) + ')');
  }
} finally {
  await browser.close();
}
