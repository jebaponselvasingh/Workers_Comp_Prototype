import { PERSONAS, loginAs, switchPersona } from "../fixtures/login";
import {
  RESERVE_VERDICT_LABEL,
  claimIdsInStage,
  expectedReserveCheck,
  firstClaimInStage,
  formatCents,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 3.2 — Reserve Adequacy Check.
 *
 * The second slice of the financial engine, and this spec is about one thing
 * above all: **the verdict on the card was decided by the server**. Every
 * expectation comes from `fixtures/seed.ts`, which restates the rule — the
 * schedule projection, the two bands and the strict boundaries — as a second
 * implementation over the same seed file the stack migrated with. A spec that
 * read the verdict off the response would agree with any rule at all.
 *
 * **AC 3's "identical in both surfaces" is only half-assertable here today.**
 * The Bills financial summary is Story 3.3's surface; what this spec can
 * assert — and does — is the half that makes it true: one `reserveCheck` field
 * on the case file, outside the stage-variant block, which is what 3.3 will
 * render from. `server/tests/test_reserve_block.py` asserts the same property
 * against the payload.
 *
 * **What the oracle does and does not prove.** It is a second implementation,
 * so it catches an arithmetic slip or a flipped boundary; it shares the
 * server's assumption that an elapsed week on an approved claim counts as
 * disbursed, so it cannot be evidence for that assumption. See
 * `fixtures/seed.ts`. The consequence of the assumption is checked where it is
 * checkable — against what the card actually renders.
 *
 * **The medical half of the exposure is not on file until Story 3.3 seeds
 * `bill`, and the console says so rather than guessing.** `null` is not `0`:
 * an unknown non-negative term leaves `light` sound on a lower bound and makes
 * `adequate` and `heavy` claims about an upper bound that nobody can stand
 * behind, so those two are withheld as `indeterminate`. Every open claim below
 * is therefore one of two verdicts, and the specs assert both — that the
 * withholding happens, and that it has not become a blackout that swallows the
 * under-reserved warnings the story exists for.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

async function openClaim(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "case-header")).toBeVisible();
}

async function reserveCheckOf(page: Page, claimId: string): Promise<Record<string, unknown>> {
  const response = await page.request.get(`/api/claims/${claimId}`);
  expect(response.status()).toBe(200);
  return (await response.json()).reserveCheck;
}

