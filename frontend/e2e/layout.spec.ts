/**
 * Layout regression checks.
 *
 * Measures the rendered page rather than trusting the CSS, because the two
 * faults this file was written for were both invisible in source: an `auto`
 * margin quietly defeating the top bar's `space-between`, and a header action
 * aligned to the top of a heading *block* instead of the heading's baseline.
 *
 * Runs with or without a backend. Anything that needs live data asserts only
 * when that element actually rendered, so the suite stays honest instead of
 * passing because a component was absent.
 */

import { expect, test } from "@playwright/test";

/** Widths that matter: desktop, laptop, tablet, and the narrowest phone. */
const WIDTHS = [1440, 1280, 1024, 820, 640, 390];

test.describe("layout", () => {
  for (const width of WIDTHS) {
    test(`the page never scrolls sideways at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      await page.waitForTimeout(400);

      const { scrollW, clientW, offenders } = await page.evaluate(() => {
        const de = document.documentElement;
        const offenders: string[] = [];
        document.querySelectorAll("*").forEach((el) => {
          const r = el.getBoundingClientRect();
          if (r.width > 0 && r.right > de.clientWidth + 1) {
            const cls = (el.className || "").toString().split(" ")[0];
            offenders.push(`${el.tagName.toLowerCase()}.${cls}`);
          }
        });
        return { scrollW: de.scrollWidth, clientW: de.clientWidth, offenders };
      });

      expect(offenders, `elements past the right edge: ${offenders.join(", ")}`)
        .toEqual([]);
      expect(scrollW).toBeLessThanOrEqual(clientW + 1);
    });
  }

  test("top bar items share one vertical centre", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.waitForTimeout(600);

    const centres = await page.evaluate(() =>
      [".ai-pill", ".ticker", ".topbar__controls"]
        .map((sel) => {
          const el = document.querySelector(sel);
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return { sel, centre: r.top + r.height / 2 };
        })
        .filter(Boolean),
    );

    expect(centres.length).toBeGreaterThan(1);
    const first = centres[0]!.centre;
    for (const item of centres) {
      // Sub-pixel drift is fine; anything more reads as misaligned.
      expect(Math.abs(item!.centre - first), item!.sel).toBeLessThanOrEqual(1);
    }
  });

  test("the ticker is not stranded beside the status pill", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.waitForTimeout(800);

    const gaps = await page.evaluate(() => {
      const pill = document.querySelector(".ai-pill");
      const ticker = document.querySelector(".ticker");
      const controls = document.querySelector(".topbar__controls");
      if (!pill || !ticker || !controls) return null;
      return {
        before: ticker.getBoundingClientRect().left - pill.getBoundingClientRect().right,
        after: controls.getBoundingClientRect().left - ticker.getBoundingClientRect().right,
      };
    });

    // Only meaningful once a recovered figure exists to render.
    test.skip(gaps === null, "ticker needs a backend to render");
    // An `auto` margin previously left ~32px on one side and ~320px on the
    // other. Neither gap should dwarf the other.
    const ratio = Math.max(gaps!.before, gaps!.after) / Math.max(1, Math.min(gaps!.before, gaps!.after));
    expect(ratio, `gaps ${JSON.stringify(gaps)}`).toBeLessThan(3);
  });

  test("a header action sits on the heading's baseline", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.waitForTimeout(600);

    const m = await page.evaluate(() => {
      const h1 = document.querySelector(".view__header h1");
      const btn = document.querySelector('[data-testid="open-guided-demo"]');
      if (!h1 || !btn) return null;
      const a = h1.getBoundingClientRect();
      const b = btn.getBoundingClientRect();
      return { h1Mid: a.top + a.height / 2, btnMid: b.top + b.height / 2 };
    });

    expect(m).not.toBeNull();
    // Baseline alignment leaves the boxes slightly offset by design; a large
    // gap means it reverted to aligning against the whole heading block.
    expect(Math.abs(m!.btnMid - m!.h1Mid)).toBeLessThan(8);
  });

  test("KPI tiles form one even row", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.waitForTimeout(600);

    const tiles = await page.evaluate(() => {
      const row = document.querySelector(".kpi-row");
      if (!row) return null;
      return Array.from(row.children).map((c) => {
        const r = c.getBoundingClientRect();
        return { top: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) };
      });
    });

    expect(tiles).not.toBeNull();
    const [first, ...rest] = tiles!;
    for (const tile of rest) {
      expect(tile.top).toBe(first.top);
      expect(Math.abs(tile.w - first.w)).toBeLessThanOrEqual(1);
      expect(Math.abs(tile.h - first.h)).toBeLessThanOrEqual(1);
    }
  });
});
