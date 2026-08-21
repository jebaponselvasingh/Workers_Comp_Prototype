/**
 * Flagged-claim rates by injury type, employer and handler (AC 3).
 *
 * `HandlerBenchmarkTable`'s idiom three times over: a hand-written semantic
 * `<table>`, a `ColumnSpec` list that generates the head and the body together,
 * and a fixed `isError → empty → data` branch order with the alert *replacing*
 * the table rather than sitting above an empty one.
 *
 * **Two busy predicates rather than one, and the split is this surface's own.**
 * `HandlerBenchmarkTable` has one query per table and can therefore let one
 * boolean drive both `aria-busy` and the skeletons. Here three tables ride one
 * query keyed on the whole sort set, so a control change re-keys it and every
 * table is technically in flight — while two of the three are showing rows that
 * have not moved and an order nobody asked about. So `isLoading` (no payload at
 * all: skeletons *and* busy) is separated from `isRefreshing` (a new order
 * outstanding for *this* table: busy, previous rows kept). `FraudPage` decides
 * which table that is; `useFraudRates` keeps the previous answer on screen.
 *
 * **The sort control sets a server parameter and nothing else.** Changing it
 * changes the TanStack key, which issues a request, which returns rows the
 * server ordered; there is no array re-ordering anywhere in this file and there
 * must not be one. That is not merely AD-1 discipline: the orders are total
 * orders with an explicit label tie-break decided in
 * `services/worklist/fraud.py`, over a population the browser holds only eight
 * rows of, so a client-side re-order would disagree with the server about the
 * tail of a truncated table and would have no way to know.
 *
 * **…and the control renders the server's echoed order, not its own last click.**
 * `RateBreakdownResponse.sort` exists for exactly that, and it is the failing
 * request that needs it: a 422 or a timeout otherwise leaves a `<select>`
 * claiming an order the rows beside it are not in, with no way back except
 * another click. The requested value stands in only while its answer is
 * outstanding, which is the one moment the echo is knowingly stale.
 *
 * (`noDerivation.test.ts` is the real guard and reads code with comments
 * stripped. The spec's own verification is a plain grep over this folder, which
 * is why the sentence above names the operation rather than spelling it — a
 * prose mention of a banned call is exactly the false positive
 * `test_no_module_outside_the_registry_hardcodes_the_band` had to write an
 * allowlist paragraph about on the server side.)
 *
 * **`rateBp` is basis points and `lib/rate.ts` is the only place they become a
 * percentage.** Not a local `/ 100`: `noDerivation.test.ts` fails a build on
 * arithmetic against a payload field, and rightly — a unit conversion written at
 * a call site is one keystroke from a threshold. The comp rate reached the same
 * conclusion in Story 3.1 and this table reuses the function it produced.
 *
 * **Three figures per row, and the denominator is the point.** A rate alone is
 * uninterpretable over a thin bucket — one flagged claim of one is 100%, and so
 * is fifty of fifty — so `flagged` and `claims` sit beside `rateBp` and the
 * reader can see what the percentage is a percentage *of*. The server refuses to
 * invent a minimum sample size, and this table refuses to hide the reason.
 *
 * **Every row is a click target.** Injury type opens `filter[injuryType]` on the
 * exact stored string, employer opens `filter[employerId]` and handler opens
 * `filter[handlerId]` — the same three facets the portfolio charts already drill
 * on, so a row here and a bar there open the same list.
 */
import { Link } from "react-router";

import type {
  EmployerRate,
  FraudRateSort,
  FraudRateSorts,
  FraudRates,
  HandlerRate,
  InjuryTypeRate,
} from "@/api/dashboard";
import { formatBasisPoints } from "@/lib/rate";

import type { DrillFilters } from "../drill/filters";

import { drillHref } from "../drill/filters";

/** How many placeholder rows are held open while a request is in flight. */
const SKELETON_ROWS = 6;

/**
 * The five orders, and the words the UI gives them.
 *
 * The enum's values are the server's (snake_case, per the convention) and the
 * copy is this file's, which is the whole of "the UI owns display labels". A
 * `Record` over the generated union rather than a lookup with a fallback, so a
 * sixth order added on the server fails the build here rather than rendering a
 * control with a blank option in it.
 */
const SORT_LABEL: Record<FraudRateSort, string> = {
  rate_desc: "Highest rate",
  rate_asc: "Lowest rate",
  flagged_desc: "Most flagged",
  claims_desc: "Most claims",
  label_asc: "Name (A–Z)",
};

