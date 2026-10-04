import { test, expect } from './fixtures';
import fs from 'node:fs';
import path from 'node:path';

for (const fallback of [false, true]) {
  test(`статус LLM: fallback=${fallback}`, async ({ page }) => {
    await page.route('**/api/process/generate', async (route) => {
      const result = {
        process: JSON.parse(
          fs.readFileSync(path.resolve('../examples/02-grid-connection.json'), 'utf8'),
        ),
        xml: fs.readFileSync(path.resolve('../examples/02-grid-connection.bpmn'), 'utf8'),
        ambiguities: [],
        attempts: 1,
      };
      await route.fulfill({
        json: {
          ...result,
          metadata: {
            provider_used: fallback ? 'vertex_gemini' : 'multiai',
            model_used: fallback ? 'gemini-2.5-flash' : 'gpt-6.1-sol',
            fallback_used: fallback,
            attempts: fallback ? 2 : 1,
          },
        },
      });
    });
    await page.goto('/');
    await page.getByRole('button', { name: 'Загрузить пример', exact: true }).click();
    await page.getByRole('button', { name: 'Подключение к электросети', exact: true }).click();
    await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
    await expect(page.locator('.provider-info')).toContainText(
      fallback ? 'gemini-2.5-flash · резервная модель' : 'Модель: gpt-6.1-sol',
    );
    await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);
    if (fallback) await page.screenshot({ path: '../examples/failover-1366.png' });
  });
}

test('недоступность моделей: понятная ошибка без stack trace', async ({ page }) => {
  await page.route('**/api/process/generate', (route) =>
    route.fulfill({
      status: 502,
      json: {
        detail:
          'Не удалось получить ответ от основной и резервной моделей. Повторите запрос позже или проверьте настройки подключения.',
      },
    }),
  );
  await page.goto('/');
  await page.getByRole('button', { name: 'Загрузить пример', exact: true }).click();
  await page.getByRole('button', { name: 'Подключение к электросети', exact: true }).click();
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('основной и резервной моделей');
  await expect(page.getByRole('button', { name: 'Создать BPMN', exact: true })).toBeEnabled();
});
