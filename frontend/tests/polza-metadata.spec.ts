import {test,expect} from './fixtures';
import fs from 'node:fs';
import path from 'node:path';
for(const fallback of [false,true]) {
 test(`Polza metadata model and provider, fallback=${fallback}`,async({page})=>{
  await page.route('**/api/process/generate',route=>route.fulfill({json:{
   process:JSON.parse(fs.readFileSync(path.resolve('../examples/02-grid-connection.json'),'utf8')),
   xml:fs.readFileSync(path.resolve('../examples/02-grid-connection.bpmn'),'utf8'),
   ambiguities:[],attempts:1,
   metadata:{provider_requested:'polza',model_requested:'openai/gpt-6-luna',provider_used:fallback?'multiai':'polza',
    model_used:fallback?'gpt-6-sol':'openai/gpt-6-luna',fallback_used:fallback,fallback_reason:fallback?'timeout':null}
  }}));
  await page.goto('/');
  await page.getByRole('button',{name:'Загрузить пример',exact:true}).click();
  await page.getByRole('button',{name:'Подключение к электросети',exact:true}).click();
  await page.getByRole('button',{name:'Создать BPMN',exact:true}).click();
  await expect(page.locator('.provider-info')).toContainText(fallback?'Модель: GPT-6 Sol · MultiAI (резервная модель)':'Модель: GPT-6 Luna · Polza AI');
  await expect(page.locator('.djs-shape[data-element-id="Check"]')).toBeVisible();
  await page.screenshot({path: test.info().outputPath('provider.png')});
 });
}
test('coverage fallback preserves generator label',async({page})=>{
 await page.route('**/api/process/generate',route=>route.fulfill({json:{
  process:JSON.parse(fs.readFileSync(path.resolve('../examples/02-grid-connection.json'),'utf8')),
  xml:fs.readFileSync(path.resolve('../examples/02-grid-connection.bpmn'),'utf8'),ambiguities:[],attempts:1,
  metadata:{provider_requested:'polza',model_requested:'openai/gpt-6-luna',provider_used:'polza',model_used:'openai/gpt-6-luna',
   fallback_used:true,business_coverage_provider_used:'multiai',business_coverage_model_used:'gpt-6-sol'}
 }}));
 await page.goto('/');await page.locator('textarea').first().fill('Оператор обрабатывает заявку.');
 await page.getByRole('button',{name:'Создать BPMN',exact:true}).click();
 await expect(page.locator('.provider-info')).toContainText('Модель: GPT-6 Luna · Polza AI');
 await expect(page.locator('.provider-info')).toContainText('Проверка полноты: GPT-6 Sol · MultiAI');
 await expect(page.locator('.provider-info')).not.toContainText('(резервная модель)');
});
