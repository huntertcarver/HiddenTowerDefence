import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "tablet-chromium",
      use: { ...devices["iPad Pro 11"], browserName: "chromium" },
    },
  ],
  webServer: {
    command:
      "rm -rf /tmp/hidden-tower-browser && cd .. && " +
      "env -u APIFY_API_TOKEN -u HiddenLayer_API_ClientID " +
      "-u HiddenLayer_API_ClientSecret " +
      "-u NVIDIA_nemotron-3-ultra-550b-a55b_API_KEY " +
      "environment=test data_dir=/tmp/hidden-tower-browser " +
      "OPERATOR_TOKEN=browser-test-token .venv/bin/python -m uvicorn " +
      "app.main:app --host 127.0.0.1 --port 8765",
    url: "http://127.0.0.1:8765/health",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
