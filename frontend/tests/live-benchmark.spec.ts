import {test,expect} from './fixtures';
import fs from 'node:fs';
import path from 'node:path';
const root=process.env.PULSE_LIVE_ARTIFACTS;
const runs=['1-simple-multiai-gpt-6-sol','3-simple-multiai-gpt-6-astra','4-complex-multiai-gpt-6-astra','6-medium-multiai-gpt-6-astra-fallback-timeout'];
for(const run of runs) {
 test(`saved live BPMN renders all nodes: ${run}`,async({page})=>{
  test.skip(!root,'Requires explicitly saved live benchmark artifacts; no paid API calls');
  const data=JSON.parse(fs.readFileSync(path.join(root!,run,'response.json'),'utf8'));
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/process/generate',route=>route.fulfill({json:data}));
  await page.goto('/');await page.locator('textarea').first().fill('Тест уже полученного результата без обращения к API.');
  await page.getByRole('button',{name:'Создать BPMN',exact:true}).click();
  for(const node of data.process.nodes) await expect(page.locator(`.djs-shape[data-element-id="${node.id}"]`)).toHaveCount(1);
  await expect(page.locator('.provider-info')).toContainText(run.includes('sol')?'GPT-6 Sol':'GPT-6 Astra');
  if(run.includes('fallback')) await expect(page.locator('.provider-info')).toContainText('(резервная модель)');
  await page.screenshot({path:test.info().outputPath('live.png')});
  expect(errors).toEqual([]);
 });
}
