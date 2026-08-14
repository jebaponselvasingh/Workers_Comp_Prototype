/**
 * The reserve adequacy verdict, as the treatment card renders it (Story 3.2).
 *
 * Every assertion here is "the card shows what the server sent". There is no
 * expectation computed from another field, because the whole acceptance
 * criterion is that the browser does not judge a reserve: it maps a token to a
 * label and a colour (the enum convention's UI half) and prints the server's
 * sentence.
 *
 * **The verdict is read from `claim.reserveCheck`, not from the overview
 * block**, which is what AC 3 turns on: Story 3.3's Bills financial summary
 * renders this same field out of the same cached case file, so a component
 * reading a copy on the stage variant would be the drift the story exists to
 * prevent. `test_reserve_block.py` asserts the payload shape that makes it
 * true; this asserts the component honours it.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import type { ClaimDetail } from "@/api/claims";
import { createQueryClient } from "@/api/queryClient";
import {
  CLAIM_DETAIL_INTAKE,
  CLAIM_DETAIL_INVESTIGATION,
  CLAIM_DETAIL_SETTLED,
  CLAIM_DETAIL_TREATMENT,
  ME_HANDLER,
  RESERVE_CHECK_CLOSED,
  RESERVE_CHECK_HEAVY,
  RESERVE_CHECK_INDETERMINATE,
  RESERVE_CHECK_LIGHT,
  RESERVE_CHECK_LIGHT_ON_INDEMNITY,
  type StubRouteFor,
  stubApi,
} from "@/test/api-mock";

import { ClaimDetailPane } from "./ClaimDetailPane";

function renderPane(claimDetail: StubRouteFor, claim = "WC-20017") {
  stubApi({ me: ME_HANDLER, claimDetail });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[`/workspace?claim=${claim}`]}>
        <ClaimDetailPane />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The treatment case file with a different verdict on it. */
