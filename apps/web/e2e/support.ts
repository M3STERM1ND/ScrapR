import { expect, type Page } from "@playwright/test";

/**
 * Type into the objective field, tolerating hydration.
 *
 * The textarea is a controlled React input. Filling it before hydration puts
 * text into the DOM that React then discards when it takes over, leaving an
 * empty field and a disabled button — a failure that looks like a product bug
 * and is not one. Retrying the fill until the value sticks is the honest fix:
 * a real user who types early sees the same thing and types again.
 */
export async function fillObjective(page: Page, objective: string): Promise<void> {
  const field = page.getByLabel("Your question");

  await expect(async () => {
    await field.fill(objective);
    await expect(field).toHaveValue(objective, { timeout: 1_000 });
  }).toPass({ timeout: 15_000 });
}
