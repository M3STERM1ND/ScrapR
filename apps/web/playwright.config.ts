import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for the smoke suite.
 *
 * The suite is deliberately small. It is not trying to cover the product — unit
 * and integration tests do that, and the Python suite already proves the
 * pipeline end to end. What it covers is the part nothing else can: that the
 * built app boots in a real browser, that the flows a user takes actually
 * connect, and that the page is navigable by keyboard.
 *
 * `webServer` builds and serves the app rather than running `next dev`, because
 * a dev build is not what ships and its performance characteristics are nothing
 * like production's.
 */
export default defineConfig({
  testDir: "./e2e",
  // Serial, deliberately. The suite is nine tests and under a minute; running
  // them in parallel means several browsers, a Next server, an API and a worker
  // competing for one machine, and the contention surfaces as navigation
  // timeouts that read exactly like product failures. Flaky smoke tests get
  // ignored, and an ignored test is worse than no test.
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",

  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    // Artifacts only for failures: a green run should leave nothing behind.
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "off",
  },

  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],

  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: "npm run build && npm run start",
        url: "http://localhost:3000",
        reuseExistingServer: !process.env.CI,
        timeout: 180_000,
      },
});
