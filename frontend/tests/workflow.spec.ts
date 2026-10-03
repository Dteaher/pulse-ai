import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: 'http://127.0.0.1:8001' + url.pathname });
    await route.fulfill({ response });
  });
});

async function generate(page: import('@playwright/test').Page, name = 'Подключение к электросети') {
  await page.goto('/');
  await page.getByRole('button', { name: 'Загрузить пример', exact: true }).click();
  await page.getByRole('button', { name, exact: true }).click();
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
}

test('основной сценарий: XML, ручное движение, AI, Doctor, экспорт, roundtrip и история', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await generate(page);
  await expect(page.locator('.canvas-footer')).toContainText('6 участников · 10 действий');
  await expect(page.getByRole('alert')).toHaveCount(0);
  const source = page.locator('.djs-shape[data-element-id="Check"]');
  await source.click();
  await expect(page.locator('.evidence')).toContainText('Оператор проверяет документы');
  const before = await source.boundingBox();
  if (!before) throw new Error('No node bounds');
  await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2);
  await page.mouse.down();
  await page.mouse.move(before.x + before.width / 2 + 15, before.y + before.height / 2 + 18, {
    steps: 12,
  });
  await page.mouse.up();
  await expect(page.locator('.projectbar')).toContainText('Ручные изменения');
  const moved = await source.boundingBox();
  expect(moved!.y).not.toBe(before.y);
  await page.getByRole('button', { name: 'Отменить ручное изменение', exact: true }).click();
  const undone = await source.boundingBox();
  expect(Math.abs(undone!.y - before.y)).toBeLessThan(1);
  await page
    .getByLabel('Что хотите изменить?', { exact: true })
    .fill('После проверки документов добавь согласование руководителем');
  await page.getByRole('button', { name: 'Подготовить изменения', exact: true }).click();
  await page.getByRole('button', { name: 'Применить изменения', exact: true }).click();
  await expect(page.locator('.canvas-footer')).toContainText('11 действий');
  await page.getByRole('button', { name: 'Проверить', exact: true }).click();
  await expect(page.locator('.checks')).toContainText('BPMN XML / XSD');
  await expect(page.locator('.checks .failed')).toHaveCount(0);
  await page
    .locator('.issue')
    .filter({ hasText: 'Проверьте, что каждая параллельная ветка' })
    .click();
  await expect(page.locator('.audit-highlight')).toHaveCount(2);
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Экспорт BPMN', exact: true }).click();
  const download = await downloadPromise;
  const file = path.resolve('test-results/exported.bpmn');
  await download.saveAs(file);
  const content = fs.readFileSync(file, 'utf8');
  expect(content).toContain('parallelGateway');
  expect(content).toContain('exclusiveGateway');
  expect(content).toContain('Согласовать заявку');
  await page.locator('input[type=file]').setInputFiles(file);
  await expect(page.locator('.canvas-footer')).toContainText('11 действий');
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page.getByRole('tab', { name: 'История', exact: true }).click();
  await expect(page.locator('.version')).toHaveCount(2);
  await page.locator('.version').last().click();
  await expect(page.locator('.canvas-footer')).toContainText('10 действий');
  expect(errors).toEqual([]);
});

test('неоднозначности блокируют XML до ответов', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Загрузить пример', exact: true }).click();
  await page.getByRole('button', { name: 'Описание с неоднозначностями', exact: true }).click();
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.locator('.clarify')).toContainText('Нужно уточнить 2 момента');
  await expect(page.locator('.bpmn-canvas')).toHaveCount(0);
  await page.getByRole('button', { name: 'Последовательно', exact: true }).click();
  await page.getByRole('button', { name: 'Уведомить клиента об отказе', exact: true }).click();
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click();
  await expect(page.locator('.djs-shape[data-element-id="LegalCheck"]')).toBeVisible();
  await expect(page.locator('.djs-shape[data-element-id="Fork"]')).toHaveCount(0);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('ручное переименование сохраняется в экспорте и учитывается аудитором', async ({ page }) => {
  await generate(page);
  await page.locator('.djs-shape[data-element-id="Check"]').dblclick();
  await page.locator('.djs-direct-editing-content').fill('Проверить комплект документов');
  await page.locator('.djs-direct-editing-content').press('Enter');
  await expect(page.locator('.projectbar')).toContainText('Ручные изменения');
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Экспорт BPMN', exact: true }).click();
  const download = await downloadPromise;
  const file = path.resolve('test-results/renamed.bpmn');
  await download.saveAs(file);
  expect(fs.readFileSync(file, 'utf8')).toContain('Проверить комплект документов');
  await page.getByRole('button', { name: 'Проверить', exact: true }).click();
  await expect(page.locator('.checks')).toBeVisible();
  await expect(page.locator('.checks .failed')).toHaveCount(0);
});

