import {test,expect} from './fixtures';
import fs from 'node:fs';
import path from 'node:path';
const root=process.env.PULSE_FALLBACK_ARTIFACTS;
for(const run of root?fs.readdirSync(root).filter(r=>fs.existsSync(path.join(root,r,'result.bpmn'))):[]) {
 test(`fallback BPMN imports: ${run}`,async({page})=>{
  const data=JSON.parse(fs.readFileSync(path.join(root!,run,'response.json'),'utf8'));
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/process/generate',route=>route.fulfill({json:data}));
  await page.goto('/');await page.locator('textarea').first().fill('Проверка сохранённой схемы без API.');
  await page.getByRole('button',{name:'Создать BPMN',exact:true}).click();
  for(const node of data.process.nodes) await expect(page.locator(`.djs-shape[data-element-id="${node.id}"]`)).toHaveCount(1);
  if(data.metadata.fallback_used) await expect(page.locator('.provider-info')).toContainText('(резервная модель)');
  else await expect(page.locator('.provider-info')).not.toContainText('(резервная модель)');
  await page.screenshot({path:test.info().outputPath('fallback.png')});
  expect(errors).toEqual([]);
 });
}
