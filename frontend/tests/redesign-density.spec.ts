import { test, expect } from './fixtures';
import fs from 'node:fs';

const folder = '../examples/screenshots/redesign/final-density';
const sizes = [
  [1920, 1080],
  [1600, 900],
  [1440, 900],
  [1366, 768],
  [1280, 800],
  [1024, 768],
];

test('long input, large collaboration, dense findings and all requested desktop sizes', async ({
  page,
}) => {
  fs.mkdirSync(folder, { recursive: true });
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const response = await route.fetch({ url: 'http://127.0.0.1:8003' + path });
    if (path === '/api/process/import') {
      const data = await response.json();
      // Deliberately long metadata tests truncation without changing the fixture graph/XML.
      data.process.name =
        'Согласование комплексного договора технологического присоединения и проверка документов несколькими подразделениями энергоснабжающей компании';
      await route.fulfill({ response, json: data });
    } else if (path === '/api/process/audit') {
      const data = await response.json();
      data.issues = Array.from({ length: 6 }, (_, i) => ({
        code: 'VISUAL_FIXTURE_' + i,
        severity: i === 0 ? 'error' : i < 4 ? 'warning' : 'info',
        message: `Замечание ${i + 1}. Проверьте, явно ли описана ответственность подразделения за повторное согласование документов после получения исправленного комплекта от заявителя.`,
        node_ids: ['Check'],
        origin: i % 2 ? 'llm' : 'rule',
      }));
      await route.fulfill({ response, json: data });
    } else await route.fulfill({ response });
  });
  await page.goto('/');
  for (const [width, height] of sizes) {
    await page.setViewportSize({ width, height });
    await expect(page.getByRole('button', { name: 'Создать BPMN', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: `${folder}/home-${width}x${height}.png` });
  }
  await page.locator('#description').fill('Длинное описание процесса. '.repeat(650));
  await expect(page.locator('#description')).toHaveValue('Длинное описание процесса. '.repeat(650));
  const inputBox = await page.locator('#description').boundingBox();
  expect(inputBox!.height).toBeLessThanOrEqual(300);
  await page.screenshot({ path: folder + '/long-description.png' });
  await page
    .locator('input[type=file]')
    .setInputFiles('../examples/event-semantics/reference.bpmn');
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
  expect(await page.locator('.djs-shape').count()).toBeGreaterThan(20);
  for (const [width, height] of sizes) {
    await page.setViewportSize({ width, height });
    await page.getByRole('button', { name: 'По размеру', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Экспорт BPMN', exact: true })).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Подготовить изменения', exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <= innerWidth &&
          document.documentElement.scrollHeight <= innerHeight,
      ),
    ).toBe(true);
    await page.screenshot({ path: `${folder}/workspace-${width}x${height}.png` });
  }
  await page.getByRole('button', { name: 'Проверить', exact: true }).click();
  await expect(page.locator('.issue')).toHaveCount(6);
  await expect(page.locator('#command')).toHaveCount(0);
  await page.screenshot({ path: folder + '/doctor-top.png' });
  await page.locator('.issue').last().scrollIntoViewIfNeeded();
  await expect(page.locator('.issue').last()).toBeInViewport();
  expect(
    await page
      .locator('.issue')
      .last()
      .evaluate((el) => el.scrollWidth <= el.clientWidth),
  ).toBe(true);
  await page.screenshot({ path: folder + '/doctor-bottom.png' });
  expect(errors).toEqual([]);
});

test('handmade SVG mark remains legible at favicon and application sizes', async ({ page }) => {
  fs.mkdirSync(folder, { recursive: true });
  await page.route('**/logo-review', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: `<html lang="ru"><head><meta charset="UTF-8"></head><body style="margin:64px;background:#f6f6f3;color:#202631;font:14px Segoe UI;display:flex;gap:40px;align-items:center">${[16, 20, 24, 32, 48].map((size) => `<figure style="margin:0"><img src="/pulse-mark.svg" width="${size}" height="${size}" alt="PULSE ${size}px"><figcaption style="margin-top:16px">${size}px</figcaption></figure>`).join('')}</body></html>`,
    }),
  );
  await page.goto('/logo-review');
  for (const size of [16, 20, 24, 32, 48]) {
    await expect(page.getByRole('img', { name: `PULSE ${size}px`, exact: true })).toBeVisible();
  }
  await page.screenshot({ path: folder + '/mark-sizes.png' });
});
