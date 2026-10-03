import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: 'http://127.0.0.1:8001' + url.pathname });
    await route.fulfill({ response });
  });
});

async function example(page: Page) {
  await page.getByRole('button', { name: 'Загрузить пример', exact: true }).click();
  await page.getByRole('button', { name: 'Подключение к электросети', exact: true }).click();
}
async function create(page: Page) {
  await example(page);
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.locator('.bpmn-canvas')).toBeVisible();
}

test('logo returns home without API calls or reload and allows another process', async ({
  page,
}) => {
  await page.goto('/');
  await create(page);
  await expect(page.locator('footer')).toHaveCount(0);
  let requests = 0,
    navigations = 0;
  page.on('request', (r) => {
    if (r.url().includes('/api/')) requests++;
  });
  page.on('framenavigated', () => navigations++);
  await page.getByRole('link', { name: 'PULSE', exact: true }).click();
  await expect(page.locator('#description')).toBeFocused();
  await expect(page.locator('#description')).toHaveValue('');
  await expect(page.locator('.bpmn-canvas')).toHaveCount(0);
  expect(requests).toBe(0);
  expect(navigations).toBe(0);
  await create(page);
  await expect(page.getByRole('button', { name: 'Экспорт BPMN', exact: true })).toBeEnabled();
  await page.getByRole('tab', { name: 'Помощник', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Проверка', exact: true })).toBeFocused();
  await expect(page.getByRole('tabpanel')).toContainText('Проверка процесса');
  await page.keyboard.press('End');
  await expect(page.getByRole('tabpanel')).toContainText('История изменений');
});

test('example picker closes and new-process dialog traps focus and closes with Escape', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Загрузить пример', exact: true }).click();
  await page.keyboard.press('Escape');
  await expect(page.locator('.example-menu')).toHaveCount(0);
  await create(page);
  await page.getByRole('button', { name: 'Создать', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Создать BPMN', exact: true }).focus();
  await page.keyboard.press('Tab');
  await expect(dialog.getByRole('button', { name: 'Закрыть', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Создать', exact: true })).toBeFocused();
});

test('loading keeps button width stable and prevents duplicate generation', async ({ page }) => {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  let calls = 0;
  await page.route('**/api/process/generate', async (route) => {
    calls++;
    const response = await route.fetch({ url: 'http://127.0.0.1:8001/api/process/generate' });
    await wait;
    await route.fulfill({ response });
  });
  await page.goto('/');
  await example(page);
  const button = page.getByRole('button', { name: 'Создать BPMN', exact: true });
  const before = await button.boundingBox();
  await button.click();
  await expect(button).toBeDisabled();
  await expect(page.locator('.working')).toContainText('PULSE анализирует');
  expect((await button.boundingBox())!.width).toBe(before!.width);
  await page.keyboard.press('Control+Enter');
  await page.getByRole('link', { name: 'PULSE', exact: true }).click({force:true});
  expect(calls).toBe(1);
  await expect(page.locator('.working')).toBeVisible();
  await page.screenshot({ path: '../examples/screenshots/ui-polish/loading-1366.png' });
  release();
  await expect(page.locator('.bpmn-canvas')).toBeVisible();
  await expect(page.locator('.working')).toHaveCount(0);
});

test('desktop breakpoints preserve controls, independent scroll and attribution', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  fs.mkdirSync('../examples/screenshots/ui-polish', { recursive: true });
  await page.goto('/');
  for (const width of [1920, 1440, 1366, 1280, 1024]) {
    await page.setViewportSize({ width, height: 768 });
    await expect(page.locator('#description')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Создать BPMN', exact: true })).toBeVisible();
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <= innerWidth &&
          document.documentElement.scrollHeight <= innerHeight,
      ),
    ).toBe(true);
    await page.screenshot({ path: `../examples/screenshots/ui-polish/home-${width}.png` });
  }
  await create(page);
  for (const width of [1920, 1440, 1366, 1280, 1024]) {
    await page.setViewportSize({ width, height: 768 });
    await page.getByRole('button', { name: 'По размеру', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Экспорт BPMN', exact: true })).toBeVisible();
    const command = await page
      .getByRole('button', { name: 'Подготовить изменения', exact: true })
      .boundingBox();
    expect(command!.y + command!.height).toBeLessThanOrEqual(768);
    await expect(page.locator('.bjs-powered-by')).toBeVisible();
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <= innerWidth &&
          document.documentElement.scrollHeight <= innerHeight,
      ),
    ).toBe(true);
    await page.screenshot({ path: `../examples/screenshots/ui-polish/workspace-${width}.png` });
  }
  expect(errors).toEqual([]);
});
