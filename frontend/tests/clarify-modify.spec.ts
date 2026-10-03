import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { applyProcess, initialProcessState, undoProcess } from '../src/services/processState';
import type { Process, Result, Snapshot } from '../src/types';

const fixture = (name: string) =>
  JSON.parse(fs.readFileSync(path.resolve('../examples/' + name + '.json'), 'utf8')) as Process;
const xml = (name: string) =>
  fs.readFileSync(path.resolve('../examples/' + name + '.bpmn'), 'utf8');
const base: Result = {
  process: fixture('02-grid-connection'),
  xml: xml('02-grid-connection'),
  ambiguities: [],
  attempts: 1,
};
const updated: Result = {
  process: fixture('01-application'),
  xml: xml('01-application'),
  ambiguities: [],
  attempts: 1,
  changes: [
    {
      type: 'node_added',
      element_ids: ['Check'],
      description: 'Добавлено согласование руководителем.',
    },
  ],
};

async function exportXml(page: Page) {
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Экспорт BPMN', exact: true }).click();
  return fs.readFileSync((await (await download).path())!, 'utf8');
}

async function confirmChange(page: Page) {
  await expect(page.getByLabel('Предпросмотр изменений')).toBeVisible();
  await page.getByRole('button', { name: 'Применить изменения', exact: true }).click();
}

async function setup(page: Page, draft = false) {
  await page.route('**/api/health', (route) =>
    route.fulfill({ json: { configured: true, provider: 'mock' } }),
  );
  await page.route('**/api/examples', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/process/generate', (route) =>
    route.fulfill({
      json: draft
        ? {
            ...base,
            process: fixture('03-ambiguous'),
            ambiguities: fixture('03-ambiguous').ambiguities,
            xml: null,
          }
        : base,
    }),
  );
  // The importer is real and deterministic; this route makes unit UI tests independent of servers/providers.
  await page.route('**/api/process/import', (route) =>
    route.fulfill({ json: { process: base.process } }),
  );
  await page.goto('/');
  await page.locator('textarea').first().fill('Описание процесса с несколькими проверками');
  await page.getByRole('button', { name: 'Создать BPMN', exact: true }).click();
}

test('state: snapshot stores JSON and exact live XML, undo pops one state', () => {
  const before: Snapshot = {
    process: structuredClone(base.process),
    xml: '<live manual layout/>',
    label: 'До AI',
    timestamp: new Date().toISOString(),
    reason: 'before_ai_modify',
  };
  const state = applyProcess(initialProcessState, updated, before);
  before.process!.name = 'Changed outside history';
  expect(state.versions[0].process!.name).toBe(base.process.name);
  const restored = undoProcess(state);
  expect(restored.process).toEqual(base.process);
  expect(restored.xml).toBe('<live manual layout/>');
  expect(restored.versions).toHaveLength(0);
  expect(applyProcess(restored, { ...updated, xml: null })).toBe(restored);
});

test('state: v1 → v2 → v3 history keeps descriptions and accepted assumptions', () => {
  const assumption = {
    id: 'w',
    question: 'Канал уведомления?',
    severity: 'warning',
    suggested_answers: [],
    related_node_ids: [],
    assumption: 'Личный кабинет',
  };
  const v1 = applyProcess(
    initialProcessState,
    { ...base, accepted_assumptions: [assumption] },
    undefined,
    'Создан по описанию',
  );
  const v2 = applyProcess(
    v1,
    updated,
    {
      process: v1.process,
      xml: v1.xml,
      label: v1.description,
      timestamp: '2026-10-03',
      reason: 'before_ai_modify',
    },
    'Добавлено согласование',
  );
  const v3 = applyProcess(
    v2,
    updated,
    {
      process: v2.process,
      xml: '<manually moved v2/>',
      label: v2.description,
      timestamp: '2026-10-03',
      reason: 'before_ai_modify',
    },
    'Изменено условие',
  );
  expect(v3.version).toBe(3);
  expect(v3.versions.map((s) => s.version)).toEqual([1, 2]);
  const restored = undoProcess(v3);
  expect(restored.version).toBe(2);
  expect(restored.description).toBe('Добавлено согласование');
  expect(restored.xml).toBe('<manually moved v2/>');
  expect(restored.assumptions).toEqual([assumption]);
  expect(undoProcess(restored).version).toBe(1);
});

