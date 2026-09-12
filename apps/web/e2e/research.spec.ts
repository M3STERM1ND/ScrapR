import { expect, test } from "@playwright/test";

import { fillObjective } from "./support";

/**
 * The Phase 0 exit condition, through a browser.
 *
 * Ask a question, watch the activity timeline fill in, read the report that
 * comes back with a citation and a retrieval date on the claim.
 *
 * Needs the API and the worker running. When they are not — someone running the
 * smoke suite on a laptop with only `npm run dev` — the whole file skips rather
 * than failing, because "the API is not running" is not a regression in the
 * frontend.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const OBJECTIVE = "What is driving revenue growth at Acme Corp in FY2025?";

test.beforeAll(async ({ request }) => {
  try {
    const health = await request.get(`${API_BASE_URL}/health`);
    test.skip(!health.ok(), "the API is not reachable");
  } catch {
    test.skip(true, "the API is not reachable");
  }
});

test("a question becomes an evidence-backed report", async ({ page }) => {
  await page.goto("/research/new");

  await fillObjective(page, OBJECTIVE);
  await page.getByRole("button", { name: "Start research" }).click();

  // The workspace opens immediately: the answer does not exist yet, and the
  // page says so rather than blocking on it.
  await expect(page).toHaveURL(/\/research\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(OBJECTIVE);

  // Activity arrives by polling `?after={seq}`, narrating the pipeline's
  // stages in the plain language `REQ-ACT-002` asks for.
  //
  // `exact` matters: the timeline also carries an aria-live region announcing
  // "<label>: <status>", and a loose match would resolve to both. Only the
  // fixed stage labels are asserted — the per-area ones are named by the plan,
  // so pinning them here would make a planning change look like a UI failure.
  const activity = page.getByRole("region", { name: "Activity" });
  await expect(
    activity.getByText("Understanding the objective", { exact: true }),
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    activity.getByText("Identifying research areas", { exact: true }),
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    activity.getByText("Building the report", { exact: true }),
  ).toBeVisible({ timeout: 30_000 });

  // And then the report, with the claim typed and its source shown. This is
  // the whole product promise in one assertion: no claim without evidence.
  const claim = page.locator(".claim").first();
  await expect(claim).toBeVisible({ timeout: 30_000 });
  await expect(claim.getByText("Fact")).toBeVisible();
  await expect(claim.getByRole("link")).toBeVisible();
  await expect(claim.getByText(/Read \w+ \d+, \d{4}/)).toBeVisible();
});

test("a claim's type is not signalled by colour alone", async ({ page }) => {
  /**
   * `REQ-SYNTH-002 AC-3` and `NFR-USE-002`. The word beside the claim is the
   * non-colour channel, and it has to be in the DOM — not a background image,
   * not a hue difference a greyscale print would flatten.
   */
  await page.goto("/research/new");
  await fillObjective(page, OBJECTIVE);
  await page.getByRole("button", { name: "Start research" }).click();

  const claim = page.locator(".claim").first();
  await expect(claim).toBeVisible({ timeout: 30_000 });

  const label = await claim.evaluate(
    (element) => element.querySelector(".claim-label")?.textContent ?? "",
  );
  expect(["Fact", "Analysis", "Forecast", "Uncertain"]).toContain(label);
});

test("a claim shows its confidence and its sources show their standing", async ({
  page,
}) => {
  /**
   * Phase 2's exit condition, from the reader's side: they can see where
   * information came from and how much it is worth.
   *
   * `REQ-EVID-015 AC-1` requires confidence be displayed *where the claim
   * appears*, and `REQ-EVID-003 AC-2` requires a source's tier be visible — so
   * a reader can see why a claim is rated as it is instead of being asked to
   * trust the rating. Both were computed in the pipeline before this test
   * existed; neither reached a screen.
   */
  await page.goto("/research/new");

  await fillObjective(page, OBJECTIVE);
  await page.getByRole("button", { name: "Start research" }).click();

  const claim = page.locator(".claim").first();
  await expect(claim).toBeVisible({ timeout: 30_000 });

  // The word, not the marks: the glyphs are aria-hidden decoration, so this
  // asserts what a screen reader would actually announce.
  await expect(claim.getByText(/(High|Moderate|Low) confidence/)).toBeVisible();

  await expect(
    claim.getByText(/(Primary|Established|Unverified) source/).first(),
  ).toBeVisible();
});
