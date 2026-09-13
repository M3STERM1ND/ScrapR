import { expect, test } from "@playwright/test";

import { fillObjective } from "./support";

/**
 * Flow D through a browser, anonymously (`REQ-EXP-001..010`).
 *
 * Research, open Export, pick a format and a theme, watch the export go from
 * queued to ready, and download it. The download is a real file from real
 * object storage, checked for the right kind of content, which is the only way
 * to know the signed link, the worker and the renderer all joined up.
 *
 * Needs the API, the worker and MinIO, and skips without the API.
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

test("an anonymous reader exports research as a PDF and a deck", async ({ page }) => {
  test.setTimeout(120_000);

  await page.goto("/research/new");
  await fillObjective(page, `What is Acme Corp's revenue outlook? ${Date.now()}`);
  await page.getByRole("button", { name: "Start research" }).click();
  await expect(page.locator(".claim").first()).toBeVisible({ timeout: 45_000 });

  await page.getByRole("button", { name: "Export", exact: true }).click();
  const panel = page.getByRole("region", { name: /Export version 1/ });
  await expect(panel).toBeVisible();

  // All six themes are offered (`REQ-EXP-003 AC-1`).
  for (const name of ["Professional", "Investor", "Modern", "Corporate", "Minimal", "Dark"]) {
    await expect(panel.getByRole("radio", { name: new RegExp(name) })).toBeVisible();
  }

  // PDF in Investor.
  await panel.getByRole("radio", { name: /Investor/ }).check();
  await panel.getByRole("button", { name: "Create PDF" }).click();
  const exports = panel.getByRole("list", { name: "Exports" });
  await expect(exports.getByText(/PDF · Investor/)).toBeVisible();
  await expect(exports.getByRole("button", { name: "Download" })).toBeVisible({ timeout: 45_000 });

  const [pdf] = await Promise.all([
    page.waitForEvent("download"),
    exports.getByRole("button", { name: "Download" }).click(),
  ]);
  expect(pdf.suggestedFilename()).toBe("scrapr-research-v1-investor.pdf");
  const pdfPath = await pdf.path();
  const { readFile } = await import("node:fs/promises");
  expect((await readFile(pdfPath)).subarray(0, 5).toString()).toBe("%PDF-");

  // PowerPoint in Dark.
  await panel.getByRole("radio", { name: /PowerPoint/ }).check();
  await panel.getByRole("radio", { name: /Dark/ }).check();
  await panel.getByRole("button", { name: "Create PowerPoint" }).click();
  const deckRow = exports.getByRole("listitem").filter({ hasText: "PowerPoint · Dark" });
  await expect(deckRow.getByRole("button", { name: "Download" })).toBeVisible({ timeout: 45_000 });

  const [deck] = await Promise.all([
    page.waitForEvent("download"),
    deckRow.getByRole("button", { name: "Download" }).click(),
  ]);
  expect(deck.suggestedFilename()).toBe("scrapr-research-v1-dark.pptx");
  const deckBytes = await readFile(await deck.path());
  expect(deckBytes.subarray(0, 2).toString()).toBe("PK");
});
