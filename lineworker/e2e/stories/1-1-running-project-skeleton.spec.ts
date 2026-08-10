import { byRole } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 1.1 — Running Project Skeleton.
 * Structural assertions only: there is no login yet (Story 1.3).
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

  test("shell renders the dense-card layout frame", async ({ page }) => {
    await page.goto("/");
    await expect(byRole(page, "region", /sample claim card/i)).toBeVisible();
  });
});
