import { defineConfig, devices } from '@playwright/test'

// `make test-browser` builds the frontend and runs this against a TEST_MODE backend that
// e2e/stack.mjs starts on https://localhost:8445 (serving frontend/dist). Set BASE_URL to
// run against an already running stack instead.
const baseURL = process.env.BASE_URL ?? 'https://localhost:8445'

export default defineConfig({
  testDir: 'e2e',
  testMatch: /.*\.spec\.ts/,
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: 'test-results',
  use: {
    baseURL,
    ignoreHTTPSErrors: true,
    screenshot: 'only-on-failure',
    launchOptions: {
      args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
    },
  },
  webServer: process.env.BASE_URL
    ? undefined
    : {
        command: 'node e2e/stack.mjs',
        url: `${baseURL}/api/health`,
        ignoreHTTPSErrors: true,
        timeout: 90_000,
        reuseExistingServer: false,
        stdout: 'pipe',
        stderr: 'pipe',
      },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
