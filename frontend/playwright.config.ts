import { defineConfig, devices } from "@playwright/test";

/**
 * The browser suite.
 *
 * Every spec except `live-smoke` runs against a stubbed knowledge service
 * (`e2e/fixtures.ts`): it needs no credentials and no network, and the fixture
 * records every request and fails any that is not a knowledge or auth call.
 *
 * Locally it drives `next dev`; CI sets `E2E_WEB_SERVER=preview` so the suite
 * runs against the static export it is about to ship instead.
 */
const PORT = Number(process.env.E2E_PORT || 3002);
const BASE_URL = process.env.E2E_BASE_URL || `http://localhost:${PORT}`;

const WEB_SERVER =
  process.env.E2E_WEB_SERVER === "preview"
    ? `bun scripts/preview.ts --port ${PORT}`
    : `bunx next dev --turbopack --port ${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  /**
   * Ten seconds, not Playwright's five: a cold dev server compiling a route on
   * first request pushes the first assertion past five seconds often enough to
   * drop a test a run. Retries stay at zero locally so a real race stays visible.
   */
  expect: { timeout: 10_000 },
  timeout: 45_000,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: WEB_SERVER,
        url: `${BASE_URL}/document-intelligence/`,
        reuseExistingServer: !process.env.CI,
        timeout: 180_000,
      },
});