test.describe("@story:3-2 @epic:3 reserve adequacy check", () => {
  test("@smoke the treatment card states the server's verdict and its rationale", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const expected = expectedReserveCheck(claimId);

    await openClaim(page, claimId);

    // --- AC 1 and 3: the verdict, computed server-side ------------------
    const chip = byTestId(page, "treatment-reserve-check");
    await expect(chip).toBeVisible();
    await expect(chip).toHaveText(RESERVE_VERDICT_LABEL[expected.verdict]);
    await expect(chip).toHaveAttribute("data-verdict", expected.verdict);
    // A treatment claim is never `closed_final`. Which of the rest it gets
    // depends on whether its bills are on file — today they are not, so it is
    // `light` (sound on a lower bound) or `indeterminate` (withheld).
    expect(["light", "indeterminate"]).toContain(expected.verdict);

    // --- AC 1: the rationale is the service's sentence -------------------
    const rationale = byTestId(page, "treatment-reserve-rationale");
    await expect(rationale).toBeVisible();
    await expect(rationale).toContainText("Reserve check:");
    // Both sides of the comparison, which is what makes the verdict
    // explainable rather than an instruction to trust it.
    await expect(rationale).toContainText("$");

    // --- AC 1: the card states one notion of "paid", not two -------------
    // The regression this assertion exists for: the indemnity row read
    // `claim.paid_indemnity` (0 on every open seeded claim) while the verdict
    // was computed from the schedule, so the card said "Indemnity paid $0.00"
    // above "no exposure remains, reallocate the surplus". Both figures now
    // come from the projection the verdict was reached from.
    await expect(byTestId(page, "treatment-indemnity-paid")).toHaveText(
      `${formatCents(expected.disbursedIndemnityCents)} of ` +
        `${formatCents(expected.scheduledIndemnityCents)}`,
    );

    // --- The payload behind it agrees with the oracle --------------------
    const check = await reserveCheckOf(page, claimId);
    expect(check.verdict).toBe(expected.verdict);
    expect(check.remainingIndemnityCents).toBe(expected.remainingIndemnityCents);
    expect(check.remainingMedicalCents).toBe(expected.remainingMedicalCents);
    expect(check.projectedRemainingCents).toBe(expected.projectedRemainingCents);
    expect(check.scheduledIndemnityCents).toBe(expected.scheduledIndemnityCents);
    expect(check.disbursedIndemnityCents).toBe(expected.disbursedIndemnityCents);
    expect(check.reserveCents).toBe(expected.reserveCents);
    // `null`, not 0: the bills are not on file, so there is no total and no
    // ratio behind the verdict — see `ReserveCheckResponse`.
    expect(check.remainingMedicalCents).toBeNull();
    expect(check.projectedRemainingCents).toBeNull();
  });

  test("no open claim is judged adequate or heavy while its bills are unseen", async ({
    page,
  }) => {
    // Honest degradation, in the browser and across the whole book. The
    // unknown medical term is non-negative, so an exposure computed without it
    // is a lower bound: `light` still holds, and `adequate` and `heavy` — both
    // claims about an upper bound — are withheld. Before this, 26 of 38 open
    // claims carried "Consider reallocating surplus" derived from half their
    // inputs, and the share grew every week as schedules elapsed.
    await loginAs(page, PERSONAS.handler);

    let withheld = 0;
    let underReserved = 0;
    for (const claimId of claimIdsInStage(KAYA.name, KAYA.role, "treatment")) {
      const check = await reserveCheckOf(page, claimId);
      expect(["light", "indeterminate"], claimId).toContain(check.verdict);
      expect(String(check.rationale)).not.toContain("reallocating surplus");
      if (check.verdict === "indeterminate") withheld += 1;
      if (check.verdict === "light") underReserved += 1;
    }

    // Both halves exercised: the withholding is real, and it is not a blackout
    // — the under-reserved warnings the story exists for still come through.
    expect(withheld).toBeGreaterThan(0);
    expect(underReserved).toBeGreaterThan(0);
  });

  test("a withheld verdict says what is missing, on the card (NFR-3)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = claimIdsInStage(KAYA.name, KAYA.role, "treatment").find(
      (id) => expectedReserveCheck(id).verdict === "indeterminate",
    );
    expect(claimId, "no seeded claim exercises the withheld path").toBeDefined();

    await openClaim(page, claimId as string);

    await expect(byTestId(page, "treatment-reserve-check")).toHaveText("Awaiting Bill Data");
    await expect(byTestId(page, "treatment-reserve-check")).toHaveAttribute(
      "data-verdict",
      "indeterminate",
    );
    await expect(byTestId(page, "treatment-reserve-rationale")).toContainText(
      "Medical bills are not yet on file",
    );
  });

  test("no treatment card claims nothing was paid and nothing remains (AC 1)", async ({
    page,
  }) => {
    // The contradiction, checked in the browser across the whole book rather
    // than reasoned about from the payload — because it was a *rendering*
    // fault, and the oracle shares the server's assumption about which weeks
    // count as disbursed (see `fixtures/seed.ts`). What is genuinely checkable
    // is that the two statements on screen do not contradict each other.
    await loginAs(page, PERSONAS.handler);

    for (const claimId of claimIdsInStage(KAYA.name, KAYA.role, "treatment")) {
      await openClaim(page, claimId);
      const paid = await byTestId(page, "treatment-indemnity-paid").textContent();
      const rationale = await byTestId(page, "treatment-reserve-rationale").textContent();

      const nothingPaid = paid?.startsWith("$0 of") ?? false;
      const nothingRemains = rationale?.includes("exposure ($0)") ?? false;
      expect(
        nothingPaid && nothingRemains,
        `${claimId}: card reads "${paid}" beside "${rationale}"`,
      ).toBe(false);
    }
  });

  test("every claim in the book is judged by the rule, not by the response (AC 1)", async ({
    page,
  }) => {
    // One claim can agree with an oracle by luck; a whole stage cannot. This
    // is where the schedule projection is really tested — the seeded portfolio
    // runs from January to September, so the same rule has to produce a fully
    // elapsed schedule, a partly paid one and one that has not started.
    await loginAs(page, PERSONAS.handler);

    for (const claimId of claimIdsInStage(KAYA.name, KAYA.role, "treatment")) {
      const expected = expectedReserveCheck(claimId);
      const check = await reserveCheckOf(page, claimId);

      expect(check.verdict, `${claimId} verdict`).toBe(expected.verdict);
      expect(check.remainingIndemnityCents, `${claimId} indemnity`).toBe(
        expected.remainingIndemnityCents,
      );
      expect(check.projectedRemainingCents, `${claimId} exposure`).toBe(
        expected.projectedRemainingCents,
      );
    }
  });

  test("an unapproved schedule owes all of itself (AC 1)", async ({ page }) => {
    // Stage before calendar: an intake claim has approved no payments, so its
    // whole projection is still exposure however old the injury is. The
    // prototype's first branch, and the one a date-driven projection would
    // silently get wrong on a claim from January.
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "intake");
    const expected = expectedReserveCheck(claimId);
    const check = await reserveCheckOf(page, claimId);

    expect(check.verdict).toBe(expected.verdict);
    expect(check.remainingIndemnityCents).toBe(expected.remainingIndemnityCents);
  });

  test("a settled claim is closed final, with no ratio and no chip (AC 1)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "settled");
    const check = await reserveCheckOf(page, claimId);

    expect(check.verdict).toBe("closed_final");
    // No band arithmetic was run, so there is no ratio — not a zero. A settled
    // claim is closed whether or not its bills are on file: the sentence names
    // no figures, so there is nothing in it an unknown term could make untrue.
    expect(check.ratioBp).toBeNull();
    expect(check.rationale).toBe("Claim settled and closed. No further reserve exposure.");

    // The settled overview has no reserve-check card; the *payload* still
    // carries the verdict, which is what Story 3.3's Bills tab reads.
    await openClaim(page, claimId);
    await expect(byTestId(page, "settled-banner")).toBeVisible();
    await expect(byTestId(page, "treatment-reserve-check")).toHaveCount(0);
  });

  test("the verdict is one field on the case file, not a copy per surface (AC 3)", async ({
    page,
  }) => {
    // The structural half of "renders identically in the Bills summary and the
    // Overview card from the same server value". Story 3.3 renders the Bills
    // side; what makes the two agree is that there is only one thing to read.
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const payload = await (await page.request.get(`/api/claims/${claimId}`)).json();

    expect(payload.reserveCheck).toBeDefined();
    expect(payload.overview.reserveCheck).toBeUndefined();
    expect(payload.overview.verdict).toBeUndefined();
    // The reserve the verdict was judged against is the reserve the card shows
    // two rows above it — two figures for one claim's reserve would be the
    // clearest sign the verdict came from somewhere else.
    expect(payload.reserveCheck.reserveCents).toBe(payload.overview.reserveCents);
  });

  test("a supervisor reads the same verdict on the same claim (AD-7)", async ({ page }) => {
    // Role gates capability, scope gates visibility. A supervisor asking
    // whether a book is under-reserved is who the verdict is most for.
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "treatment");
    const asHandler = await reserveCheckOf(page, claimId);

    await switchPersona(page);
    await loginAs(page, PERSONAS.fullPortfolioSupervisor);
    const asSupervisor = await reserveCheckOf(page, claimId);

    expect(asSupervisor).toEqual(asHandler);
  });

  test("the bands come from the rule document that shipped (AC 2)", async ({ page }) => {
    // The response names the document that decided, for the reason the
    // thresholds version rides on the queue: "which rules produced this?"
    // should be answerable from the payload rather than reconstructed. A band
    // retuned by a v2 that had not been seeded would show here as a version
    // that never moved.
    await loginAs(page, PERSONAS.handler);

    const check = await reserveCheckOf(
      page,
      firstClaimInStage(KAYA.name, KAYA.role, "treatment"),
    );

    expect(check.bandsVersion).toBe(1);
  });
});
