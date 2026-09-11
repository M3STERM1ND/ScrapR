import { expect, test } from "@playwright/test";

import { fillObjective } from "./support";

/**
 * The surfaces that must load, and the keyboard path through them.
 *
 * No backend required: these assert the app boots and is navigable. The flow
 * that needs the API lives in `research.spec.ts` and skips itself when the API
 * is not running.
 */

test("the landing page states what the product does", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

test("intake asks one question and refuses an empty one", async ({ page }) => {
  await page.goto("/research/new");

  const submit = page.getByRole("button", { name: "Start research" });
  await expect(submit).toBeDisabled();

  await fillObjective(page, "Why did Acme revenue grow?");
  await expect(submit).toBeEnabled();
});

test("an objective that is too short cannot be submitted", async ({ page }) => {
  await page.goto("/research/new");

  await page.getByLabel("Your question").fill("short");

  await expect(page.getByRole("button", { name: "Start research" })).toBeDisabled();
  await expect(page.getByText(/more words/i)).toBeVisible();
});

test("an example fills the form so a first-time visitor can just start", async ({
  page,
}) => {
  await page.goto("/research/new");
  const example = page.getByRole("button", {
    name: /positioned against OpenAI/i,
  });

  await example.click();

  await expect(page.getByLabel("Your question")).toHaveValue(
    /positioned against OpenAI/,
  );
});

test("optional context stays folded away until asked for", async ({ page }) => {
  await page.goto("/research/new");

  await expect(page.getByLabel("Company or subject")).toBeHidden();
  await page.getByRole("button", { name: /Add context/i }).click();

  await expect(page.getByLabel("Company or subject")).toBeVisible();
  // The toggle renames itself once open, so this is a different locator than
  // the one clicked above — asserting on the old name would only prove the
  // element vanished.
  await expect(page.getByRole("button", { name: /Hide extra context/i })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
});

test("the intake form is reachable and submittable by keyboard alone", async ({
  page,
}) => {
  await page.goto("/research/new");

  // The textarea autofocuses, so a keyboard user lands on the one thing the
  // page is asking for.
  await expect(page.getByLabel("Your question")).toBeFocused();
  await page.keyboard.type("Why did Acme revenue grow in FY2025?");

  await page.keyboard.press("Tab"); // context toggle
  await page.keyboard.press("Tab"); // submit

  await expect(page.getByRole("button", { name: "Start research" })).toBeFocused();
});

test("every interactive element has an accessible name", async ({ page }) => {
  await page.goto("/research/new");

  const unnamed = await page.evaluate(() => {
    const interactive = document.querySelectorAll<HTMLElement>(
      "a, button, input, textarea, select",
    );
    return [...interactive]
      .filter((element) => {
        const label =
          element.getAttribute("aria-label") ??
          element.textContent?.trim() ??
          "";
        const labelled =
          element.id && document.querySelector(`label[for="${element.id}"]`);
        return !label && !labelled;
      })
      .map((element) => element.outerHTML.slice(0, 80));
  });

  expect(unnamed).toEqual([]);
});
