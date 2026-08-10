import { PERSONAS, loginAs, selectRole, switchPersona } from "../fixtures/login";
import { byRole, byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 1.3 — Role & Persona Login.
 * Drives the real login screen against the freshly reset stack: real
 * session cookie, real `app_user` seed, no mocked HTTP.
 */
test.describe("@story:1-3 @epic:1 role & persona login", () => {
  test("@smoke a supervisor signs in and lands on the dashboard shell", async ({ page }) => {
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);

    await expect(byRole(page, "region", /Portfolio dashboard/)).toBeVisible();
    await expect(byTestId(page, "top-bar")).toBeVisible();
  });

  test("the login screen boots on supervisor with its personas listed (AC 1)", async ({ page }) => {
    await page.goto("/");

    await expect(byRole(page, "heading", "LINEWORKER")).toBeVisible();
    await expect(page.getByText(/Dataset: WC_Manufacturing_Claims_2026\.xlsx/)).toBeVisible();
    await expect(byRole(page, "radio", /^Supervisor/)).toBeChecked();

    const picker = byRole(page, "combobox", "Log in as");
    await expect(picker).toBeEnabled();
    // The three seeded supervisors, nobody else.
    const options = await picker.locator("option").allTextContents();
    expect(options).toHaveLength(3);
    expect(options.every((label) => label.includes("WC Supervisor"))).toBe(true);
    expect(options[0]).toBe("David Bline — WC Supervisor (Full portfolio)");
  });

  test("clicking a role card repopulates the persona dropdown (AC 1)", async ({ page }) => {
    await page.goto("/");
    const picker = byRole(page, "combobox", "Log in as");
    await expect(picker).toBeEnabled();

    await selectRole(page, "handler");
    await expect
      .poll(async () => picker.locator("option").allTextContents())
      .toEqual([
        "Kaya Johnson — Handler (Caterpillar · GE · John Deere · Whirlpool)",
        "Dante Reyes — Handler (Boeing · Honeywell)",
        "Marcus Chen — Handler (GM · Toyota)",
        "Sarah Williams — Handler (3M)",
        "Liam O'Sullivan — Handler (John Deere)",
        "Fatima Al-Mansoori — Handler (Lockheed)",
      ]);

    await selectRole(page, "analyst");
    await expect
      .poll(async () => picker.locator("option").allTextContents())
      .toEqual(["David Bline — WC Supervisor (Analyst view)"]);
  });

  test("a handler lands on the workspace shell (AC 2)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await expect(byRole(page, "region", /Claim workspace/)).toBeVisible();
  });

  test("Switch ends the session server-side and returns to login (AC 3)", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);
    await expect(byRole(page, "region", /Portfolio dashboard/)).toBeVisible();

    await switchPersona(page);
    await expect(byRole(page, "button", /Enter Console/)).toBeVisible();

    // The session is dead on the server, not merely forgotten by the SPA:
    // the browser's own cookie jar can no longer buy a `/api/me`.
    const replay = await page.request.get("/api/me");
    expect(replay.status()).toBe(401);
  });

  test("the back button cannot walk back into a dead session (AC 3)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    await switchPersona(page);

    // Both transitions navigate with history.replace, so there is no
    // authenticated entry left to return to — going back is a no-op rather
    // than a flash of the previous persona's shell.
    await page.goBack();
    await expect(byRole(page, "region", /Claim workspace/)).toHaveCount(0);

    // History is a convenience, not the protection: re-entering the URL
    // directly hits the guard, which asks the server and gets a 401.
    await page.goto("/workspace");
    await expect(byRole(page, "button", /Enter Console/)).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/");
  });

  test("a direct URL to a protected shell redirects to login (AC 4)", async ({ page }) => {
    await page.goto("/dashboard");

    await expect(byRole(page, "button", /Enter Console/)).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/");
  });

  test("an unauthenticated API request is 401 problem+json (AC 4)", async ({ page }) => {
    const resp = await page.request.get("/api/me");

    expect(resp.status()).toBe(401);
    expect(resp.headers()["content-type"]).toContain("application/problem+json");
    expect(await resp.json()).toMatchObject({
      type: "/problems/unauthenticated",
      title: "Unauthenticated",
      status: 401,
    });
  });

  test("the analyst persona reaches the dashboard shell too (AC 2)", async ({ page }) => {
    await loginAs(page, PERSONAS.analyst);
    await expect(byRole(page, "region", /Portfolio dashboard/)).toBeVisible();
  });
});
