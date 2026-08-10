import { psqlQuery } from "../fixtures/reset";
import { expect, test } from "../fixtures/test";

/**
 * Story 1.2 — Persisted Claim Portfolio (Schema & Seed).
 * No UI surface in this story: assertions are stack-level by design —
 * the reset's migrate+seed ran, the api (app role) is healthy through
 * nginx, and the seed contents are verified via the e2e owner connection.
 */
test.describe("@story:1-2 @epic:1 persisted claim portfolio", () => {
  test("@smoke freshly reset stack is healthy and fully seeded", async ({ page }) => {
    const resp = await page.request.get("/api/healthz");
    expect(resp.status()).toBe(200);
    expect(await resp.json()).toEqual({ status: "ok", db: "ok" });

    const [row] = psqlQuery(
      "SELECT (SELECT count(*) FROM claim) || '|' || (SELECT count(*) FROM employer) || '|' || " +
        "(SELECT count(*) FROM employee) || '|' || (SELECT count(*) FROM app_user)",
    );
    expect(row).toBe("100|10|100|10");
  });

  test("seed encodes the AD-7 scope model", () => {
    const parkEmployers = psqlQuery(
      "SELECT e.name FROM app_user u " +
        "JOIN user_employer_assignment a ON a.user_id = u.id " +
        "JOIN employer e ON e.id = a.employer_id " +
        "WHERE u.name = 'Jennifer Park' AND u.role = 'supervisor' ORDER BY e.name",
    );
    expect(parkEmployers).toEqual([
      "3M Company",
      "General Motors",
      "Toyota Motor Manufacturing",
    ]);

    const scopeAllWithRows = psqlQuery(
      "SELECT count(*) FROM app_user u " +
        "JOIN user_employer_assignment a ON a.user_id = u.id WHERE u.scope_all",
    );
    expect(scopeAllWithRows).toEqual(["0"]);
  });

  test("claim business ids are unique WC-nnnn", () => {
    const [dupes] = psqlQuery(
      "SELECT count(*) FROM (SELECT claim_id FROM claim GROUP BY claim_id HAVING count(*) > 1) d",
    );
    expect(dupes).toBe("0");
    const [wcShaped] = psqlQuery("SELECT count(*) FROM claim WHERE claim_id ~ '^WC-[0-9]+$'");
    expect(wcShaped).toBe("100");
  });
});