test('неверный текст в mock не подменяется примером', async ({ page }) => {
  await page.goto('/');
  await page
    .getByLabel('Описание процесса')
    .fill('Произвольный процесс, для которого требуется реальная модель.');
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Тестовый режим поддерживает только');
  await expect(page.locator('.bpmn-canvas')).toHaveCount(0);
});

test('уточнение нового процесса открывается после закрытия модального ввода', async ({ page }) => {
  await generate(page);
  await page.getByRole('button', { name: 'Создать', exact: true }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Описание с неоднозначностями', exact: true })
    .click();
  await page.getByRole('dialog').getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('.clarify')).toContainText('Нужно уточнить 2 момента');
  await expect(page.getByRole('button', { name: 'Экспорт BPMN', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Отменить уточнение', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Экспорт BPMN', exact: true })).toBeEnabled();
});

test('визуальная проверка 1366×768 и Full HD', async ({ page }) => {
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'От описания к BPMN-модели.' }),
  ).toBeVisible();
  fs.mkdirSync('../examples/screenshots', { recursive: true });
  await page.screenshot({ path: '../examples/screenshots/start-1366.png' });
  await generate(page);
  await page.screenshot({ path: '../examples/screenshots/editor-1366.png' });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.getByRole('button', { name: 'По размеру', exact: true }).click();
  await page.screenshot({ path: '../examples/screenshots/editor-1920.png' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('сложный импорт доступен для редактирования и экспорта без потери свойств', async ({
  page,
}) => {
  await generate(page);
  const pendingDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Экспорт BPMN', exact: true }).click();
  const originalDownload = await pendingDownload;
  const file = path.resolve('test-results/unsupported.bpmn');
  await originalDownload.saveAs(file);
  const xml = fs.readFileSync(file, 'utf8').replace('isExecutable="false"', 'isExecutable="true"');
  await page.locator('input[type=file]').setInputFiles({
    name: 'executable.bpmn',
    mimeType: 'application/xml',
    buffer: Buffer.from(xml),
  });
  await expect(page.locator('.feedback.success')).toContainText('AI-операции доступны только');
  await expect(page.getByLabel('Что хотите изменить?', { exact: true })).toBeDisabled();
  const preservedDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Экспорт BPMN', exact: true }).click();
  const saved = await preservedDownload;
  await saved.saveAs(file);
  expect(fs.readFileSync(file, 'utf8')).toContain('isExecutable="true"');
});

test('TO-BE UI: предложение требует применения, AS-IS сохраняется (mock транспорта)', async ({
  page,
}) => {
  await generate(page);
  await page.route('**/api/process/modify', async (route) => {
    const body = route.request().postDataJSON();
    const response = await page.request.post('http://127.0.0.1:8001/api/process/modify', {
      data: {
        process: body.process,
        command: 'После проверки документов добавь согласование руководителем',
      },
    });
    await route.fulfill({ response });
  });
  await page.getByRole('button', { name: 'Улучшить', exact: true }).click();
  await expect(page.locator('.proposal')).toContainText('AS-IS');
  await expect(page.locator('.proposal')).toContainText('TO-BE');
  await expect(page.locator('.canvas-footer')).toContainText('10 действий');
  await page.getByRole('button', { name: 'Применить', exact: true }).click();
  await expect(page.locator('.canvas-footer')).toContainText('11 действий');
  await page.getByRole('tab', { name: 'История', exact: true }).click();
  await expect(page.locator('.version')).toHaveCount(1);
});
