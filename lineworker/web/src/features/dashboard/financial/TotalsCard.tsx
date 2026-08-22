/**
 * The three portfolio money figures, as KPI tiles (Story 7.4, AC 1).
 *
 * `KpiCard` with no variant and no wrapper of its own, which is the point rather
 * than an economy: the portfolio dashboard's Total Paid and Total Reserve cards
 * are the idiom an analyst already reads, and these are the same drawing over a
 * *segmented* book plus the third figure that dashboard has never shown. The
 * sums, the ranking behind the card below, and every cent arrived decided from
 * `services/worklist/decomposition.py` (AD-1); this file supplies three labels,
 * three captions, three tones and three drill targets.
 *
 * **The three captions are the whole reason this file is not three lines.** The
 * money words in this domain do not mean what they look like, and every one of
 * the three has a footnote somewhere in the tree that a card is the last chance
 * to state:
 *
 * - **Paid** is the registered `total_paid` derivation over the static `paid_*`
 *   columns — the same figure the Total Paid KPI card shows and the employer
 *   bars sum — and it excludes bills and expenses paid on open claims.
 *   `deferred-work.md` carries the finding, its magnitude and its owner; the
 *   caption says what the figure sums so a reader who drills into a treatment
 *   claim and finds a five-figure Paid to Date is not looking at a
 *   contradiction nobody warned them about.
 * - **Reserve** is the column, and it is money *not yet spent*. It is the one
 *   of the three a reader is most likely to add to the first, which is exactly
 *   why the third tile exists.
 * - **Projected** is `total_claim_projected` — paid plus reserve — and it is
 *   deliberately not labelled "incurred". The epic's word is "incurred", the
 *   case file already uses "Total incurred" for the paid-only figure, and
 *   `services/derivations/claim_money.py` records the discrepancy that this
 *   console refuses to spread. The caption says what it sums instead.
 *
 * **All three tiles drill, and two of them drill to the same list.** Paid,
 * reserve and projected are three sums over *one* population — the segmented
 * book — so all three open the active segmentation with no slice added, which is
 * `KpiCard.drill`'s recorded case for a whole filter set rather than a single
 * facet (Total Claims, Total Paid and Total Reserve on the portfolio dashboard
 * do the same). A tile that invented a facet to look more specific would open a
 * list that did not reconcile with the figure on it.
 *
 * **Nothing here computes anything.** `formatCents` is the only division by a
 * hundred in this console and it is `lib/money.ts`'s; there is no `paid +
 * reserve` and no share of a total.
 */
import type { FinancialDecomposition } from "@/api/dashboard";
import { formatCents } from "@/lib/money";

import { KpiCard, KpiCardSkeleton } from "../KpiCard";
import { type DrillFilters } from "../drill/filters";

/** How many tiles the row holds open while the figures are unknown. */
const TILE_COUNT = 3;

export function TotalsCard({
  data,
  segmentation,
  segmented,
  isError,
}: {
  /** The server's decomposition, or `undefined` while it is unknown. */
  data: FinancialDecomposition | undefined;
  /**
   * The workspace's active filter — the drill target for all three tiles.
   *
   * Passed in rather than read here, `DashboardPage`'s composition rule: the
   * page owns the URL and the sections receive what it decided, so a tile and
   * the caption above it cannot come to describe two different filters.
   */
  segmentation: DrillFilters;
  /** Whether the figures describe an intersection rather than the whole book. */
  segmented: boolean;
  isError: boolean;
}) {
  if (isError) {
    // **Nothing at all**, not a skeleton and not three zeroed tiles —
    // `FraudPage`'s ruling: a pulsing placeholder under the section's own
    // `role="alert"` would be two states saying opposite things, and three
    // zeroes would be a *figure*, which is worse than an absence on a card
    // whose whole subject is what a book costs.
    return null;
  }
  if (data === undefined) {
    return (
      <>
        {Array.from({ length: TILE_COUNT }, (_, index) => (
          <KpiCardSkeleton key={index} />
        ))}
      </>
    );
  }

  // The population the three figures are over, named rather than assumed. With
  // a segmentation applied it is the intersection, and a caption reading "in
  // this portfolio" over a nine-dimension subset is the kind of sentence an
  // analyst quotes at somebody — the Story 7.3 finding, on a money surface where
  // it would be quoted in dollars.
  const over = segmented ? `${String(data.claimsInScope)} matching claims` : "the whole portfolio";

  return (
    <>
      <KpiCard
        testId="financial-kpi-paid"
        value={formatCents(data.totals.paidCents)}
        // **"Total Paid", the portfolio dashboard's own label for this exact
        // derivation** — and deliberately not "Paid to date", which is the case
        // file's label for `paidToDateCents`: a *different* figure that also
        // counts paid bills and expenses and reads five figures on an open claim
        // where this one reads zero. Two surfaces one drill click apart may not
        // spell two quantities the same way; that is the ruling this story
        // applied to "incurred" and it applies here for the same reason.
        label="Total Paid"
        // What it sums, on the card, because the word does not say it — see the
        // module docstring and `deferred-work.md`.
        caption={`Indemnity + medical + expense columns, ${over}`}
        tone="steel"
        drill={segmentation}
      />
      <KpiCard
        testId="financial-kpi-reserve"
        value={formatCents(data.totals.reserveCents)}
        label="Reserve held"
        caption={`Money set aside and not yet spent, ${over}`}
        tone="warn"
        drill={segmentation}
      />
      <KpiCard
        testId="financial-kpi-projected"
        value={formatCents(data.totals.projectedCents)}
        label="Total (projected)"
        // "Paid plus reserve" rather than "incurred" — and the caption names
        // *which* paid, because the case file's "Total Claim (Projected)" is the
        // same registered derivation over the other basis: on a treatment claim
        // with unbilled disbursements the two figures differ, and a caption
        // reading only "paid plus reserve" is true of both and warns nobody.
        caption={`Paid columns plus reserve, ${over}`}
        tone="brand"
        drill={segmentation}
      />
    </>
  );
}
