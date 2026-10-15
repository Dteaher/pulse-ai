import {test,expect} from './fixtures';
import fs from 'node:fs';
import path from 'node:path';
for(const mode of ['plain','formal']) {
 test(`Doctor diagnostics and BPMN import: ${mode}`,async({page})=>{
  const root=path.resolve('../.runtime/doctor-stage-a',mode);
  test.skip(!fs.existsSync(root),'Requires locally generated offline artifacts');
  const data=JSON.parse(fs.readFileSync(path.join(root,'response.json'),'utf8'));
  const audit=JSON.parse(fs.readFileSync(path.join(root,'audit.json'),'utf8'));
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/process/generate',route=>route.fulfill({json:data}));
  await page.route('**/api/process/audit',route=>route.fulfill({json:audit}));
  await page.goto('/');await page.locator('textarea').first().fill('Проверка эталонной схемы без платных запросов.');
  await page.getByRole('button',{name:'Создать BPMN',exact:true}).click();
  for(const node of data.process.nodes) await expect(page.locator(`.djs-shape[data-element-id="${node.id}"]`)).toHaveCount(1);
  await page.getByRole('button',{name:'Проверить',exact:true}).click();
  await expect(page.locator('.checks .failed')).toHaveCount(0);
  await expect(page.locator('.audit')).toContainText('Программные правила');
  await expect(page.locator('.audit')).toContainText('SCC');
  await page.screenshot({path:test.info().outputPath('doctor.png')});
  expect(errors).toEqual([]);
 });
}
