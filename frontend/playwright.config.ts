import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const REPO_ROOT = path.resolve(__dirname, '..');
const FIXTURES_DIR = path.join(REPO_ROOT, 'src', 'tests', 'fixtures');
const TEST_DATA_DIR = path.join(REPO_ROOT, 'data-e2e');
const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT ?? 8011);
const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT ?? 5179);

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    headless: true,
    viewport: { width: 1280, height: 800 },
    screenshot: 'only-on-failure',
    video: 'off',
    trace: 'retain-on-failure',
    actionTimeout: 5_000,
    navigationTimeout: 15_000,
  },

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: {
          executablePath: process.env.CHROMIUM_PATH ?? '/usr/bin/chromium',
        },
      },
    },
  ],

  globalSetup: './tests/helpers/global-setup',

  webServer: [
    {
      command: `uv run python main.py --host 127.0.0.1 --port ${BACKEND_PORT} --no-auth --mock-sdk --fixtures-dir ${FIXTURES_DIR} --data-dir ${TEST_DATA_DIR}`,
      cwd: REPO_ROOT,
      url: `http://127.0.0.1:${BACKEND_PORT}/api/projects`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      stdout: 'pipe',
      stderr: 'pipe',
      env: { PYTHONUNBUFFERED: '1' },
    },
    {
      command: `npm run dev -- --port ${FRONTEND_PORT} --strictPort`,
      cwd: __dirname,
      url: `http://127.0.0.1:${FRONTEND_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      env: {
        // vite.config.js reads VITE_BACKEND_HOST and VITE_BACKEND_PORT
        VITE_BACKEND_HOST: '127.0.0.1',
        VITE_BACKEND_PORT: String(BACKEND_PORT),
      },
    },
  ],
});
