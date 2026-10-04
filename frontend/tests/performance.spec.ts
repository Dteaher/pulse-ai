import { test, expect } from './fixtures';
import fs from 'node:fs';
import path from 'node:path';

const folder = path.resolve('../examples/performance');
const load = (name: string) => JSON.parse(fs.readFileSync(path.join(folder, `before-${name}-response.json`), 'utf8'));

for (const name of ['simple', 'complex']) {
  test(`performance: ${name} actual MultiAI XML imports and records local timing`, async ({ page }) => {
    await page.goto('/');
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: 'Открыть .bpmn', exact: true }).click();
    await (await chooser).setFiles(path.join(folder, `before-${name}.bpmn`));
    await expect(page.locator('.djs-element[data-element-id]').first()).toBeVisible();
    const measurements = await page.evaluate(() => performance.getEntriesByType('measure')
      .filter(entry => entry.name.startsWith('pulse.frontend_bpmn_import.'))
      .map(entry => ({ name: entry.name, duration_ms: entry.duration })));
    expect(measurements.some(entry => entry.name.endsWith('.render'))).toBe(true);
    expect(measurements.every(entry => entry.duration_ms >= 0)).toBe(true);
    fs.writeFileSync(path.join(folder, `frontend-${name}.json`), JSON.stringify(measurements, null, 2));
    if (name === 'complex') await page.screenshot({ path: path.join(folder, 'frontend-complex.png') });
  });
}

test('performance: rapid submit sends one request and preserves the tab scope', async ({ page }) => {
  let calls = 0;
  let firstScope = '';
  await page.route('**/api/health', route => route.fulfill({ json: { configured: true, provider: 'multiai' } }));
  await page.route('**/api/process/generate', async route => {
    calls++;
    firstScope = route.request().headers()['x-pulse-session'];
    expect(firstScope).toMatch(/^[a-zA-Z0-9_-]{16,128}$/);
    // Hold the transport long enough to exercise the actual loading guard.
    await new Promise(resolve => setTimeout(resolve, 150));
    await route.fulfill({ json: load('simple') });
  });
  await page.goto('/');
  await page.locator('textarea').first().fill('Оператор регистрирует заявку, проверяет документы и принимает либо отклоняет заявку.');
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await page.locator('textarea').first().press('Control+Enter');
  await expect(page.locator('.djs-element[data-element-id]').first()).toBeVisible();
  expect(calls).toBe(1);
  await page.reload();
  const scope = await page.evaluate(() => sessionStorage.getItem('pulse.requestScope'));
  expect(scope).toBe(firstScope);
});
