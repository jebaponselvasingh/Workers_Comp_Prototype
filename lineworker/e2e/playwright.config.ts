import { defineConfig, devices } from "@playwright/test";

/**
 * AD-15 story-gate suite. Deterministic by construction:
 * one worker, no parallelism, DB reset via the `setup` project dependency
 * (never globalSetup) before specs run.
 */
export default defineConfig({
  testDir: ".",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8081",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "setup",
      testMatch: /fixtures\/db-reset\.setup\.ts/,
    },
    {
      name: "stories",
      testMatch: /stories\/.*\.spec\.ts/,
      dependencies: ["setup"],
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
