import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against a live stack (web + api + worker). Start it with:
 *
 *   docker compose --env-file .env -f infra/docker-compose.yml up -d
 *   cd web && npm run e2e
 *
 * The suite is intentionally serial: the teacher journey mutates released
 * content and the admin journey writes audit rows. Each spec cleans up after
 * itself, but parallel workers would still race on shared seed data.
 */
export default defineConfig({
  testDir: "./e2e",
  outputDir: "test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
