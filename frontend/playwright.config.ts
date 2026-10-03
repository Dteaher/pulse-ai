import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  retries: 0,
  webServer: [
    {
      command: '..\\.venv\\Scripts\\python.exe tests/serve_mock.py',
      url: 'http://127.0.0.1:8001/api/health',
      reuseExistingServer: false,
    },
    {
      command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: true,
    },
  ],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    viewport: { width: 1366, height: 768 },
    browserName: 'chromium',
    channel:
      process.env.PULSE_TEST_CHANNEL === 'chromium'
        ? undefined
        : process.env.PULSE_TEST_CHANNEL || 'msedge',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
