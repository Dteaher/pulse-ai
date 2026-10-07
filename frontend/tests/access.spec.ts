import { test, expect } from './fixtures';

const secret = 'browser-test-workspace-access';
test('shared workspace access: invalid code, successful check, memory only, reload asks again', async ({
  page,
}) => {
  let checks = 0;
  await page.route('**/api/health', (route) =>
    route.fulfill({ json: { provider: 'mock', configured: true, access_required: true } }),
  );
  await page.route('**/api/access/check', (route) => {
    checks++;
    return route.fulfill({
      status: route.request().headers().authorization === `Bearer ${secret}` ? 200 : 401,
      json: { status: 'ok', code: 'ACCESS_REQUIRED' },
    });
  });
  await page.goto('/');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(page.getByLabel('Код доступа')).toBeFocused();
  await page.getByLabel('Код доступа').fill('incorrect');
  await page.getByRole('button', { name: 'Открыть PULSE' }).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось войти');
  await page.getByLabel('Код доступа').fill(secret);
  await page.getByRole('button', { name: 'Открыть PULSE' }).click();
  await expect(dialog).not.toBeVisible();
  expect(checks).toBe(2);
  expect(
    await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage })),
  ).not.toContain(secret);
  await page.reload();
  await expect(dialog).toBeVisible();
});

test('public workspace needs no gate and truthfully labels fixture mode', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.getByText('Демо на примерах · без обращения к AI-модели')).toBeVisible();
});

test('non-JSON upstream failure gives a recovery message instead of a parse error', async ({
  page,
}) => {
  await page.route('**/api/process/generate', (route) =>
    route.fulfill({ status: 502, contentType: 'text/html', body: '<html>proxy error</html>' }),
  );
  await page.goto('/');
  await page
    .getByRole('button', { name: 'Использовать пример: Обработка заявки', exact: true })
    .click();
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Сервер вернул неожиданный ответ');
  await expect(page.getByRole('button', { name: 'Создать BPMN', exact: true })).toBeEnabled();
});

test('home remains usable at phone width with no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByLabel('Описание процесса', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('mobile-home.png'), fullPage: true });
});


test('Copilot suggestion matches an existing document-review task', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Использовать пример: Обработка заявки', exact: true }).click();
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.locator('.djs-element[data-element-id]').first()).toBeVisible();
  await expect(page.getByRole('button', { name: '+ Согласование после проверки документов' })).not.toBeVisible();
});
