import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * The landing page shows all of its content whatever the reader's motion
 * setting, on a fresh load.
 *
 * The regression this guards: the animated sections chose different markup when
 * the operating system asked for reduced motion. The server cannot know that
 * setting, so it rendered the animated version, hidden in its starting state;
 * the browser's first render took the static branch, hydration kept the
 * server's inline styles, and nothing ever animated them away. A reader with
 * "animation effects" off saw a navbar over a blank page.
 *
 * Playwright's `toBeVisible` counts `opacity: 0` and a line translated out of
 * its clipping box as visible, which is exactly the broken state, so these
 * checks measure what a person would see instead. No backend required.
 */

/** Opacity as rendered: the product of the element's and every ancestor's. */
async function renderedOpacity(locator: Locator): Promise<number> {
  return locator.evaluate((element) => {
    let opacity = 1;
    for (let node: Element | null = element; node; node = node.parentElement) {
      opacity *= Number(getComputedStyle(node).opacity);
    }
    return opacity;
  });
}

async function expectShown(locator: Locator): Promise<void> {
  await expect(async () => {
    expect(await renderedOpacity(locator)).toBe(1);
  }).toPass({ timeout: 5_000 });
}

/** Each headline line must sit inside its clipping span, not below it. */
async function expectHeadlineInPlace(page: Page): Promise<void> {
  const lines = page.locator("h1 > span > span");
  await expect(lines).toHaveCount(2);
  await expect(async () => {
    const offsets = await lines.evaluateAll((spans) =>
      spans.map((span) => {
        const clip = span.parentElement!.getBoundingClientRect();
        return Math.abs(span.getBoundingClientRect().top - clip.top);
      }),
    );
    for (const offset of offsets) expect(offset).toBeLessThan(1);
  }).toPass({ timeout: 5_000 });
}

/**
 * Elements the page left at opacity 0 once every section has been scrolled
 * through. Closed disclosures are `aria-hidden` on purpose and do not count.
 */
async function stuckHidden(page: Page): Promise<string[]> {
  return page.locator("main").evaluate((main) =>
    [...main.querySelectorAll<HTMLElement>("[style]")]
      .filter((element) => !element.closest('[aria-hidden="true"]'))
      .filter((element) => getComputedStyle(element).opacity === "0")
      .map((element) => element.outerHTML.slice(0, 100)),
  );
}


for (const reducedMotion of ["reduce", "no-preference"] as const) {
  test.describe(`motion preference: ${reducedMotion}`, () => {
    test.use({ reducedMotion });

    test("a fresh load shows the hero headline and its content", async ({ page }) => {
      await page.goto("/");

      await expectHeadlineInPlace(page);
      await expectShown(page.getByText("ScrapR reads the filings", { exact: false }));
      await expectShown(page.getByRole("main").getByRole("link", { name: "Start researching" }));
      await expectShown(page.getByText("Analyze NVIDIA as a company", { exact: false }));
    });

    test("every landing section is shown once scrolled to, and nothing stays hidden", async ({
      page,
    }) => {
      await page.goto("/");
      const ids = await page
        .locator("main section[id]")
        .evaluateAll((sections) => sections.map((section) => section.id));
      expect(ids.length).toBeGreaterThan(5);

      // One heading at a time, as a reader scrolls. Content appears as it
      // enters the viewport, so each heading is brought into view itself: a
      // tall section scrolled to by its middle leaves its heading above the
      // fold, never asked to appear. The hero is `#top`, checked above.
      for (const id of ids.filter((id) => id !== "top")) {
        const heading = page.locator(`#${id} h2`).first();
        await heading.scrollIntoViewIfNeeded();
        await expectShown(heading);
      }

      // Then the whole page top to bottom, so everything below each heading has
      // entered the viewport too before asking whether anything stayed hidden.
      await page.evaluate(() => window.scrollTo(0, 0));
      const height = await page.evaluate(() => document.documentElement.scrollHeight);
      for (let y = 0; y < height; y += 300) {
        await page.mouse.wheel(0, 300);
        await page.waitForTimeout(80);
      }
      await expect(async () => {
        expect(await stuckHidden(page)).toEqual([]);
      }).toPass({ timeout: 5_000 });
    });
  });
}

test("normal motion still starts the hero from its animated state", async ({ request }) => {
  // The entrance animation is unchanged: the server still ships the starting
  // frame, and the browser animates from it. Reduced motion changes how it
  // moves, never what the server sends.
  const html = await (await request.get("/")).text();

  expect(html).toContain("transform:translateY(108%)");
  expect(html).toMatch(/style="opacity:0;transform:translateY\(14px\)"/);
});
