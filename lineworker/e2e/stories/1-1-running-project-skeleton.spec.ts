import { byRole } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 1.1 — Running Project Skeleton.
 * Structural assertions: the SPA is served by nginx and the API answers
 * through the same ingress.
 *
 * Amended by Story 1.3 (AD-15: specs are amended, never deleted). The
 * sample claim card this spec asserted on is gone — `/` is now the login
 * screen. What 1.1 actually owns is the *stack*: nginx serving the built
 * SPA with its design tokens and self-hosted fonts applied, and the API
 * answering through the same ingress. The second test now pins that,
 * rather than re-asserting login behaviour that 1-3's spec already covers.
 */
test.describe("@story:1-1 @epic:1 running project skeleton", () => {
  test("@smoke SPA shell loads through nginx and /api/healthz answers healthy through the proxy", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(byRole(page, "heading", "LINEWORKER")).toBeVisible();

    const resp = await page.request.get("/api/healthz");
    expect(resp.status()).toBe(200);
    expect(await resp.json()).toEqual({ status: "ok", db: "ok" });
  });

  test("the built SPA is served with its design tokens and fonts applied", async ({ page }) => {
    await page.goto("/");

    // The Story 1.1 palette and self-hosted type actually reached the
    // browser — i.e. Tailwind's build ran and the CSS bundle was served by
    // nginx, not just that some HTML came back.
    const tokens = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      return {
        brand: root.getPropertyValue("--color-brand").trim(),
        surface: root.getPropertyValue("--color-surface").trim(),
        bodyFont: getComputedStyle(document.body).fontFamily,
      };
    });
    expect(tokens.brand).toBe("#e8560a");
    // The CSS minifier shortens #ffffff, so match the colour rather than
    // the spelling.
    expect(tokens.surface).toMatch(/^#(fff|ffffff)$/i);
    expect(tokens.bodyFont).toContain("Inter");

    // Fonts are bundled, never fetched from a CDN (AD-5's no-external-calls
    // posture starts here).
    const fontRequests: string[] = [];
    page.on("request", (request) => {
      if (/fonts\.(googleapis|gstatic)\.com/.test(request.url())) fontRequests.push(request.url());
    });
    await page.reload();
    expect(fontRequests).toEqual([]);
  });
});
