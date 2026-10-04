import { test, expect } from './fixtures';
import fs from 'node:fs';
import path from 'node:path';
import { applyProcess, initialProcessState, undoProcess } from '../src/services/processState';
import type { Process } from '../src/types';

const folder = path.resolve('../examples/pipeline-resilience');
const load = (name: string) =>
  JSON.parse(fs.readFileSync(path.join(folder, name + '-response.json'), 'utf8'));

test('graph-free Clarify: text → questions → answers → real bpmn-js import', async ({ page }) => {
  const first = load('ambiguous');
  const final = load('simple');
  await page.route('**/api/health', (route) =>
    route.fulfill({ json: { configured: true, provider: 'multiai' } }),
  );
  await page.route('**/api/process/generate', (route) => route.fulfill({ json: first }));
  await page.route('**/api/process/clarify', (route) => {
    const body = route.request().postDataJSON();
    expect(body.process).toBeNull();
    expect(body.preflight.original_text).toBe(first.preflight.original_text);
    expect(Object.keys(body.answers).length).toBe(first.ambiguities.length);
    return route.fulfill({ json: final });
  });
  await page.goto('/');
  await page.locator('textarea').first().fill(first.preflight.original_text);
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.getByText('УТОЧНЕНИЕ ПРОЦЕССА', { exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(folder, 'clarify-ui.png') });
  for (const q of first.ambiguities) {
    await page
      .getByLabel('Свой ответ: ' + q.question)
      .fill('Оператор проверяет повторно. Проверки выполняются параллельно.');
  }
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click();
  await expect(page.locator('.djs-element[data-element-id]').first()).toBeVisible();
  await expect(page.getByText('УТОЧНЕНИЕ ПРОЦЕССА', { exact: true })).not.toBeVisible();
});

for (const name of [
  'simple',
  'medium',
  'complex',
  'vertex',
  'exact-complex',
  'exact-reference',
  'clarified',
  'exact-complex-polished',
]) {
  test(`real ${name}: actual provider XML imports into bpmn-js`, async ({ page }) => {
    await page.goto('/');
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: 'Открыть .bpmn', exact: true }).click();
    await (await chooser).setFiles(path.join(folder, name + '.bpmn'));
    await expect(page.locator('.djs-element[data-element-id]').first()).toBeVisible();
    await expect(page.locator('.error-box')).toHaveCount(0);
    if (name === 'exact-complex-polished')
      await page.screenshot({ path: path.join(folder, 'exact-complex-browser.png') });
  });
}

test('exact Undo preserves assumptions, ProcessDefinition and live XML', () => {
  const p = load('simple').process as Process;
  p.assumptions = [
    { id: 'a', text: 'Регистрация вручную', source: 'model_inference', confidence: 0.8 },
  ];
  const first = applyProcess(initialProcessState, { ...load('simple'), process: p });
  const before = {
    xml: first.xml + '\n',
    process: p,
    label: 'v1',
    timestamp: '2026-10-03',
    reason: 'modify',
    version: 1,
  };
  const second = applyProcess(
    first,
    { ...load('simple'), process: { ...p, assumptions: [] } },
    before,
  );
  const restored = undoProcess(second);
  expect(restored.xml).toBe(before.xml);
  expect(restored.process).toEqual(p);
  expect(restored.process?.assumptions).toEqual(p.assumptions);
});

test('TO-BE Clarify keeps AS-IS intact and returns an unapplied proposal', async ({ page }) => {
  const first = load('ambiguous');
  const base = load('simple');
  await page.route('**/api/health', (route) =>
    route.fulfill({ json: { configured: true, provider: 'multiai' } }),
  );
  await page.route('**/api/process/generate', (route) => route.fulfill({ json: base }));
  await page.route('**/api/process/modify', (route) => route.fulfill({ json: first }));
  await page.route('**/api/process/clarify', (route) => {
    const body = route.request().postDataJSON();
    expect(body.operation).toBe('modify');
    expect(body.base_process.id).toBe(base.process.id);
    expect(body.instruction).toContain('TO-BE');
    return route.fulfill({ json: load('medium') });
  });
  await page.goto('/');
  await page.locator('textarea').first().fill('Оператор получает заявку и регистрирует её.');
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.locator('.djs-element[data-element-id]').first()).toBeVisible();
  const exportXml = async () => {
    const waiting = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Экспорт BPMN', exact: true }).click();
    return fs.readFileSync((await (await waiting).path())!, 'utf8');
  };
  const before = await exportXml();
  await page.getByRole('button', { name: 'Улучшить', exact: true }).click();
  await expect(page.getByText('УТОЧНЕНИЕ ПРОЦЕССА', { exact: true })).toBeVisible();
  for (const q of first.ambiguities)
    await page.getByLabel('Свой ответ: ' + q.question).fill('Оператор, проверки параллельны.');
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Предложение улучшения', exact: true }),
  ).toBeVisible();
  expect(await exportXml()).toBe(before);
  await expect(page.getByLabel('Предпросмотр изменений')).not.toBeVisible();
});

test('assumptions remain visible and survive a bpmn-js export/import roundtrip', async ({
  page,
  request,
}) => {
  await page.route('**/api/**', async (route) =>
    route.fulfill({
      response: await route.fetch({
        url: route.request().url().replace('http://127.0.0.1:5173', 'http://127.0.0.1:8001'),
      }),
    }),
  );
  await page.goto('/');
  const waiting = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Открыть .bpmn', exact: true }).click();
  await (await waiting).setFiles(path.join(folder, 'assumptions-demo.bpmn'));
  await expect(page.getByText('Приняты допущения: 1', { exact: true })).toBeVisible();
  await page.getByText('Приняты допущения: 1', { exact: true }).click();
  await expect(
    page.getByText('Регистрация выполняется вручную (демонстрационное допущение).', {
      exact: true,
    }),
  ).toBeVisible();
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Экспорт BPMN', exact: true }).click();
  const xml = fs.readFileSync((await (await downloading).path())!, 'utf8');
  const result = await request.post('http://127.0.0.1:8001/api/process/import', { data: { xml } });
  expect(result.status()).toBe(200);
  expect((await result.json()).process.assumptions).toEqual([
    {
      id: 'demo_assumption',
      text: 'Регистрация выполняется вручную (демонстрационное допущение).',
      source: 'model_inference',
      confidence: 0.8,
    },
  ]);
});