function withVerdict(reserveCheck: ClaimDetail["reserveCheck"]): StubRouteFor {
  return {
    status: 200,
    body: { ...CLAIM_DETAIL_TREATMENT.body, reserveCheck },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- the four verdicts ----------------------------------------------------

test("an adequate reserve is labelled and toned ok", async () => {
  renderPane(CLAIM_DETAIL_TREATMENT);

  const chip = await screen.findByTestId("treatment-reserve-check");
  expect(chip).toHaveTextContent("Reserve Adequate");
  expect(chip).toHaveAttribute("data-verdict", "adequate");
  expect(chip.className).toContain("text-ok");
});

test("a light reserve is the error tone, because it is money that is not there", async () => {
  // The pairing that reads backwards until you say it out loud: *light* is the
  // error (exposure the carrier has not reserved for), *heavy* is the warning
  // (capital tied up). The prototype makes the same call.
  renderPane(withVerdict(RESERVE_CHECK_LIGHT));

  const chip = await screen.findByTestId("treatment-reserve-check");
  expect(chip).toHaveTextContent("Reserve Light");
  expect(chip).toHaveAttribute("data-verdict", "light");
  expect(chip.className).toContain("text-error");
});

test("a heavy reserve is the warn tone", async () => {
  renderPane(withVerdict(RESERVE_CHECK_HEAVY));

  const chip = await screen.findByTestId("treatment-reserve-check");
  expect(chip).toHaveTextContent("Reserve Heavy");
  expect(chip.className).toContain("text-warn");
});

test("a settled claim's verdict is muted rather than passed", async () => {
  // `closed_final` is a statement, not a status to act on. Colouring it green
  // would read as a pass mark on a comparison nobody made.
  renderPane(withVerdict(RESERVE_CHECK_CLOSED));

  const chip = await screen.findByTestId("treatment-reserve-check");
  expect(chip).toHaveTextContent("Closed — Final");
  expect(chip.className).toContain("text-faint");
});

test("a claim whose bills are not on file is withheld rather than judged", async () => {
  // Honest degradation (Story 3.3's seam). The unknown medical term can move a
  // claim up a band, so `adequate` and `heavy` are not answers the console is
  // entitled to give — and `heavy`'s rationale in particular would be telling a
  // handler to reallocate money away from a claim whose costs nobody has
  // totalled.
  renderPane(withVerdict(RESERVE_CHECK_INDETERMINATE));

  const chip = await screen.findByTestId("treatment-reserve-check");
  expect(chip).toHaveTextContent("Awaiting Bill Data");
  expect(chip).toHaveAttribute("data-verdict", "indeterminate");
  // Muted: any of the three status colours would imply a conclusion.
  expect(chip.className).toContain("text-muted-text");
  expect(screen.getByTestId("treatment-reserve-rationale")).toHaveTextContent(
    "Medical bills are not yet on file",
  );
  // And no advice, because there is nothing to advise.
  expect(screen.getByTestId("treatment-financials").textContent).not.toContain(
    "reallocating surplus",
  );
});

test("under-reserved on indemnity alone still reports, and says the figure is a floor", async () => {
  // The verdict that survives an incomplete exposure, and the reason the
  // withholding is not blanket: an unknown non-negative addition cannot bring
  // an exposure back under a threshold it has already passed, so this claim is
  // under-reserved whatever the bills say. Suppressing it would lose exactly
  // the signal the story exists for.
  renderPane(withVerdict(RESERVE_CHECK_LIGHT_ON_INDEMNITY));

  expect(await screen.findByTestId("treatment-reserve-check")).toHaveTextContent(
    "Reserve Light",
  );
  expect(screen.getByTestId("treatment-reserve-rationale")).toHaveTextContent(
    "before medical bills are counted",
  );
});

// --- the rationale --------------------------------------------------------

test("the rationale is the server's sentence, rendered verbatim", async () => {
  renderPane(withVerdict(RESERVE_CHECK_LIGHT));

  expect(await screen.findByTestId("treatment-reserve-rationale")).toHaveTextContent(
    "Reserve check: Projected remaining exposure ($63,000) exceeds current reserve " +
      "($45,000). Recommend re-evaluating reserve upward.",
  );
});

test("a settled claim's rationale names no figures", async () => {
  renderPane(withVerdict(RESERVE_CHECK_CLOSED));

  const rationale = await screen.findByTestId("treatment-reserve-rationale");
  expect(rationale).toHaveTextContent("Claim settled and closed. No further reserve exposure.");
  expect(rationale.textContent).not.toContain("$");
});

// --- AD-1: nothing here is computed --------------------------------------

test("the card does not recompute the verdict from the figures it was sent", async () => {
  // A payload whose verdict contradicts its own arithmetic: the exposure is
  // 140% of the reserve, which is light under the seeded bands — and the
  // server says `heavy`. The card must say heavy. This is the one test that
  // can tell "rendered the server's answer" from "happened to agree with it",
  // and it is deliberately an impossible payload, because a *possible* one
  // cannot distinguish the two.
  renderPane(
    withVerdict({
      ...RESERVE_CHECK_HEAVY,
      projectedRemainingCents: 6_300_000,
      remainingIndemnityCents: 5_114_130,
      reserveCents: 4_500_000,
    }),
  );

  expect(await screen.findByTestId("treatment-reserve-check")).toHaveTextContent("Reserve Heavy");
});

test("the ratio is not rendered, so a null one cannot become NaN", async () => {
  // The settled payload carries `ratioBp: null`. Nothing on the card reads it
  // — the rationale is what explains the verdict — so this is a guard against
  // a later change putting "NaN%" beside a closed claim.
  renderPane(withVerdict(RESERVE_CHECK_CLOSED));

  await screen.findByTestId("treatment-reserve-check");
  expect(screen.getByTestId("treatment-financials").textContent).not.toContain("NaN");
  expect(screen.getByTestId("treatment-financials").textContent).not.toContain("%");
});

// --- the card states one notion of "paid", not two -----------------------

test("the indemnity-paid row is the schedule's figures, not the claim's column", async () => {
  // The regression this pair of fields exists for (code review, 2026-08-14).
  // The fixture makes the two sources disagree the way the seeded database
  // does — the column says nothing has been paid, the projection says
  // $11,358.70 of $40,000 has — so a card that read `paidIndemnityCents` shows
  // "$0" and one that reads the block shows "$11,359 of $40,000". Only one of
  // those can sit above a verdict computed from the projection without the
  // card contradicting itself.
  renderPane({
    status: 200,
    body: {
      ...CLAIM_DETAIL_TREATMENT.body,
      overview: { ...CLAIM_DETAIL_TREATMENT.body.overview, paidIndemnityCents: 0 },
    },
  });

  const row = await screen.findByTestId("treatment-indemnity-paid");
  expect(row).toHaveTextContent("$11,359 of $40,000");
  expect(row).not.toHaveTextContent("$0");
});

test("a fully disbursed schedule reads as paid, beside a verdict saying nothing remains", async () => {
  // The exact card the review found: 19 of Kaya's 28 treatment claims had a
  // fully elapsed schedule, so the verdict said "$0 exposure — reallocate the
  // surplus" while the row above it said "Indemnity paid $0.00". The two now
  // agree: everything scheduled has been disbursed, which is *why* nothing
  // remains.
  renderPane(
    withVerdict({
      ...RESERVE_CHECK_HEAVY,
      projectedRemainingCents: 0,
      remainingIndemnityCents: 0,
      remainingMedicalCents: 0,
      scheduledIndemnityCents: 4_000_000,
      disbursedIndemnityCents: 4_000_000,
      disbursedMedicalCents: 0,
      ratioBp: 0,
      rationale:
        "Current reserve ($45,000) comfortably exceeds projected remaining exposure ($0). " +
        "Consider reallocating surplus.",
    }),
  );

  expect(await screen.findByTestId("treatment-indemnity-paid")).toHaveTextContent(
    "$40,000 of $40,000",
  );
  expect(screen.getByTestId("treatment-reserve-check")).toHaveTextContent("Reserve Heavy");
});

// --- where the card renders ----------------------------------------------

test("the verdict renders on the treatment variant and on no other", async () => {
  // The prototype puts the reserve-check row on the treatment overview; Story
  // 3.3 adds the Bills-tab twin. The *payload* carries a verdict at every
  // stage, which is what lets that twin read it — an intake claim's Overview
  // simply has no card for it.
  const { unmount } = renderPane(CLAIM_DETAIL_TREATMENT);
  expect(await screen.findByTestId("treatment-reserve-check")).toBeInTheDocument();
  unmount();

  for (const [detail, claim, marker] of [
    [CLAIM_DETAIL_INTAKE, "WC-20003", "intake-checklist"],
    [CLAIM_DETAIL_INVESTIGATION, "WC-20051", "investigation-financials"],
    [CLAIM_DETAIL_SETTLED, "WC-20068", "settled-banner"],
  ] as const) {
    const view = renderPane(detail, claim);
    expect(await screen.findByTestId(marker)).toBeInTheDocument();
    expect(screen.queryByTestId("treatment-reserve-check")).not.toBeInTheDocument();
    view.unmount();
  }
});