test('optional Clarify: accept two assumptions, count and retain them through Undo', async ({
  page,
}) => {
  const questions = [0, 1].map((i) => ({
    id: 'w' + i,
    question: 'Как уведомлять клиента ' + i + '?',
    severity: 'warning',
    suggested_answers: ['Личный кабинет'],
    related_node_ids: [],
    assumption: 'Уведомление через личный кабинет',
  }));
  await setup(page);
  await page.route('**/api/process/generate', (r) =>
    r.fulfill({
      json: {
        ...base,
        process: { ...base.process, ambiguities: questions },
        ambiguities: questions,
      },
    }),
  );
  await page.route('**/api/process/clarify', async (route) => {
    const body = route.request().postDataJSON();
    expect(body.accepted_ambiguity_ids).toEqual(['w0', 'w1']);
    expect(body.answers).toEqual({});
    await route.fulfill({ json: { ...base, accepted_assumptions: questions } });
  });
  await page.getByRole('button', { name: 'Создать', exact: true }).click();
  await page
    .getByRole('dialog')
    .locator('textarea')
    .fill('Новое описание процесса с уведомлениями');
  await page.getByRole('dialog').getByRole('button', { name: 'Создать BPMN', exact: true }).click();
  await expect(page.locator('.clarify')).toContainText('Желательно уточнить');
  const checks = page.getByRole('checkbox', { name: 'Принять как допущение' });
  await checks.nth(0).check();
  await checks.nth(1).check();
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click();
  await expect(page.locator('.assumptions-summary')).toContainText('Приняты допущения: 2');
  await page.route('**/api/process/modify', (route) => route.fulfill({ json: updated }));
  await page.locator('#command').fill('Добавь согласование');
  await page.getByRole('button', { name: 'Подготовить изменения', exact: true }).click();
  await confirmChange(page);
  await page.getByRole('button', { name: 'Отменить AI-изменение', exact: true }).click();
  await expect(page.locator('.assumptions-summary')).toContainText('Приняты допущения: 2');
});

test('Modify preview can be cancelled without changing XML or history', async ({ page }) => {
  await page.route('**/api/process/modify', (r) => r.fulfill({ json: updated }));
  await setup(page);
  await expect(page.locator('.djs-shape').first()).toBeVisible();
  const before = await exportXml(page);
  await page
    .locator('#command')
    .fill('Добавь согласование. ' + 'Сохрани остальные действия и условия. '.repeat(15));
  await page.getByRole('button', { name: 'Подготовить изменения', exact: true }).click();
  await expect(page.getByLabel('Предпросмотр изменений')).toContainText('Добавится');
  await expect(
    page.getByRole('button', { name: 'Применить изменения', exact: true }),
  ).toBeInViewport();
  await expect(page.getByText('Показать команду полностью', { exact: true })).toBeVisible();
  expect(await exportXml(page)).toBe(before);
  await page
    .getByLabel('Предпросмотр изменений')
    .getByRole('button', { name: 'Отменить', exact: true })
    .click();
  expect(await exportXml(page)).toBe(before);
  await page.getByRole('tab', { name: 'История', exact: true }).click();
  await expect(page.locator('.version')).toHaveCount(0);
});

