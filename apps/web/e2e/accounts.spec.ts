import { expect, test, type Page } from "@playwright/test";

import { fillObjective } from "./support";

/**
 * Flow E and Flow F through a browser (`REQ-AUTH-001..007`, `DEC-16`, `DEC-17`).
 *
 * Research anonymously, save it to a new account from inside the workspace,
 * sign out, sign back in, and find it in saved research. Then delete it and
 * watch it go. Cookies are real and HttpOnly, so this is the one suite that
 * proves the browser actually carries the session between pages.
 *
 * Needs the API and worker, like the research suite, and skips without them.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const PASSWORD = "a long enough password";

test.beforeAll(async ({ request }) => {
  try {
    const health = await request.get(`${API_BASE_URL}/health`);
    test.skip(!health.ok(), "the API is not reachable");
  } catch {
    test.skip(true, "the API is not reachable");
  }
});

function uniqueEmail(): string {
  return `reader-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

async function fillField(page: Page, label: string, value: string): Promise<void> {
  const field = page.getByLabel(label, { exact: true });
  await expect(async () => {
    await field.fill(value);
    await expect(field).toHaveValue(value, { timeout: 1_000 });
  }).toPass({ timeout: 15_000 });
}

test("research saved to a new account survives signing out and back in", async ({ page }) => {
  const objective = `What is Acme Corp's hiring outlook? ${Date.now()}`;
  const email = uniqueEmail();

  // Flow A, anonymously. No account wall anywhere (`REQ-AUTH-001`).
  await page.goto("/research/new");
  await fillObjective(page, objective);
  await page.getByRole("button", { name: "Start research" }).click();
  await expect(page).toHaveURL(/\/research\/[0-9a-f-]{36}$/);
  const workspaceUrl = page.url();

  // Flow E: the offer is inside the research (`REQ-AUTH-003 AC-1`).
  await page.getByRole("link", { name: "Save to an account" }).click();
  await expect(page).toHaveURL(/\/signup\?next=/);
  await fillField(page, "Email", email);
  await fillField(page, "Password", PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  // Back where the reader was, now signed in.
  await expect(page).toHaveURL(workspaceUrl);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(objective);
  await expect(page.getByRole("link", { name: "Save to an account" })).toHaveCount(0);

  // Leave.
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/research\/new$/);

  // Flow F: sign in, open history, open the research.
  await page.goto("/signin?next=/history");
  await fillField(page, "Email", email);
  await fillField(page, "Password", PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/history$/);

  const saved = page.getByRole("list", { name: "Saved research" });
  await expect(saved.getByRole("link", { name: objective })).toBeVisible();
  await saved.getByRole("link", { name: objective }).click();
  await expect(page).toHaveURL(workspaceUrl);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(objective);

  // Deletion, confirmed in the page (`REQ-SEC-008`).
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByRole("button", { name: "Delete permanently" }).click();
  await expect(page).toHaveURL(/\/research\/new$/);

  await page.goto("/history");
  await expect(page.getByText("Nothing saved yet.")).toBeVisible();
});

test("a wrong password is refused without saying whether the account exists", async ({ page }) => {
  await page.goto("/signin");
  await fillField(page, "Email", uniqueEmail());
  await fillField(page, "Password", "not the right password");
  await page.getByRole("button", { name: "Sign in" }).click();

  // By text, not by role: Next's route announcer is also `role="alert"`.
  await expect(
    page.getByText("That email and password do not match an account."),
  ).toBeVisible();
});

test("history asks a signed-out visitor to sign in rather than showing nothing", async ({
  page,
}) => {
  await page.goto("/history");

  await expect(page.getByText(/Saved research lives in an account/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in", exact: true }).last()).toBeVisible();
});

test("a sign-in link cannot be used to redirect off the site", async ({ page }) => {
  const email = uniqueEmail();
  await page.goto(`/signup?next=${encodeURIComponent("//evil.example/steal")}`);
  await fillField(page, "Email", email);
  await fillField(page, "Password", PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page).toHaveURL(/localhost:3000\/history$/);
});