/** The order the options are offered in — the enum's, declared once. */
const SORT_ORDER: readonly FraudRateSort[] = [
  "rate_desc",
  "rate_asc",
  "flagged_desc",
  "claims_desc",
  "label_asc",
];

/**
 * One column of one table.
 *
 * `HandlerBenchmarkTable.ColumnSpec` field for field, generic over the row type
 * because the three tables carry three different row shapes that differ only in
 * how they name their subject.
 */
interface ColumnSpec<RowT> {
  key: string;
  header: string;
  /** `text-left` for names, `text-center` for figures — the prototype's. */
  align: string;
  cell: (row: RowT) => React.ReactNode;
}

/**
 * The three figure columns, identical on all three tables.
 *
 * Declared once and spread into each `COLUMNS` list, because they really are one
 * decision: "publish the rate beside the two counts it was computed from" is the
 * shape of the payload, and three copies of it would be three places for a
 * column to go missing from one table only.
 */
function figureColumns<RowT extends { flagged: number; claims: number; rateBp: number }>(): readonly ColumnSpec<RowT>[] {
  return [
    {
      key: "rate",
      header: "Flagged rate",
      align: "text-center",
      cell: (row) => (
        <span className="font-mono font-bold">{formatBasisPoints(row.rateBp)}%</span>
      ),
    },
    {
      key: "flagged",
      header: "Flagged",
      align: "text-center",
      cell: (row) => <span className="font-mono">{row.flagged}</span>,
    },
    {
      key: "claims",
      header: "Claims",
      align: "text-center",
      cell: (row) => <span className="font-mono text-muted-text">{row.claims}</span>,
    },
  ];
}

/** A subject cell that opens the claims behind its row. */
function SubjectLink({
  testId,
  label,
  filters,
}: {
  testId: string;
  label: string;
  filters: DrillFilters;
}) {
  return (
    <Link
      data-testid={testId}
      to={drillHref(filters)}
      className="rounded font-semibold text-text hover:underline focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
    >
      {label}
    </Link>
  );
}

const INJURY_COLUMNS: readonly ColumnSpec<InjuryTypeRate>[] = [
  {
    key: "subject",
    header: "Injury type",
    align: "text-left",
    // The **exact stored string**, which is what the server matches on: no
    // trimming, no case-folding, no merging of near-duplicates. Canonicalizing
    // free text is a data-quality decision with an owner, and a link that did it
    // would open a list the row never counted.
    cell: (row) => (
      <SubjectLink
        testId="fraud-rate-link"
        label={row.injuryType}
        filters={{ injuryType: row.injuryType }}
      />
    ),
  },
  ...figureColumns<InjuryTypeRate>(),
];

const EMPLOYER_COLUMNS: readonly ColumnSpec<EmployerRate>[] = [
  {
    key: "subject",
    header: "Employer",
    align: "text-left",
    // The id, never the label: a `short_name` is a label and not an identity.
    cell: (row) => (
      <SubjectLink
        testId="fraud-rate-link"
        label={row.label}
        filters={{ employerId: String(row.employerId) }}
      />
    ),
  },
  ...figureColumns<EmployerRate>(),
];

const HANDLER_COLUMNS: readonly ColumnSpec<HandlerRate>[] = [
  {
    key: "subject",
    header: "Handler",
    align: "text-left",
    cell: (row) => (
      <SubjectLink
        testId="fraud-rate-link"
        label={row.handlerName}
        filters={{ handlerId: String(row.handlerId) }}
      />
    ),
  },
  ...figureColumns<HandlerRate>(),
];

function SkeletonRows({ columns }: { columns: number }) {
  return (
    <>
      {Array.from({ length: SKELETON_ROWS }, (_, index) => (
        <tr key={index} data-testid="fraud-rate-row-skeleton" aria-hidden>
          <td colSpan={columns} className="py-[7px]">
            <span className="block h-[14px] animate-pulse rounded bg-surface-2" />
          </td>
        </tr>
      ))}
    </>
  );
}

/**
 * One breakdown: a heading, a sort control, and the table.
 *
 * Generic over the row type so the three tables are one component rather than
 * three near-copies — the divergence `HandlerBenchmarkTable`'s `COLUMNS` list
 * exists to prevent, applied to three tables instead of one.
 */
