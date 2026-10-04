import { test, expect } from './fixtures';
import fs from 'node:fs';
import path from 'node:path';

const folder = path.resolve('../examples/performance-v2');
for (const name of ['revised-multiai-patch', 'revised-vertex_gemini-patch', 'multiai-full', 'rename']) {
  test(`performance v2: ${name} full complex XML imports into real bpmn-js`, async ({ page }) => {
    await page.goto('/');
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: 'Открыть .bpmn', exact: true }).click();
    await (await chooser).setFiles(path.join(folder, `${name}.bpmn`));
    await expect(page.locator('.djs-element[data-element-id]').first()).toBeVisible();
    await expect(page.locator('#command')).toBeEnabled();
    await expect(page.locator('.error-box')).toHaveCount(0);
    if (name === 'revised-multiai-patch') await page.screenshot({ path: path.join(folder, 'patch-edge.png') });
  });
}

test('performance v2: exact rename still has preview, apply, version and Undo', async ({ page }) => {
  const base = JSON.parse(fs.readFileSync(path.resolve('../examples/pipeline-resilience/exact-complex-polished-response.json'), 'utf8'));
  const proposal = JSON.parse(fs.readFileSync(path.join(folder, 'rename-response.json'), 'utf8'));
  await page.route('**/api/health', route => route.fulfill({ json: { configured: true, provider: 'multiai' } }));
  await page.route('**/api/process/generate', route => route.fulfill({ json: base }));
  await page.route('**/api/process/modify', route => {
    expect(route.request().postDataJSON().instruction).toBe('Переименуй роль Оператор в Специалист');
    return route.fulfill({ json: proposal });
  });
  await page.goto('/');
  await page.locator('textarea').first().fill(base.process.description);
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.locator('#command')).toBeEnabled();
  await page.locator('#command').fill('Переименуй роль Оператор в Специалист');
  await page.getByRole('button', { name: 'Подготовить изменения', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Применить изменения', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Применить изменения', exact: true }).click();
  await expect(page.locator('.djs-label').filter({ hasText: 'Специалист' })).toBeVisible();
  await page.screenshot({ path: path.join(folder, 'rename-edge.png') });
  await page.getByRole('button', { name: 'Отменить AI-изменение', exact: true }).click();
  await expect(page.locator('.djs-label').filter({ hasText: 'Оператор' })).toBeVisible();
  await expect(page.locator('.djs-label').filter({ hasText: 'Специалист' })).toHaveCount(0);
});
