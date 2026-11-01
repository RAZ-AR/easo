import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end tests against a running stack with the demo data (DEV_LOGIN on, see CLAUDE.md):
 *   docker compose up -d && docker compose run --rm api python -m app.seed
 *   cd web && npm run e2e
 * E2E_BASE_URL points them elsewhere (default: the compose web container).
 */
export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false, // the tests share one database and its demo users
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  timeout: 30_000,
  expect: { timeout: 7_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8090',
    locale: 'en-US',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] }, testMatch: /guest\.spec\.ts/ },
  ],
})
