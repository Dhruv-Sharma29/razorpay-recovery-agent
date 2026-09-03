import { defineConfig } from "@playwright/test";

/**
 * Contrast/visual regression config, kept separate from the vitest unit
 * suite so `npm test` stays fast. Run with `npm run test:contrast`.
 */
export default defineConfig({
  testDir: "./e2e",
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:4173",
  },
  webServer: {
    command: "npm run dev -- --port 4173",
    url: "http://localhost:4173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
