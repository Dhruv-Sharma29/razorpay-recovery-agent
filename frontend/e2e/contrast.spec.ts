/**
 * WCAG contrast regression check.
 *
 * Why this exists: two shipped bugs were CSS *resolution* failures that
 * no DOM assertion could catch — a stat tile whose background rule lost
 * to a later same-specificity rule (leaving near-white text on white,
 * 1.08:1), and rail text pinned to a fixed brand color that vanished on
 * the dark surface. Both are only visible once the cascade is resolved
 * in a real browser, which is what this does.
 */

import { expect, test } from "@playwright/test";

/** WCAG AA: 4.5:1 normal text, 3:1 for large (>=18.66px bold or >=24px). */
const AA_NORMAL = 4.5;
const AA_LARGE = 3;

const SELECTORS = [
  ".stat-tile__label",
  ".stat-tile__value",
  ".stat-tile__sub",
  ".sidebar__link-label",
  ".sidebar__link-hint",
  ".sidebar__name",
  ".sidebar__tagline",
  ".sidebar__footer",
  ".ai-pill",
  ".view__header h1",
  ".view__header p",
  ".funnel__label",
  ".funnel__value",
  ".funnel__caption",
  ".funnel__definitions dt",
  ".funnel__definitions dd",
  ".scenarios__name",
  ".scenarios__amount",
  ".scenarios__note",
  ".outcome-chip",
  ".banner",
  ".chain__label",
  ".chain__value",
  ".cases__controls span",
  ".batch-runner__toast",
  ".source-badge",
  ".rail-node__label",
  ".rail-node__value",
  ".rail-node__reason",
  ".card-title",
  ".form-field label",
  ".form-hint",
  ".status-badge",
  ".btn",
  ".audit-table th",
  ".audit-table td",
  ".dashboard-header h1",
  ".dashboard-header p",
  ".error-state__title",
  ".error-state__description",
  ".empty-state__title",
  ".empty-state__description",
  ".app-footer",
];

type Sample = {
  selector: string;
  text: string;
  ratio: number;
  required: number;
};

/**
 * Resolves each element's effective background by walking ancestors past
 * transparent fills, then returns its contrast ratio.
 */
async function sample(page: import("@playwright/test").Page, selectors: string[]) {
  return page.evaluate((sels) => {
    const toRgb = (c: string) => {
      const m = c.match(/[\d.]+/g);
      return m ? m.slice(0, 3).map(Number) : null;
    };
    const alpha = (c: string) => {
      const m = c.match(/[\d.]+/g);
      return m && m.length > 3 ? Number(m[3]) : 1;
    };
    const lum = (rgb: number[]) => {
      const s = rgb.map((v) => {
        const x = v / 255;
        return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * s[0] + 0.7152 * s[1] + 0.0722 * s[2];
    };
    const bgOf = (el: Element): number[] => {
      let node: Element | null = el;
      while (node) {
        const bg = getComputedStyle(node).backgroundColor;
        if (bg && alpha(bg) > 0.95) {
          const rgb = toRgb(bg);
          if (rgb) return rgb;
        }
        node = node.parentElement;
      }
      return [255, 255, 255];
    };

    const out: Sample[] = [];
    for (const sel of sels) {
      for (const el of Array.from(document.querySelectorAll(sel))) {
        const text = (el.textContent ?? "").trim();
        if (!text) continue;
        const cs = getComputedStyle(el);
        if (cs.visibility === "hidden" || cs.display === "none") continue;
        if (!(el as HTMLElement).offsetParent && cs.position !== "fixed") continue;

        const fg = toRgb(cs.color);
        if (!fg) continue;
        const a = lum(fg);
        const b = lum(bgOf(el));
        const [hi, lo] = a > b ? [a, b] : [b, a];
        const ratio = (hi + 0.05) / (lo + 0.05);

        const px = parseFloat(cs.fontSize);
        const bold = Number(cs.fontWeight) >= 700;
        const large = px >= 24 || (bold && px >= 18.66);

        out.push({
          selector: sel,
          text: text.slice(0, 40),
          ratio: Number(ratio.toFixed(2)),
          required: large ? 3 : 4.5,
        });
      }
    }
    return out;
  }, selectors);
}

for (const theme of ["light", "dark"] as const) {
  test(`all visible text meets WCAG AA in ${theme} theme`, async ({ page }) => {
    await page.goto("/");
    await page.evaluate((t) => localStorage.setItem("theme", t), theme);
    await page.reload({ waitUntil: "networkidle" });

    // Populate the KPIs, funnel and case table so their text is covered
    // too. Falls back to the error banner when no backend is running,
    // which is itself worth checking.
    await page.getByTestId("run-batch-btn").click();
    await page.waitForTimeout(2500);
    await page.getByTestId("nav-cases").click();
    await page.waitForTimeout(800);

    const samples = await sample(page, SELECTORS);
    expect(samples.length, "found no text to check").toBeGreaterThan(10);

    const failures = samples.filter((s) => s.ratio < s.required);
    expect(
      failures,
      `Low-contrast text in ${theme} theme:\n` +
        failures
          .map(
            (f) =>
              `  ${f.selector} "${f.text}" → ${f.ratio}:1 (needs ${f.required}:1)`,
          )
          .join("\n"),
    ).toEqual([]);
  });
}

test("theme is applied before first paint (no flash)", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => localStorage.setItem("theme", "dark"));

  // Capture the theme attribute as the document is first parsed, before
  // React has mounted — this is what a returning dark-mode user sees.
  await page.goto("/", { waitUntil: "commit" });
  const atParse = await page.evaluate(
    () => document.documentElement.dataset.theme,
  );
  expect(atParse).toBe("dark");
});

test.describe("KPI tiles", () => {
  test("render as one uniform set", async ({ page }) => {
    await page.goto("/", { waitUntil: "networkidle" });
    await page.waitForSelector('[data-testid="kpi-tiles"]');

    const boxes = await page.evaluate(() =>
      Array.from(document.querySelectorAll(".kpi-row .stat-tile")).map((el) => {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        return [
          Math.round(r.width),
          Math.round(r.height),
          cs.padding,
          cs.borderRadius,
          cs.borderTopWidth,
        ].join("|");
      }),
    );

    expect(boxes).toHaveLength(4);
    expect(new Set(boxes).size, `tiles differ: ${[...new Set(boxes)]}`).toBe(1);
  });
});
