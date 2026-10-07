import { defineConfig } from '@playwright/test';
import path from 'node:path';
const python =
  process.env.PULSE_TEST_PYTHON ||
  path.resolve('../.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  webServer: [
    {
      command: `"${python}" tests/serve_mock.py`,
      url: 'http://127.0.0.1:8003/api/health',
      reuseExistingServer: false,
    },
    {
      command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5181 --strictPort',
      url: 'http://127.0.0.1:5181',
      reuseExistingServer: false,
      env: { PULSE_API_TARGET: 'http://127.0.0.1:8003' },
    },
  ],
  use: {
    baseURL: 'http://127.0.0.1:5181',
    viewport: { width: 1366, height: 768 },
    browserName: 'chromium',
    channel:
      process.env.PULSE_TEST_CHANNEL === 'chromium' ||
      (process.env.CI && !process.env.PULSE_TEST_CHANNEL)
        ? undefined
        : process.env.PULSE_TEST_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined),
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
