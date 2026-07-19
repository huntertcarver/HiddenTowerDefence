import { expect, test, type Page } from "@playwright/test";

test.describe.configure({ mode: "serial" });

async function login(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Operator login" }).click();
  await page.getByLabel("Operator token").fill("browser-test-token");
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("button", { name: "Operator online" })).toBeVisible();
}

test("renders a full-viewport top-down game on desktop", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Hidden Tower Defence" })).toBeVisible();
  await expect(page.locator("#game-root canvas")).toBeVisible();
  const viewport = page.viewportSize();
  const game = await page.locator("#game-root canvas").boundingBox();
  expect(game?.width).toBeGreaterThan((viewport?.width ?? 0) * 0.9);
  expect(game?.height).toBeGreaterThan((viewport?.height ?? 0) * 0.9);
  await expect(page.getByRole("region", { name: "Sanitized backend console" })).toBeVisible();
});

test("animates clean and restricted paths with console synchronization", async ({
  page,
}) => {
  await page.goto("/");
  await login(page);
  await page.getByText("Demo sequence", { exact: true }).click();

  await page.getByRole("button", { name: "clean", exact: true }).click();
  await expect(page.locator("#console-list")).toContainText("tool_completed", {
    timeout: 15_000,
  });
  const cleanEvent = page.locator(".console-entry").filter({ hasText: "content_received" }).first();
  await cleanEvent.click();
  await expect(page.locator("#entity-details h3")).toContainText("fixture:clean-ai-tool");

  await page.getByRole("button", { name: "restricted", exact: true }).click();
  const approval = page
    .locator(".approval-card")
    .filter({ hasText: "fixture:restricted-injection" });
  await expect(approval).toBeVisible();
  await approval.getByRole("button", { name: "Approve" }).click();
  await expect(page.locator("#console-list")).toContainText("approval_resolved");
});

test("reconstructs authoritative state after refresh on tablet", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#game-root canvas")).toBeVisible();
  await page.reload();
  await expect(page.locator("#game-root canvas")).toBeVisible();
  await expect(page.locator("#console-list")).toContainText("content_received");
  await expect(page.locator(".control-dock")).toBeVisible();
});
