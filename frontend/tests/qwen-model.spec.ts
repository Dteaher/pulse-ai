import { test, expect } from './fixtures';
import path from 'node:path';

test('Qwen real simple result imports and renders in bpmn-js', async ({ page }) => {
  await page.goto('/');
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Открыть .bpmn', exact: true }).click();
  await (await chooser).setFiles(path.resolve('../examples/qwen/simple.bpmn'));
  await expect(page.locator('.djs-element[data-element-id]').first()).toBeVisible();
  await expect(page.locator('#command')).toBeEnabled();
  await expect(page.locator('.error-box')).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath('qwen-real-simple.png') });
});
