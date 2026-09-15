import { expect, test, type Page } from "@playwright/test";

/**
 * The landing navbar: which actions are routes, which are anchors, and what it
 * shows for a signed-in reader, at desktop and phone widths.
 *
 * No backend required. The session check is answered in the browser, so both
 * states are deterministic; `accounts.spec.ts` proves the same thing against a
 * real session cookie when the API is running.
 */

const EMAIL = "reader@example.com";
const MOBILE = { width: 390, height: 844 };

/** Answer the session check as `email`, or as nobody. Sign-out flips it. */
async function mockSession(page: Page, email: string | null): Promise<void> {
  let current = email;
  const cors = {
    "access-control-allow-origin": "http://localhost:3000",
    "access-control-allow-credentials": "true",
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "GET, POST, OPTIONS",
  };

  await page.route("**/v1/auth/session", (route) =>
    route.request().method() === "OPTIONS"
      ? route.fulfill({ status: 204, headers: cors })
      : route.fulfill({
          status: 200,
          headers: cors,
          contentType: "application/json",
          body: JSON.stringify({
            account: current ? { email: current, created_at: "2026-01-01T00:00:00Z" } : null,
          }),
        }),
  );
  await page.route("**/v1/auth/signout", (route) => {
    if (route.request().method() !== "OPTIONS") current = null;
    return route.fulfill({ status: 204, headers: cors });
  });
}

const mainNav = (page: Page) => page.getByRole("navigation", { name: "Main" });

test.describe("desktop", () => {
  test("signed out: Sign in and Start researching go to their pages, not a section", async ({
    page,
  }) => {
    await mockSession(page, null);
    await page.goto("/");
    const nav = mainNav(page);

    await expect(nav.getByRole("link", { name: "Sign in" })).toBeVisible();
    await expect(nav.getByRole("button", { name: "Account" })).toHaveCount(0);

    await nav.getByRole("link", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/signin$/);

    await page.goto("/");
    await nav.getByRole("link", { name: "Start researching" }).click();
    await expect(page).toHaveURL(/\/research\/new$/);
  });

  test("the hero's Start researching goes to the research page", async ({ page }) => {
    await mockSession(page, null);
    await page.goto("/");

    await page.getByRole("main").getByRole("link", { name: "Start researching" }).click();
    await expect(page).toHaveURL(/\/research\/new$/);
  });

  test("section links still scroll within the landing page", async ({ page }) => {
    await mockSession(page, null);
    await page.goto("/");

    await mainNav(page).getByRole("link", { name: "FAQ" }).click();
    await expect(page).toHaveURL(/\/#faq$/);
  });

  test("signed in: an avatar instead of Sign in, holding the account and sign-out", async ({
    page,
  }) => {
    await mockSession(page, EMAIL);
    await page.goto("/");
    const nav = mainNav(page);
    const avatar = nav.getByRole("button", { name: "Account" });

    await expect(avatar).toBeVisible();
    await expect(nav.getByRole("link", { name: "Sign in" })).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Start researching" })).toBeVisible();
    await expect(nav.getByText(EMAIL)).toBeHidden();

    await avatar.click();
    await expect(avatar).toHaveAttribute("aria-expanded", "true");
    await expect(nav.getByText(EMAIL)).toBeVisible();

    // Escape closes it, and the reader is back on the avatar.
    await page.keyboard.press("Escape");
    await expect(nav.getByText(EMAIL)).toBeHidden();
    await expect(avatar).toBeFocused();

    await avatar.click();
    await nav.getByRole("button", { name: "Sign out" }).click();
    await expect(nav.getByRole("link", { name: "Sign in" })).toBeVisible();
    await expect(avatar).toHaveCount(0);
    await expect(page).toHaveURL(/localhost:3000\/$/);
  });

  test("signed in: the product header shows the same avatar, not the email", async ({
    page,
  }) => {
    await mockSession(page, EMAIL);
    await page.goto("/research/new");
    const header = page.getByRole("banner");

    await expect(header.getByRole("button", { name: "Account" })).toBeVisible();
    await expect(header.getByText(EMAIL)).toBeHidden();
  });
});

test.describe("mobile", () => {
  test.use({ viewport: MOBILE });

  test("signed out: the menu offers Sign in, navigates to it, and closes", async ({ page }) => {
    await mockSession(page, null);
    await page.goto("/");

    await page.getByRole("button", { name: "Open menu" }).click();
    const menu = page.locator("#mobile-nav");
    await expect(menu).toBeVisible();
    for (const label of ["How it works", "Evidence", "Workspace", "FAQ", "Sign in", "Start researching"]) {
      await expect(menu.getByRole("link", { name: label })).toBeVisible();
    }
    await expect(menu.getByRole("button", { name: "Sign out" })).toHaveCount(0);

    await menu.getByRole("link", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/signin$/);
  });

  test("signed out: Start researching in the menu goes to the research page", async ({
    page,
  }) => {
    await mockSession(page, null);
    await page.goto("/");

    await page.getByRole("button", { name: "Open menu" }).click();
    await page.locator("#mobile-nav").getByRole("link", { name: "Start researching" }).click();
    await expect(page).toHaveURL(/\/research\/new$/);
  });

  test("a section link closes the menu", async ({ page }) => {
    await mockSession(page, null);
    await page.goto("/");

    await page.getByRole("button", { name: "Open menu" }).click();
    await page.locator("#mobile-nav").getByRole("link", { name: "Evidence" }).click();
    await expect(page.locator("#mobile-nav")).toBeHidden();
    await expect(page).toHaveURL(/\/#evidence$/);
  });

  test("signed in: the menu holds the account and sign-out, not Sign in", async ({ page }) => {
    await mockSession(page, EMAIL);
    await page.goto("/");

    // Wait for the session answer before opening, as a reader would see it.
    await expect(page.getByRole("button", { name: "Open menu" })).toBeVisible();
    await page.getByRole("button", { name: "Open menu" }).click();
    const menu = page.locator("#mobile-nav");

    await expect(menu.getByRole("button", { name: "Sign out" })).toBeVisible();
    await expect(menu.getByText("Signed in as")).toBeVisible();
    await expect(menu.getByRole("link", { name: "Start researching" })).toBeVisible();
    await expect(menu.getByRole("link", { name: "Sign in" })).toHaveCount(0);

    await menu.getByRole("button", { name: "Sign out" }).click();
    await expect(menu).toBeHidden();
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(menu.getByRole("link", { name: "Sign in" })).toBeVisible();
  });
});
