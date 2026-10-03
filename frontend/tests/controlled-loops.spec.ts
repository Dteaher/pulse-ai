import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
const folder = path.resolve('../examples/controlled-loops');
for (const name of ['reference', 'vertex', 'failover', 'groq']) {
  test(`controlled loops: ${name} imports, renders and exports through bpmn-js`, async ({ page, request }) => {
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/api/**', async route => {
      const url = route.request().url().replace('http://127.0.0.1:5173','http://127.0.0.1:8001');
      await route.fulfill({ response: await route.fetch({ url }) });
    });
    const xml = fs.readFileSync(path.join(folder, `${name}.bpmn`),'utf8');
    await page.goto('/');
    await page.locator('input[type=file]').setInputFiles(path.join(folder, `${name}.bpmn`));
    await expect(page.locator('#command')).toBeEnabled();
    const imported = await request.post('http://127.0.0.1:8001/api/process/import', { data: { xml } });
    expect(imported.status()).toBe(200);
    const p = (await imported.json()).process;
    expect(p.pools).toHaveLength(2);
    expect(p.message_flows.length).toBeGreaterThanOrEqual(4);
    for (const pool of p.pools) await expect(page.locator(`.djs-shape[data-element-id="${pool.id}"]`)).toHaveCount(1);
    for (const f of p.message_flows) await expect(page.locator(`.djs-connection[data-element-id="${f.id}"]`)).toHaveCount(1);
    expect(p.nodes.filter((n: {type:string}) => n.type==='parallel_gateway')).toHaveLength(2);
    const download = page.waitForEvent('download');
    await page.getByRole('button',{name:'Экспорт BPMN',exact:true}).click();
    const exported = fs.readFileSync((await (await download).path())!, 'utf8');
    const restored = await request.post('http://127.0.0.1:8001/api/process/import', { data: { xml:exported, previous:p } });
    expect(restored.status()).toBe(200);
    expect((await restored.json()).process).toEqual(p);
    expect(errors).toEqual([]);
    await page.getByRole('button',{name:'По размеру',exact:true}).click();
    await page.screenshot({ path:path.join(folder,`${name}-1366.png`) });
  });
}
