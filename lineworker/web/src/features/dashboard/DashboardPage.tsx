/**
 * The portfolio dashboard's header and ten KPI cards (UX-DR7, FR-SUP-1/A).
 *
 * The prototype's `renderSV` (line 1001) builds this by folding the global
 * claim array in the browser — ten `filter`/`reduce` passes over data it had
 * already filtered client-side to whatever persona was selected. Here every
 * number arrives from `GET /api/dashboard/summary`, computed by
 * `services/worklist` behind the caller's employer scope (AD-1, AD-7), and
 * this file does exactly two things to it: format cents as dollars, and choose
 * a colour token. `noDerivation.test.ts` walks `features/dashboard` and fails
 * the build over anything else.
 *
 * **The two captions that quote a number quote the server's.** "Severity ≥
 * N/100" and "Score ≥ N — review needed" interpolate `highRiskSeverityMin` and
 * `fraudScoreMin` off the response, so superseding the rule document moves the
 * count and the caption together. A constant here would be a second copy of a
 * rule the browser cannot see change — and the e2e spec would have no way to
 * tell a caption that follows the document from one that happens to agree with
 * it today.
 *
 * **What this page does not render.** Caseload, Active Tx, High Risk and the
 * SLA strip are already in the shared top bar (`TopBar`, `SlaStrip`) for every
 * role; repeating them here would be two components showing one number, which
 * is the disagreement Epic 5 is most exposed to. The charts (5.3), the top-30
 * worklist (5.4) and drill-through (5.5) are the rest of the epic and are
 * deliberately absent.
 *
 * **Story 5.2's table is a second query, deliberately.** Two server answers with
 * two costs, so they get two cache entries, two loading states and two failure
 * modes — a benchmark request that 500s leaves the ten KPI cards standing rather
 * than blanking the page, which is the whole reason the sections are not folded
 * into one endpoint. That makes `aria-busy` a decision rather than an accident:
 * see the attribute below.
 */
import {
  useDashboardCharts,
  useDashboardSummary,
  useHandlerBenchmarks,
  type PortfolioSummary,
} from "@/api/dashboard";
import { formatCents } from "@/lib/money";

import { PortfolioCharts } from "./charts/PortfolioCharts";
import { HandlerBenchmarkTable } from "./HandlerBenchmarkTable";
import { KpiCard, KpiCardSkeleton, type KpiTone } from "./KpiCard";

/** The response's money fields, by the `*Cents` suffix the contract guarantees. */
type MoneyField = Extract<keyof PortfolioSummary, `${string}Cents`>;
/** Everything else: counts, chip totals and the two thresholds. */
type CountField = Exclude<keyof PortfolioSummary, MoneyField>;

interface CardSpecBase {
  /** `data-testid` stem — kebab-case, matching the card's label. */
  testId: string;
  label: string;
  caption: (summary: PortfolioSummary) => string;
  tone: KpiTone;
}

/**
 * A card's declaration: which field, and whether it is money.
 *
 * A union rather than `field: keyof PortfolioSummary` beside an independent
 * `money?: boolean`, because those two are not independent — that shape
 * type-checks `{ field: "totalClaims", money: true }`, which renders a
 * hundred-claim portfolio as "$1" and would ship, since neither figure is
 * implausible on its own and no test asserts the absence of a dollar sign.
 * Pairing them here makes the wrong state unrepresentable rather than merely
 * unlikely, the same habit as `KpiTone`'s exhaustive `TONE_CLASS` map. It
 * matters more than it looks: 5.2 and 5.3 extend this table, and a money card
 * is one copy-pasted line away from a count card.
 */
type CardSpec =
  | (CardSpecBase & {
      /** The response field this card shows, formatted with `String`. */
      field: CountField;
      money?: false;
    })
  | (CardSpecBase & {
      /** Cents on the wire, dollars on screen — `lib/money.ts` does the one division. */
      field: MoneyField;
      money: true;
    });

/**
 * Row 1, in the prototype's order with its labels, captions and tones
 * (`renderSV`, line 1060). The order is the design contract and is declared
 * once here rather than laid out in JSX, so "the cards render in this order"
 * is a list a reviewer can check against the prototype line by line.
 */