test('stale preview cannot replace manual changes made while reviewing', async ({ page }) => {
  await page.route('**/api/process/modify', (r) => r.fulfill({ json: updated }));
  await setup(page);
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
  await page.locator('#command').fill('Добавь согласование');
  await page.getByRole('button', { name: 'Подготовить изменения', exact: true }).click();
  await expect(page.getByLabel('Предпросмотр изменений')).toBeVisible();
  const bounds = (await page.locator('.djs-shape[data-element-id="Check"]').boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2 + 35, {
    steps: 12,
  });
  await page.mouse.up();
  const moved = await exportXml(page);
  await page.getByRole('button', { name: 'Применить изменения', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText(
    'Диаграмма изменилась после подготовки превью',
  );
  expect(await exportXml(page)).toBe(moved);
  await expect(page.getByLabel('Предпросмотр изменений')).toHaveCount(0);
});

test('Clarify: questions → followup → answers → BPMN', async ({ page }) => {
  let count = 0;
  await page.route('**/api/process/clarify', async (route) => {
    const request = route.request().postDataJSON();
    expect(request.original_text).toContain('Описание процесса');
    count++;
    await route.fulfill({
      json:
        count === 1
          ? {
              ...base,
              process: {
                ...base.process,
                ambiguities: [
                  {
                    id: 'followup',
                    question: 'После какой проверки?',
                    suggested_answers: ['После юридической'],
                    related_node_ids: [],
                    severity: 'critical',
                  },
                ],
              },
              ambiguities: [],
              xml: null,
              clarification_round: 1,
            }
          : { ...base, clarification_round: 2 },
    });
  });
  await setup(page, true);
  for (const field of await page.locator('.clarify fieldset').all())
    await field.locator('input').fill('Параллельно, при отказе уведомить клиента');
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click();
  await expect(page.locator('.clarify')).toContainText('После какой проверки?');
  await page.getByRole('button', { name: 'После юридической', exact: true }).click();
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click();
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
  expect(count).toBe(2);
});

test('Clarify: critical questions still block at round limit', async ({ page }) => {
  await page.route('**/api/process/clarify', async (route) => {
    const data = route.request().postDataJSON();
    await route.fulfill({
      json: data.continue_with_draft
        ? base
        : {
            ...base,
            process: fixture('03-ambiguous'),
            xml: null,
            clarification_round: 3,
            clarification_limit_reached: true,
          },
    });
  });
  await setup(page, true);
  for (const field of await page.locator('.clarify fieldset').all())
    await field.locator('input').fill('Ответ');
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click();
  await expect(page.locator('.clarify')).toContainText('Лимит уточнений достигнут');
  await expect(page.getByRole('button', { name: 'Продолжить', exact: true })).toBeDisabled();
  await expect(
    page.getByRole('button', { name: 'Продолжить с допущениями черновика', exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Изменить описание', exact: true })).toBeEnabled();
});

test('Modify + ChangeSet + Undo restores exact manually moved layout', async ({ page }) => {
  await page.route('**/api/process/modify', async (route) => {
    expect(route.request().postDataJSON().instruction).toBe('Добавь согласование');
    await route.fulfill({ json: updated });
  });
  await setup(page);
  const shape = page.locator('.djs-shape[data-element-id="Check"]');
  await expect(shape).toBeVisible();
  const bounds = (await shape.boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2 + 35, {
    steps: 12,
  });
  await page.mouse.up();
  const moved = (await shape.boundingBox())!;
  expect(Math.abs(moved.y - bounds.y)).toBeGreaterThan(10);
  const before = await exportXml(page);
  await page.locator('#command').fill('Добавь согласование');
  await page.getByRole('button', { name: 'Подготовить изменения', exact: true }).click();
  await expect(page.getByLabel('Предпросмотр изменений')).toBeVisible();
  expect(await exportXml(page)).toBe(before);
  await confirmChange(page);
  await expect(page.getByLabel('Изменения процесса')).toContainText(
    'Добавлено согласование руководителем',
  );
  await page.getByRole('button', { name: 'Отменить AI-изменение', exact: true }).click();
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
  expect(await exportXml(page)).toBe(before);
});

test('invalid Modify preserves live diagram and shows safe error', async ({ page }) => {
  await page.route('**/api/process/modify', (route) =>
    route.fulfill({ status: 502, json: { detail: 'Не удалось получить корректный процесс.' } }),
  );
  await setup(page);
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
  const before = await exportXml(page);
  await page.locator('#command').fill('Добавь согласование');
  await page.getByRole('button', { name: 'Подготовить изменения', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Текущая версия процесса сохранена');
  expect(await exportXml(page)).toBe(before);
});

test('ambiguous Modify keeps v1 and passes operation context into Clarify', async ({ page }) => {
  const draft = {
    ...base.process,
    ambiguities: [
      {
        id: 'which',
        question: 'После какого согласования?',
        suggested_answers: ['После руководителя'],
        related_node_ids: [],
        severity: 'critical',
      },
    ],
  };
  await page.route('**/api/process/modify', (route) =>
    route.fulfill({ json: { ...base, process: draft, xml: null, changes: [] } }),
  );
  await page.route('**/api/process/clarify', async (route) => {
    const body = route.request().postDataJSON();
    expect(body.operation).toBe('modify');
    expect(body.instruction).toBe('Добавь проверку после согласования');
    expect(body.base_process).toEqual(base.process);
    await route.fulfill({ json: updated });
  });
  await setup(page);
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
  await page.locator('#command').fill('Добавь проверку после согласования');
  await page.getByRole('button', { name: 'Подготовить изменения', exact: true }).click();
  await expect(page.locator('.clarify')).toContainText('После какого согласования?');
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
  await page.getByRole('button', { name: 'После руководителя', exact: true }).click();
  await page.getByRole('button', { name: 'Продолжить', exact: true }).click();
  await confirmChange(page);
  await expect(page.getByLabel('Изменения процесса')).toBeVisible();
  await expect(page.getByLabel('Изменения процесса').locator('h2')).toBeInViewport();
  await page.getByRole('button', { name: 'Отменить AI-изменение', exact: true }).click();
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
});