function RateTable<RowT>({
  testId,
  title,
  errorSubject,
  columns,
  breakdown,
  sort,
  onSort,
  truncationCaption,
  emptyMessage,
  isPending,
  isRefreshing,
  isError,
  rowKey,
}: {
  testId: string;
  title: string;
  /**
   * What this table's failure alert names itself.
   *
   * Its own sentence rather than the heading interpolated, because the heading is
   * a column-relative fragment ("By employer") and an alert has to stand alone:
   * all three of these fire at once — one query, three sections — and a screen
   * reader reads three of them in a row. `PortfolioCharts` sets the precedent, six
   * charts to one `/dashboard/charts` request, each naming its own subject.
   */
  errorSubject: string;
  columns: readonly ColumnSpec<RowT>[];
  breakdown:
    | {
        items: RowT[];
        totalCategories: number;
        truncated: boolean;
        limit: number | null;
        /** The order the server applied — what the control renders. */
        sort: FraudRateSort;
      }
    | undefined;
  /** The order this table has been *asked* for; the echo above wins once it lands. */
  sort: FraudRateSort;
  onSort: (next: FraudRateSort) => void;
  truncationCaption: (shown: number, total: number) => string;
  emptyMessage: string;
  isPending: boolean;
  /** A new order is outstanding for this table, and the previous rows are on screen. */
  isRefreshing: boolean;
  isError: boolean;
  rowKey: (row: RowT) => string;
}) {
  // `HandlerBenchmarkTable`'s conjunction — a query status without a payload and
  // a payload without a status cannot put the skeletons and the busy flag out of
  // step — but driving the *skeletons* only. There is nothing to draw yet.
  const isLoading = isPending && breakdown === undefined;
  // …and the busy flag is the wider of the two, because "waiting for this table's
  // new order" is also a load, just one with readable rows under it.
  const isBusy = isLoading || isRefreshing;
  // The server's answer once there is one, and the requested value only while
  // that answer is outstanding. On a failed request the echo is the order the
  // rows on screen are actually in, which is the state this field exists for.
  const shownSort = breakdown === undefined || isRefreshing ? sort : breakdown.sort;

  return (
    <section
      data-testid={testId}
      aria-labelledby={`${testId}-heading`}
      aria-busy={isBusy}
      className="rounded-lg border border-border bg-surface p-3"
    >
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3
          id={`${testId}-heading`}
          className="font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase"
        >
          {title}
        </h3>
        <select
          data-testid={`${testId}-sort`}
          aria-label={`Sort ${title}`}
          value={shownSort}
          onChange={(event) => onSort(event.target.value as FraudRateSort)}
          className="rounded border border-border bg-surface px-[6px] py-px text-[10px] text-muted-text focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
        >
          {SORT_ORDER.map((option) => (
            <option key={option} value={option}>
              {SORT_LABEL[option]}
            </option>
          ))}
        </select>
      </div>

      {isError ? (
        // Inline, never a dialog (NFR-3), and in place of the table rather than
        // above an empty one: a headless table reads as "this scope has nothing
        // in it", which is a different and much quieter lie than a failure.
        //
        // `errorSubject` rather than `title`: one failed request raises all three
        // of these at once, so a reader hears them back to back and each has to
        // say which table it is about. Three sentences reading "By employer could
        // not be loaded" are one sentence heard three times with the subject
        // hidden in a fragment.
        <p
          role="alert"
          data-testid={`${testId}-error`}
          className="rounded-md border border-border bg-error-soft px-3 py-2 text-[11.5px] font-semibold text-error"
        >
          ⚠ {errorSubject} could not be loaded. Try again in a moment.
        </p>
      ) : breakdown !== undefined && breakdown.items.length === 0 ? (
        <p data-testid={`${testId}-empty`} className="text-[11.5px] text-faint">
          {emptyMessage}
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[11.5px]">
              <caption className="sr-only">{title}</caption>
              <thead>
                <tr className="text-[10px] tracking-[0.3px] text-faint uppercase">
                  {columns.map((column) => (
                    <th
                      key={column.key}
                      scope="col"
                      className={`border-b-2 border-border py-[6px] pr-2 font-semibold ${column.align}`}
                    >
                      {column.header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {isLoading ? (
                  <SkeletonRows columns={columns.length} />
                ) : (
                  breakdown?.items.map((row) => (
                    <tr
                      key={rowKey(row)}
                      data-testid={`${testId}-row`}
                      data-row-key={rowKey(row)}
                      className="border-b border-hairline last:border-b-0"
                    >
                      {columns.map((column) => (
                        <td
                          key={column.key}
                          data-testid={`${testId}-${column.key}`}
                          className={`py-[7px] pr-2 ${column.align}`}
                        >
                          {column.cell(row)}
                        </td>
                      ))}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {/* Assembled from `limit` and `totalCategories`, and rendered only
              when the server says it cut — so a scope with fewer categories
              than the cap carries no apology for a truncation that did not
              happen. `limit ?? items.length` for `DistributionBars`' recorded
              reason: a caption keyed on `limit` alone renders nothing if
              `truncated` ever arrives beside a null limit, which is silent
              truncation and the one thing these three fields exist to prevent. */}
          {breakdown?.truncated === true && (
            <p data-testid={`${testId}-truncation`} className="mt-2 text-[10px] text-faint">
              {truncationCaption(
                breakdown.limit ?? breakdown.items.length,
                breakdown.totalCategories,
              )}
            </p>
          )}
        </>
      )}
    </section>
  );
}

export function FraudRateTables({
  data,
  sorts,
  onSort,
  pendingSort,
  isPending,
  isError,
}: {
  /** The server's three breakdowns, or `undefined` while they are unknown. */
  data: FraudRates | undefined;
  sorts: FraudRateSorts;
  /** Set one table's order. The page owns the state; the request follows it. */
  onSort: (table: keyof FraudRateSorts, next: FraudRateSort) => void;
  /**
   * Which table is waiting on a new order, if any.
   *
   * The three tables are one request, so the *query* cannot say which of them a
   * re-fetch is for — only the control that was used can, and `FraudPage` is
   * where that is remembered. `null` while nothing is outstanding, which is
   * every state except the moment after a click.
   */
  pendingSort: keyof FraudRateSorts | null;
  isPending: boolean;
  isError: boolean;
}) {
  const state = { isPending, isError };

  return (
    <section
      data-testid="fraud-rates"
      aria-labelledby="fraud-rates-heading"
      className="mb-[14px]"
    >
      <h2
        id="fraud-rates-heading"
        className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase"
      >
        Flagged-claim rates
      </h2>

      <div className="grid gap-[10px] lg:grid-cols-3">
        <RateTable
          testId="fraud-rate-injury"
          title="By injury type"
          errorSubject="The flagged-claim rates by injury type"
          columns={INJURY_COLUMNS}
          breakdown={data?.byInjuryType}
          sort={sorts.injuryType}
          isRefreshing={pendingSort === "injuryType"}
          onSort={(next) => onSort("injuryType", next)}
          truncationCaption={(shown, total) =>
            `Showing ${String(shown)} of ${String(total)} injury types.`
          }
          emptyMessage="No claims in this portfolio yet."
          rowKey={(row) => row.injuryType}
          {...state}
        />

        <RateTable
          testId="fraud-rate-employer"
          title="By employer"
          errorSubject="The flagged-claim rates by employer"
          columns={EMPLOYER_COLUMNS}
          breakdown={data?.byEmployer}
          sort={sorts.employer}
          isRefreshing={pendingSort === "employer"}
          onSort={(next) => onSort("employer", next)}
          // Uncapped on the server — the employers in a book are bounded by the
          // assignment rather than by the data — so this is unreachable and says
          // the honest thing if it ever is not.
          truncationCaption={(shown, total) =>
            `Showing ${String(shown)} of ${String(total)} employers.`
          }
          emptyMessage="No claims in this portfolio yet."
          rowKey={(row) => String(row.employerId)}
          {...state}
        />

        <RateTable
          testId="fraud-rate-handler"
          title="By handler"
          errorSubject="The flagged-claim rates by handler"
          columns={HANDLER_COLUMNS}
          breakdown={data?.byHandler}
          sort={sorts.handler}
          isRefreshing={pendingSort === "handler"}
          onSort={(next) => onSort("handler", next)}
          truncationCaption={(shown, total) =>
            `Showing ${String(shown)} of ${String(total)} handlers.`
          }
          emptyMessage="No handler carries a claim in this portfolio yet."
          rowKey={(row) => String(row.handlerId)}
          {...state}
        />
      </div>

      {/* The rule every `flagged` column was counted at, quoted from the
          response — the KPI cards' two captions and the benchmark table's band
          footnote, on a third surface and for their reason: superseding the rule
          document has to move the figures *and* the sentence explaining them,
          together, with nothing deployed. */}
      {data !== undefined && !isError && (
        <p data-testid="fraud-rate-threshold" className="mt-2 text-[10px] text-faint">
          Flagged means a fraud-flagged claim scoring {data.fraudFlagScoreMin} or
          above — the same review rule the portfolio&apos;s Fraud Flags card
          counts, and not the narrower SIU referral rule the pipeline above shows.
        </p>
      )}
    </section>
  );
}
