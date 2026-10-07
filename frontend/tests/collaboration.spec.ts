import { test, expect } from './fixtures';
import fs from 'node:fs';
import path from 'node:path';

const folder = path.resolve('../examples/collaboration');
const candidate = JSON.parse(fs.readFileSync(path.join(folder, 'modified.json'), 'utf8'));

test('collaboration: bpmn-js import, messages, manual edit, Modify preview and exact Undo', async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/api/**', async (route) => {
    const url = route.request().url().replace('http://127.0.0.1:5181', 'http://127.0.0.1:8003');
    await route.fulfill({ response: await route.fetch({ url }) });
  });
  await page.route('**/api/process/modify', async (route) => {
    const body = route.request().postDataJSON();
    expect(body.process.pools).toHaveLength(2);
    expect(body.process.participants.find((r: { id: string }) => r.id === 'Client')).toMatchObject({
      kind: 'external',
      pool_id: 'ClientPool',
    });
    expect(body.process.message_flows).toHaveLength(5);
    await route.fulfill({ json: candidate });
  });
  async function xml() {
    const wait = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Экспорт BPMN', exact: true }).click();
    return fs.readFileSync((await (await wait).path())!, 'utf8');
  }
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles(path.join(folder, 'reference.bpmn'));
  await expect(page.locator('.djs-shape[data-element-id="ClientPool"]')).toBeVisible();
  await expect(page.locator('.djs-shape[data-element-id="CompanyPool"]')).toBeVisible();
  await expect(page.locator('.djs-connection[data-element-id^="Message"]')).toHaveCount(5);
  await expect(page.locator('#command')).toBeEnabled();
  const task = page.locator('.djs-shape[data-element-id="Check"]');
  const box = (await task.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 15, box.y + box.height / 2 + 10, { steps: 8 });
  await page.mouse.up();
  const before = await xml();
  const imported = await request.post('http://127.0.0.1:8003/api/process/import', {
    data: { xml: before },
  });
  expect(imported.status()).toBe(200);
  expect((await imported.json()).process.message_flows).toHaveLength(5);
  await page.locator('#command').fill('Уточни название доработки документов');
  await page.getByRole('button', { name: 'Подготовить изменения', exact: true }).click();
  await expect(page.getByLabel('Предпросмотр изменений')).toBeVisible();
  expect(await xml()).toBe(before);
  await page.getByRole('button', { name: 'Применить изменения', exact: true }).click();
  await expect(page.locator('#command')).toBeEnabled();
  expect(await xml()).toContain('Доработать и уточнить документы');
  await expect(page.locator('.djs-connection[data-element-id^="Message"]')).toHaveCount(5);
  await page.getByRole('button', { name: 'Отменить AI-изменение', exact: true }).click();
  await expect(page.locator('#command')).toBeEnabled();
  expect(await xml()).toBe(before);
  expect(errors).toEqual([]);
  await page.getByRole('button', { name: 'По размеру', exact: true }).click();
  await page.screenshot({ path: path.join(folder, 'reference-1366.png') });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.getByRole('button', { name: 'По размеру', exact: true }).click();
  await page.screenshot({ path: path.join(folder, 'reference-1920.png') });
});
