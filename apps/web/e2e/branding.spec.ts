import { expect, test } from "@playwright/test";

/**
 * The ScrapR name and icon, as a browser and a screen reader meet them.
 *
 * No backend required. What is checked is what a regression would quietly
 * undo: the starter app's favicon coming back, a title losing the name, or the
 * wordmark reading "S ScrapR" because the mark and the full word both appear.
 */

test("the landing page is titled ScrapR and links the ScrapR icons", async ({ page, request }) => {
  await page.goto("/");

  await expect(page).toHaveTitle("ScrapR | AI Research");
  await expect(page.locator('link[rel="icon"][href^="/favicon.ico"]')).toHaveCount(1);
  await expect(page.locator('link[rel="icon"][href^="/icon.png"]')).toHaveCount(1);
  await expect(page.locator('link[rel="apple-touch-icon"]')).toHaveCount(1);

  const manifest = await (await request.get("/manifest.webmanifest")).json();
  expect(manifest.name).toBe("ScrapR");
  for (const icon of manifest.icons as { src: string }[]) {
    expect((await request.get(icon.src)).ok()).toBe(true);
  }
});

test("app pages put the name first in the title", async ({ page }) => {
  await page.goto("/signin");
  await expect(page).toHaveTitle("ScrapR | Sign in");

  await page.goto("/research/new");
  await expect(page).toHaveTitle("ScrapR | New research");
});

test("the wordmark is the mark followed by crapR, and is announced once as ScrapR", async ({
  page,
}) => {
  for (const path of ["/", "/research/new"]) {
    await page.goto(path);
    const home = page.getByRole("link", { name: "ScrapR home" }).first();

    await expect(home.locator("svg")).toHaveCount(1);
    // What is drawn: the mark, then "crapR". Never the whole word beside it.
    await expect(home.getByText("crapR", { exact: true })).toBeVisible();
    await expect(home.getByText("ScrapR", { exact: true })).toHaveClass(/sr-only/);
  }
});

test("the starter app's images are gone", async ({ request }) => {
  for (const path of ["/vercel.svg", "/next.svg"]) {
    expect((await request.get(path)).status()).toBe(404);
  }
});