const ROW_ONE: readonly CardSpec[] = [
  {
    testId: "kpi-total-claims",
    field: "totalClaims",
    label: "Total Claims",
    caption: () => "Manufacturing portfolio",
    tone: "steel",
  },
  {
    testId: "kpi-under-treatment",
    field: "underTreatment",
    label: "Under Treatment",
    caption: () => "Active — awaiting RTW",
    tone: "warn",
  },
  {
    testId: "kpi-settled-closed",
    field: "settledClosed",
    label: "Settled & Closed",
    caption: () => "Fully resolved",
    tone: "ok",
  },
  {
    testId: "kpi-high-risk",
    field: "highRisk",
    label: "High Risk",
    // The band's boundary, from the document that decided the count.
    caption: (summary) => `Severity ≥ ${summary.highRiskSeverityMin}/100`,
    tone: "error",
  },
  {
    testId: "kpi-total-paid",
    field: "totalPaidCents",
    label: "Total Paid",
    // **A caption that undercounts its own figure by one column, ported
    // deliberately.** `total_paid` sums indemnity, medical *and* expense — the
    // expense column is $65,761 of the seeded portfolio's $1,670,497 — while
    // this wording, which is the prototype's, names only the first two. Same
    // shape as the "Total incurred" note in `claim_money.py`: the figure is the
    // design contract and is ported exactly, the label is the UI's, and
    // rewording it is a product decision rather than a port. Stated once here
    // so the next reader does not "fix" the arithmetic to match the caption.
    caption: () => "Indemnity + medical",
    tone: "brand",
    money: true,
  },
  {
    testId: "kpi-total-reserve",
    field: "totalReserveCents",
    label: "Total Reserve",
    caption: () => "Active case exposure",
    tone: "plain",
    money: true,
  },
];

/** Row 2 (`renderSV`, line 1068), same contract. */
const ROW_TWO: readonly CardSpec[] = [
  {
    testId: "kpi-fraud-flags",
    field: "fraudFlagged",
    label: "Fraud Flags",
    // The *review* threshold, not the queue's SIU referral one — two rules
    // over one column pair, and the server counted at this one.
    caption: (summary) => `Score ≥ ${summary.fraudScoreMin} — review needed`,
    tone: "error",
  },
  {
    testId: "kpi-osha-recordable",
    field: "oshaRecordable",
    label: "OSHA Recordable",
    caption: () => "Form 300/301 filed",
    tone: "warn",
  },
  {
    testId: "kpi-litigation",
    field: "litigation",
    label: "Litigation",
    caption: () => "Attorney representation",
    tone: "error",
  },
  {
    testId: "kpi-surgery-required",
    field: "surgeryRequired",
    label: "Surgery Required",
    caption: () => "Operative cases",
    tone: "warn",
  },
];

const DATASET = "WC_Manufacturing_Claims_2026.xlsx";

function Row({
  specs,
  summary,
  columns,
}: {
  specs: readonly CardSpec[];
  summary: PortfolioSummary | undefined;
  /** The prototype's `.kr6` / `.kr4` grids. */
  columns: string;
}) {
  return (
    <div className={`mb-[10px] grid gap-[10px] ${columns}`}>
      {specs.map((spec) =>
        summary === undefined ? (
          <KpiCardSkeleton key={spec.testId} />
        ) : (
          <KpiCard
            key={spec.testId}
            testId={spec.testId}
            // The union above is what makes this exhaustive rather than
            // hopeful: `spec.money` narrows `spec.field` to the `*Cents` half,
            // so `formatCents` can never be handed a claim count.
            value={
              spec.money === true
                ? formatCents(summary[spec.field])
                : String(summary[spec.field])
            }
            label={spec.label}
            caption={spec.caption(summary)}
            tone={spec.tone}
          />
        ),
      )}
    </div>
  );
}

/**
 * The dataset chip — the prototype's `.sb`, with its three counts computed.
 *
 * `renderSV` writes "10 employers · 15 US plants" as literals; both are
 * scope-blind and the second is wrong even for the full portfolio (its own
 * array holds 29 distinct plants). All three come off the response here, so a
 * scoped supervisor is told the size of the book she can actually see.
 */
