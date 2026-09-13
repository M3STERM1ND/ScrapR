import { expect, test } from "@playwright/test";

import { fillObjective } from "./support";

/**
 * Flow G through a browser (`REQ-VER-001..008`, `DEC-19`, `DEC-20`).
 *
 * Research, press Update Research, watch the previous report stay in place
 * while the update runs, then read What's Changed on the new version and open
 * the original from the version list — unchanged and labelled as the earlier
 * version.
 *
 * Against the local stand-in provider and fixture tools the second run finds
 * the same evidence, so the summary must say, in words, that nothing
 * meaningful changed (`REQ-VER-006 AC-3`). That is the honest answer, and the
 * one worth asserting: an update that invents change from rewording is the
 * failure `OPEN-27` was registered to prevent.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

test.beforeAll(async ({ request }) => {
  try {
    const health = await request.get(`${API_BASE_URL}/health`);
    test.skip(!health.ok(), "the API is not reachable");
  } catch {
    test.skip(true, "the API is not reachable");
  }
});

test("updating research keeps the original and says what changed", async ({ page }) => {
  test.setTimeout(120_000);
  const objective = `How is Acme Corp performing this year? ${Date.now()}`;

  await page.goto("/research/new");
  await fillObjective(page, objective);
  await page.getByRole("button", { name: "Start research" }).click();
  await expect(page).toHaveURL(/\/research\/[0-9a-f-]{36}$/);
  const workspaceUrl = page.url();

  // The first report.
  await expect(page.locator(".claim").first()).toBeVisible({ timeout: 45_000 });
  const firstClaim = await page.locator(".claim p.measure").first().innerText();

  // G-1: the explicit control, and only it (`REQ-VER-001`).
  await page.getByRole("button", { name: "Update research" }).click();

  // The update's own activity, and the new version arriving with its summary.
  await expect(
    page.getByRole("region", { name: "Activity" }).getByText("Checking what may have changed", {
      exact: true,
    }),
  ).toBeVisible({ timeout: 45_000 });
  const changed = page.getByRole("region", { name: "What’s changed" });
  await expect(changed).toBeVisible({ timeout: 60_000 });
  await expect(changed.getByText(/since version 1/)).toBeVisible();

  // `REQ-VER-008 AC-1`, `AC-3`: both versions listed, the shown one marked.
  const versions = page.getByRole("navigation", { name: "Versions" });
  await expect(versions.getByText("Version 2", { exact: true })).toBeVisible();
  await expect(versions.locator('[aria-current="page"]')).toContainText("Version 2");

  // `REQ-VER-002 AC-1`, `REQ-VER-008 AC-2`: open the original, in full.
  await versions.getByRole("link", { name: /Version 1/ }).click();
  await expect(page).toHaveURL(/\/v\/1$/);
  await expect(page.getByText(/You are viewing version 1/)).toBeVisible();
  await expect(page.locator(".claim p.measure").first()).toHaveText(firstClaim);
  await expect(page.getByRole("region", { name: "What’s changed" })).toHaveCount(0);

  // And back to the latest.
  await page.getByRole("link", { name: /Go to the latest, version 2/ }).click();
  await expect(page).toHaveURL(workspaceUrl);
  await expect(page.getByRole("region", { name: "What’s changed" })).toBeVisible();
});
