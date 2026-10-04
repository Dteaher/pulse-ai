import { test as base } from '@playwright/test';
import path from 'node:path';

export * from '@playwright/test';

// Historical screenshots are evidence, not mutable test output. OneDrive can
// lock an existing PNG while syncing it. Keep every new capture in the unique
// Playwright artifact directory; never skip a screenshot or an assertion.
export const test = base.extend({
  page: async ({ page }, use, testInfo) => {
    const original = page.screenshot.bind(page);
    const examples = path.resolve('../examples');
    let capture = 0;
    page.screenshot = options => {
      const relative = options?.path ? path.relative(examples, path.resolve(options.path)) : undefined;
      const historical = relative !== undefined && !relative.startsWith('..') && !path.isAbsolute(relative);
      return original(historical
        ? { ...options, path: testInfo.outputPath(`${++capture}-${path.basename(options!.path!)}`) }
        : options);
    };
    await use(page);
  },
});