function DatasetChip({ summary }: { summary: PortfolioSummary }) {
  return (
    <span
      data-testid="dataset-chip"
      className="rounded-[3px] bg-steel-soft px-2 py-[3px] font-sans text-[10px] font-bold text-steel"
    >
      📊 {DATASET} · {summary.totalClaims} claims · {summary.employerCount}{" "}
      employers · {summary.plantCount} plants
    </span>
  );
}

export function DashboardPage() {
  const summary = useDashboardSummary();
  const benchmarks = useHandlerBenchmarks();
  const charts = useDashboardCharts();

  return (
    <div
      data-testid="portfolio-dashboard"
      // The whole surface, not the rows alone: the chip's counts are as absent
      // as the cards' figures while the request is in flight, and a live region
      // that announced only half of it would be describing a screen nobody sees.
      //
      // **`summary.isPending` alone, and that is a decision rather than an
      // oversight.** With two independent queries there are two defensible
      // readings — "something on this page is loading" (an OR) and "the page's
      // primary content is loading" — and they differ in the case that actually
      // happens: the cards land first and the table a moment later, and an OR
      // would keep the whole region busy while ten figures sat on screen fully
      // readable. The table carries its own `aria-busy` for its own rows, so
      // the second half is announced where it is, not by a flag on the first.
      aria-busy={summary.isPending}
    >
      <h2 className="mb-[13px] flex flex-wrap items-center gap-2 font-display text-[15px] font-bold text-text">
        Manufacturing WC — Portfolio Overview
        {/* `!isError` as well as `data`, because those are not the same
            condition. `refetchOnMount` is on (only `refetchOnWindowFocus` is
            off), so remounting after `staleTime` expires refetches, and a
            refetch that fails leaves `isError` true with the *previous*
            response still in `data` — which would draw a chip reading "100
            claims · 10 employers" directly above "⚠ Portfolio figures could not
            be loaded", with all ten cards gone. That mixed state is precisely
            what the "no zeros in place of the cards" rule below exists to
            prevent, one level up: stale counts presented as current are the
            quiet version of the same lie. On failure the header carries no
            figures at all. */}
        {summary.data && !summary.isError && (
          <DatasetChip summary={summary.data} />
        )}
      </h2>

      {/* Announced rather than only drawn: a supervisor using a screen reader
          gets one polite sentence when the figures land, instead of ten cards
          appearing silently. `sr-only` because the cards themselves are the
          visual announcement. */}
      <p role="status" aria-live="polite" className="sr-only">
        {summary.isPending
          ? "Loading portfolio figures."
          : summary.isError
            ? // `QueuePane`'s ruling: the error paragraph below is a
              // `role="alert"` of its own, and announcing the same failure
              // twice — assertively, preempting this polite region — is worse
              // than announcing it once.
              ""
            : "Portfolio figures updated."}
      </p>

      {summary.isError ? (
        // Inline, never a dialog (NFR-3) — and no zeros in place of the cards:
        // ten zeroed KPIs are a portfolio with nothing in it, which is a
        // different and much quieter lie than an error message.
        <p
          role="alert"
          data-testid="portfolio-summary-error"
          className="rounded-md border border-border bg-error-soft px-3 py-2 text-[11.5px] font-semibold text-error"
        >
          ⚠ Portfolio figures could not be loaded. Try again in a moment.
        </p>
      ) : (
        <>
          <Row
            specs={ROW_ONE}
            summary={summary.data}
            columns="lg:grid-cols-6 sm:grid-cols-3"
          />
          <Row
            specs={ROW_TWO}
            summary={summary.data}
            columns="lg:grid-cols-4 sm:grid-cols-2"
          />
        </>
      )}

      {/* Outside the summary's error branch on purpose: the two sections are
          two server answers, and a failed portfolio request says nothing about
          whether the handler table loaded. Nesting it inside would have made
          one endpoint's outage blank the other's content for no reason. */}
      <HandlerBenchmarkTable
        data={benchmarks.data}
        isPending={benchmarks.isPending}
        isError={benchmarks.isError}
      />

      {/* Outside the summary's error branch for the table's reason, one section
          further down: three server answers, three failure modes. A charts
          request that 404s shows its seven inline alerts and leaves the ten KPI
          cards and the ranked table exactly as they were (NFR-3). */}
      <PortfolioCharts
        data={charts.data}
        isPending={charts.isPending}
        isError={charts.isError}
      />
    </div>
  );
}
