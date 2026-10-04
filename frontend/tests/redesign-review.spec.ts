import { test, expect } from './fixtures';
import fs from 'node:fs';

test('visual review: home, loading, workspace, Doctor, History, Modify, Clarify and error', async ({
  page,
}) => {
  const folder = '../examples/screenshots/redesign/' + (process.env.PULSE_REVIEW_PASS || 'final');
  fs.mkdirSync(folder, { recursive: true });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/api/**', async (route) => {
    const u = new URL(route.request().url());
    const response = await route.fetch({ url: 'http://127.0.0.1:8001' + u.pathname });
    await route.fulfill({ response });
  });
  await page.goto('/');
  await page.screenshot({ path: folder + '/home.png' });
  await page
    .getByRole('button', { name: 'Использовать пример: Подключение к электросети', exact: true })
    .click();
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
  await page.screenshot({ path: folder + '/workspace.png' });
  await page.getByRole('button', { name: 'Скрыть Copilot', exact: true }).click();
  await expect(page.getByRole('tabpanel')).toHaveCount(0);
  await page.getByRole('button', { name: 'По размеру', exact: true }).click();
  await page.screenshot({ path: folder + '/canvas-expanded.png' });
  await page.getByRole('button', { name: 'Показать Copilot', exact: true }).click();
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
  await page.getByRole('button', { name: 'Проверить', exact: true }).click();
  await expect(page.locator('.checks')).toBeVisible();
  await page.screenshot({ path: folder + '/doctor.png' });
  await page.getByRole('tab', { name: 'История', exact: true }).click();
  await page.screenshot({ path: folder + '/history-empty.png' });
  await page.getByRole('tab', { name: 'Помощник', exact: true }).click();
  await page
    .locator('#command')
    .fill('После проверки документов добавь согласование руководителем');
  await page.getByRole('button', { name: 'Подготовить изменения', exact: true }).click();
  await expect(page.getByLabel('Предпросмотр изменений')).toBeVisible();
  await page.screenshot({ path: folder + '/modify-preview.png' });
  await page.getByRole('button', { name: 'Применить изменения', exact: true }).click();
  await expect(page.locator('.change-summary')).toBeVisible();
  await page.screenshot({ path: folder + '/changes.png' });
  await page.getByRole('tab', { name: 'История', exact: true }).click();
  await page.screenshot({ path: folder + '/history.png' });
  await page.getByRole('link', { name: 'PULSE', exact: true }).click();
  await page.getByRole('button', { name: 'Загрузить пример', exact: true }).click();
  await page.getByRole('button', { name: 'Описание с неоднозначностями', exact: true }).click();
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.locator('.clarify')).toBeVisible();
  await page.screenshot({ path: folder + '/clarify.png' });
  await page.getByRole('link', { name: 'PULSE', exact: true }).click();
  await page.locator('#description').fill('Произвольный процесс без встроенного примера');
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.screenshot({ path: folder + '/error.png' });
  expect(errors).toEqual([]);
});
