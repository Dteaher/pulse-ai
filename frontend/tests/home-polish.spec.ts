import { test, expect } from './fixtures';
import fs from 'node:fs';

test('home polish: laptop layouts, examples, keyboard generation and entire brand link', async ({
  page,
}) => {
  const folder = '../examples/screenshots/final-polish';
  fs.mkdirSync(folder, { recursive: true });
  const consoleProblems: string[] = [];
  let generations = 0;
  page.on('pageerror', (error) => consoleProblems.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error' || message.type() === 'warning') {
      consoleProblems.push(message.text());
    }
  });
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/process/generate') generations++;
    const response = await route.fetch({ url: 'http://127.0.0.1:8003' + path });
    await route.fulfill({ response });
  });
  await page.goto('/');
  const create = page.getByRole('button', { name: 'Создать BPMN', exact: true });
  await expect(create).toBeDisabled();
  for (const [width, height] of [
    [1920, 1080],
    [1600, 900],
    [1440, 900],
    [1366, 768],
    [1280, 800],
    [1024, 768],
  ]) {
    await page.setViewportSize({ width, height });
    await expect(create).toBeInViewport();
    await expect(
      page.getByRole('button', {
        name: 'Использовать пример: Подключение к электросети',
        exact: true,
      }),
    ).toBeInViewport();
    await expect(page.getByRole('heading', { name: 'Три опорные точки' })).toBeInViewport();
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <= innerWidth &&
          document.documentElement.scrollHeight <= innerHeight,
      ),
    ).toBe(true);
    await page.screenshot({ path: `${folder}/home-${width}x${height}.png` });
  }
  await page.setViewportSize({ width: 1366, height: 768 });
  await page
    .getByRole('button', { name: 'Использовать пример: Подключение к электросети', exact: true })
    .hover();
  await page.screenshot({ path: folder + '/example-hover.png' });
  await page.locator('#description').fill('Мало');
  await expect(create).toBeDisabled();
  await page
    .getByRole('button', { name: 'Использовать пример: Подключение к электросети', exact: true })
    .click();
  await expect(page.locator('#description')).toHaveValue(/Клиент отправляет заявку на подключение/);
  await expect(create).toBeEnabled();
  expect(generations).toBe(0);
  await page.locator('#description').focus();
  await create.evaluate((element) =>
    Promise.all(element.getAnimations().map((animation) => animation.finished)),
  );
  await page.screenshot({ path: folder + '/filled-focus-1366.png' });
  await page.locator('#description').press('Control+Enter');
  await expect(page.locator('.bpmn-canvas')).toBeVisible();
  expect(generations).toBe(1);
  await page.screenshot({ path: folder + '/workspace-1366.png' });
  // Click the wordmark, not the SVG: the whole identity is one home link.
  await page.locator('.brand .wordmark').click();
  await expect(page.locator('#description')).toBeFocused();
  await expect(page.locator('#description')).toHaveValue('');
  await expect(page.locator('.bpmn-canvas')).toHaveCount(0);
  await expect(create).toBeDisabled();
  expect(generations).toBe(1);
  await page.getByRole('link', { name: 'PULSE', exact: true }).focus();
  await page.screenshot({ path: folder + '/logo-keyboard-focus.png' });
  await page.keyboard.press('Enter');
  await expect(page.locator('#description')).toBeFocused();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page
    .getByRole('button', { name: 'Использовать пример: Подключение к электросети', exact: true })
    .hover();
  await expect(page.locator('.home-examples button').last().locator('svg')).toHaveCSS(
    'transform',
    'none',
  );
  await expect(page.getByRole('link', { name: 'PULSE', exact: true })).toHaveCSS(
    'transition-duration',
    '0s',
  );
  expect(consoleProblems).toEqual([]);
});
