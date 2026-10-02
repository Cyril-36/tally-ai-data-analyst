import os from 'node:os';
import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

/**
 * Starts the real backend (LLM_PROVIDER=fake, a scripted provider that runs the whole validated
 * path without a paid model) on a throwaway data dir, plus the Vite dev server (which proxies /api).
 *
 *   E2E_BACKEND_COMMAND  replace the backend start command (listen on E2E_BACKEND_PORT)
 *   E2E_BACKEND_PORT / E2E_FRONTEND_PORT  use free ports when the defaults are occupied
 *   E2E_REUSE_SERVERS=1  reuse servers that are already running instead of starting fresh ones
 */
const DEMO_PASSWORD = process.env.DEMO_PASSWORD ?? 'e2e-demo-password-1';
const backendPort = Number(process.env.E2E_BACKEND_PORT ?? 8000);
const frontendPort = Number(process.env.E2E_FRONTEND_PORT ?? 5173);
const dataDir = path.join(os.tmpdir(), 'tally-e2e-data');
const env = `DATA_DIR="${dataDir}" DEMO_PASSWORD="${DEMO_PASSWORD}" LLM_PROVIDER=fake`;
const backendCommand =
  process.env.E2E_BACKEND_COMMAND ??
  `sh -c 'rm -rf "${dataDir}" && mkdir -p "${dataDir}" && ${env} uv run app seed-demo && ${env} exec uv run uvicorn app.main:app --port ${backendPort}'`;
const reuse = !!process.env.E2E_REUSE_SERVERS;

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: `http://localhost:${frontendPort}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    acceptDownloads: true,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: backendCommand,
      cwd: '../backend',
      url: `http://localhost:${backendPort}/api/health`,
      reuseExistingServer: reuse,
      timeout: 120_000,
      stdout: 'ignore',
    },
    {
      command: `npm run dev -- --port ${frontendPort} --strictPort`,
      url: `http://localhost:${frontendPort}`,
      reuseExistingServer: reuse,
      timeout: 60_000,
    },
  ],
});
