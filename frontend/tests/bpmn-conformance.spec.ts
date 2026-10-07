import { test, expect } from './fixtures';
import fs from 'node:fs';
import path from 'node:path';
const folder = path.resolve('../examples/bpmn-conformance');

for (const name of [
  'basic',
  'implicit',
  'none-intermediate',
  'collaboration-loops',
  'event-wait',
  'real-result',
]) {
  test(`BPMN subset: ${name} imports, renders and preserves semantics after export`, async ({
    page,
    request,
  }, testInfo) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route('**/api/**', async (route) => {
      await route.fulfill({
        response: await route.fetch({
          url: route.request().url().replace('http://127.0.0.1:5181', 'http://127.0.0.1:8003'),
        }),
      });
    });
    await page.goto('/');
    const started = Date.now();
    await page.locator('input[type=file]').setInputFiles(path.join(folder, `${name}.bpmn`));
    await expect(page.locator('#command')).toBeEnabled();
    const p = JSON.parse(
      fs.readFileSync(
        path.join(folder, name === 'real-result' ? 'real-process.json' : `${name}.json`),
        'utf8',
      ),
    );
    for (const n of p.nodes)
      await expect(page.locator(`.djs-shape[data-element-id="${n.id}"]`)).toHaveCount(1);
    const importedMs = Date.now() - started;
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Экспорт BPMN', exact: true }).click();
    const xml = fs.readFileSync((await (await download).path())!, 'utf8');
    const response = await request.post('http://127.0.0.1:8003/api/process/import', {
      data: { xml, previous: p },
    });
    expect(response.status()).toBe(200);
    // New optional field has a default; retain every existing semantic comparison.
    expect((await response.json()).process).toEqual({ ...p, assumptions: p.assumptions ?? [] });
    if (name === 'none-intermediate') expect(xml).toContain('intermediateThrowEvent');
    if (name === 'event-wait') expect(xml).toContain('eventBasedGateway');
    if (name === 'collaboration-loops') {
      // Moddle omits xsi:type for the property's default tExpression type.
      expect(xml).toContain('conditionExpression');
      expect(xml).toContain('Стоимость &lt;= 500000');
      expect(xml).not.toContain('tFormalExpression');
    }
    await page.getByRole('button', { name: 'По размеру', exact: true }).click();
    await page.screenshot({ path: path.join(folder, `${name}-browser.png`) });
    await testInfo.attach('import-timing', {
      body: JSON.stringify({ import_ms: importedMs }),
      contentType: 'application/json',
    });
    expect(errors).toEqual([]);
  });
}

test('Doctor distinguishes normative errors, business warnings and clarification', async ({
  page,
}) => {
  await page.route('**/api/**', async (route) => {
    await route.fulfill({
      response: await route.fetch({
        url: route.request().url().replace('http://127.0.0.1:5181', 'http://127.0.0.1:8003'),
      }),
    });
  });
  await page.route('**/api/process/audit', async (route) => {
    await route.fulfill({
      json: {
        technical: {
          'BPMN XML / XSD': true,
          'Граф / связи / события / шлюзы': false,
          Участники: true,
        },
        llm_audit: false,
        notice: '',
        issues: [
          {
            severity: 'error',
            source: 'BPMN_SPEC',
            spec_section: '§10.5.2',
            origin: 'rule',
            node_ids: ['N0'],
            message: 'Start Event не допускает входящий Sequence Flow.',
          },
          {
            severity: 'warning',
            source: 'BUSINESS_LOGIC',
            origin: 'rule',
            node_ids: ['N1'],
            message: 'Проверьте последствия отказа.',
          },
          {
            severity: 'clarification',
            source: 'BUSINESS_LOGIC',
            origin: 'rule',
            node_ids: [],
            message: 'Нужна ли обработка исключений?',
          },
        ],
      },
    });
  });
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles(path.join(folder, 'basic.bpmn'));
  await expect(page.locator('#command')).toBeEnabled();
  await page.getByRole('button', { name: 'Проверить', exact: true }).click();
  await expect(page.locator('.issue.error .finding-origin')).toHaveText('BPMN');
  await expect(page.locator('.issue.warning .finding-origin')).toHaveText('Бизнес');
  await expect(page.locator('.issue.clarification')).toContainText('Уточнение');
  await expect(page.locator('.issue.error .finding-origin')).toHaveAttribute('title', '§10.5.2');
  await page.screenshot({ path: path.join(folder, 'doctor-categories.png') });
});
