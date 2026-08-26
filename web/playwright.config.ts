import { defineConfig, devices } from "@playwright/test";

// Real backend, real LLM calls, real tool execution -- same "no mocks"
// standard the pytest suite holds itself to (see README). Timeouts are
// generous because a multi-step agent_step loop can take 30-90s of real
// wall-clock time across several sequential OpenAI calls.
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false, // sequential -- shares one Postgres/Chroma backend, no point racing
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
