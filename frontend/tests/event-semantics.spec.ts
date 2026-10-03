import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
const folder=path.resolve('../examples/event-semantics');
for (const name of ['reference','catch-events']) {
  test(`event semantics: ${name} render, export, Modify preview and exact Undo`, async ({page,request}) => {
    const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/api/**',async route=> {
      const url=route.request().url().replace('http://127.0.0.1:5173','http://127.0.0.1:8001');
      await route.fulfill({response:await route.fetch({url})});
    });
    await page.goto('/');
    await page.locator('input[type=file]').setInputFiles(path.join(folder,`${name}.bpmn`));
    await expect(page.locator('#command')).toBeEnabled();
    await expect(page.locator('.djs-shape[data-element-id="Reply"]')).toHaveCount(1);
    const source=fs.readFileSync(path.join(folder,`${name}.bpmn`),'utf8');
    const response=await request.post('http://127.0.0.1:8001/api/process/import',{data:{xml:source}});
    expect(response.status()).toBe(200);
    const before=(await response.json()).process;
    expect(before.nodes.find((n:{id:string})=>n.id==='Reply').type).toBe('event_based_gateway');
    expect(before.nodes.find((n:{id:string})=>n.id==='CompanyStart').event_definition).toBe('message');
    async function exported() {
      const wait=page.waitForEvent('download');
      await page.getByRole('button',{name:'Экспорт BPMN',exact:true}).click();
      return fs.readFileSync((await (await wait).path())!,'utf8');
    }
    const live=await exported();expect(live).toContain('eventBasedGateway');expect(live).toContain('messageEventDefinition');
    const restored=await request.post('http://127.0.0.1:8001/api/process/import',{data:{xml:live,previous:before}});
    expect(restored.status()).toBe(200);expect((await restored.json()).process).toEqual(before);
    const candidate=structuredClone(before);candidate.nodes.find((n:{id:string})=>n.id==='Check').name='Проверить комплект документов';
    const built=await request.post('http://127.0.0.1:8001/api/process/bpmn',{data:{process:candidate}});
    expect(built.status()).toBe(200);
    await page.route('**/api/process/modify',async route=> {
      await route.fulfill({json:{process:candidate,xml:(await built.json()).xml,ambiguities:[],changes:[{type:'node_changed',element_ids:['Check'],description:'Уточнена подпись проверки',category:'structure',action:'changed'}],metadata:{provider_used:'mock',model_used:'fixtures',fallback_used:false}}});
    });
    await page.locator('#command').fill('Уточни название проверки');
    await page.getByRole('button',{name:'Подготовить изменения',exact:true}).click();
    await expect(page.getByLabel('Предпросмотр изменений')).toBeVisible();
    await page.getByRole('button',{name:'Применить изменения',exact:true}).click();
    await expect(page.locator('#command')).toBeEnabled();
    expect(await exported()).toContain('Проверить комплект документов');
    await page.getByRole('button',{name:'Отменить AI-изменение',exact:true}).click();
    await expect(page.locator('#command')).toBeEnabled();expect(await exported()).toBe(live);
    await page.getByRole('button',{name:'По размеру',exact:true}).click();
    await page.screenshot({path:path.join(folder,`${name}-1366.png`)});
    expect(errors).toEqual([]);
  });
}
