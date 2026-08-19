/**
 * The ranked handler performance table (UX-DR7, FR-SUP-2/3/B).
 *
 * The prototype builds this inside `renderSV` (line 1015): it groups the global
 * claim array by handler, averages three duration columns per group and over
 * the whole array, adds them up, compares each handler with the portfolio,
 * bands the difference and sorts — all in the browser. Every one of those steps
 * now happens in `services/worklist/benchmarks.py` behind the caller's employer
 * scope (AD-1, AD-7), and this file does exactly three things with the result:
 * pick a colour token, pick a display label, and set a bar's width from a
 * percentage the server computed. `noDerivation.test.ts` walks
 * `features/dashboard` and fails the build over anything else — which is why
 * there is no `.sort()` here even though the rows arrive in an order this
 * component could trivially re-impose, and why `rank` is read off the row
 * rather than taken from the map index.
 *
 * **A hand-written semantic `<table>`, not TanStack Table.** The dependency is
 * installed and has zero uses in `src/`; the one real table in the app
 * (`bills/PaymentScheduleCard.tsx`) is hand-written for the same reason this one
 * is. What TanStack buys is client-side sorting, filtering and paging over a
 * dataset the browser holds — and this browser holds one page of already-
 * ranked rows (six on the full portfolio, two for a scoped supervisor) that it
 * must not re-sort, because the ranking is a function of a rule document's
 * thresholds and a scope predicate it does not have. `getSortedRowModel` would
 * be precisely the guard-violating derivation the story is built to prevent.
 * Recorded as a deliberate departure from Task 4's wording in the story file's
 * Dev Agent Record.
 *
 * **Rows are inert.** Drill-through into a handler's caseload is Story 5.5; the
 * shape that survives it is the one here — data in, nothing out — so adding an
 * `onRowClick` later changes this file and none of its callers.
 *
 * **The bar is a token-styled div, not a chart.** Story 5.3 owns Recharts and
 * the dependency is still absent. The fill copies `injury/SeverityCard.tsx`:
 * `width: ${n}%` straight from a server figure, with the *colour* carrying the
 * server's banding. Which end of the scale the width means is a decision and is
 * argued at `CycleSpeedBar`.
 */
import type {
  ComplexityBand,
  CycleStatus,
  HandlerBenchmark,
  HandlerBenchmarks,
} from "@/api/dashboard";

import { Link } from "react-router";

import { CHIP_CLASS } from "../claim-detail/bills/statusTone";

import { drillHref } from "./drill/filters";

/** The em dash the console draws wherever a figure is genuinely unknown. */
const EM_DASH = "—";

/** How many placeholder rows are held open while the request is in flight. */
const SKELETON_ROWS = 6;

/**
 * The deviation's sign, spelled out.
 *
 * `signDisplay: "exceptZero"` rather than a comparison against zero, and that
 * is not stylistic: `noDerivation.test.ts` forbids comparing anything in this
 * directory against a numeric literal, because that is what re-banding a chip
 * looks like. Asking `Intl` for the sign keeps the formatting honest *and*
 * keeps the guard meaningful — there is no arithmetic here to have to exempt.
 */
const DEVIATION_FORMAT = new Intl.NumberFormat("en-US", { signDisplay: "exceptZero" });

/**
 * Complexity grade → chip colour, and the labels the enum convention leaves to
 * the UI.
 *
 * `ok` / `warn` / `error` and nothing else — the same three tones the queue
 * chips and the KPI cards use (UX notes: "no new colours"). Reading them as
 * good/bad is the wrong frame and is worth saying: a High complexity book is
 * not a handler doing badly, it is a desk carrying severe, surgical and
 * litigated claims. The tone is loudness — "look here first" — which is the
 * same meaning `bills/statusTone.ts` records for its own map.
 */
const COMPLEXITY_TONE: Record<ComplexityBand, string> = {
  low: "border-ok/40 bg-ok-soft text-ok",
  med: "border-warn/40 bg-warn-soft text-warn",
  high: "border-error/40 bg-error-soft text-error",
};

const COMPLEXITY_LABEL: Record<ComplexityBand, string> = {
  low: "Low",
  med: "Medium",
  high: "High",
};

/** Cycle status → chip colour. The story's mapping, verbatim. */
const STATUS_TONE: Record<CycleStatus, string> = {
  on_track: "border-ok/40 bg-ok-soft text-ok",
  watch: "border-warn/40 bg-warn-soft text-warn",
  attention: "border-error/40 bg-error-soft text-error",
};

const STATUS_LABEL: Record<CycleStatus, string> = {
  on_track: "On Track",
  watch: "Watch",
  attention: "Attention",
};

/** The bar's fill, banded by the same status the chip shows. */
const STATUS_FILL: Record<CycleStatus, string> = {
  on_track: "bg-ok",
  watch: "bg-warn",
  attention: "bg-error",
};

/**
 * How many decimals a composite cycle time is shown to.
 *
 * **A local constant rather than a `decimals` field on the response**, and the
 * choice is worth stating because `/stats/sla` does ship one per metric. It has
 * to: that endpoint publishes four metrics whose precisions genuinely differ
 * (pick and approve to a tenth, settle and the RTW rate to whole units), so
 * "how many decimals" is data there. Here there is exactly one such figure —
 * `compositeDays`, in the row and again as `portfolioCompositeDays` — and
 * `benchmarks.COMPOSITE_DECIMALS` is a module constant on the server too, not a
 * rule-document parameter. Putting a field on the wire to carry a constant that
 * cannot vary would add a payload key nothing could ever change, and would
 * still leave this file needing a fallback for the null case. If the precision
 * ever becomes a policy, it becomes a response field on that day.
 */
const COMPOSITE_DECIMALS = 1;

/**
 * A number the server sent, or the em dash — never a zero standing in for an
 * unknown.
 *
 * `String(value)` rather than a fixed precision: the server rounded these to
 * the precision it decided them at (`sla.py`'s ruling for the tiles), so a
 * client formatting to a precision of its own would display a figure the server
 * never computed. Used for the RTW rate, which is a whole percentage.
 */
function figure(value: number | null, suffix: string): string {
  return value === null ? EM_DASH : `${String(value)}${suffix}`;
}

/**
 * A composite cycle time, shown at the precision it was **ranked** at.
 *
 * `toFixed` and not `String`, and this is the one formatting decision on the
 * page that changes what a reader can conclude. The server ranks on the exact
 * composite and publishes it to a tenth of a day precisely so the Avg Days
 * column has the precision the order was decided at — but a JSON number that
 * lands on a whole day loses its tenth on the way through `String`, so Dante's
 * 78.0 rendered "78d" and Marcus's 72.0 rendered "72d" beside Fatima's "72.7d".
 * A reader comparing rows 3 and 4 then saw what looked like a seven-tenths gap
 * and what was in fact a rank decided by six hundredths, with no way to tell
 * the difference — which is the exact ambiguity the whole re-derivation of this
 * composite was about. Every composite therefore shows the same number of
 * decimals, including the trailing zero.
 *
 * This is formatting a figure the server decided, not deciding one: the value
 * is already rounded to this precision on the wire, so `toFixed` never changes
 * a digit — it only refuses to drop one.
 */
function compositeFigure(value: number | null, suffix: string): string {
  return value === null ? EM_DASH : `${value.toFixed(COMPOSITE_DECIMALS)}${suffix}`;
}

/**
 * The cycle-speed bar — peer-relative, and the ratio is the server's.
 *
 * **Direction: full width is the *slowest* handler in scope.** That is the
 * prototype's `comp / maxComp` and it is kept deliberately rather than
 * inherited; the alternative — inverting it so a longer bar means faster, which
 * the column's name argues for — was considered and rejected for one reason.
 * The bar sits immediately beside the Avg Days column, and with this direction
 * its length *is* that column drawn: two handlers ten percent apart in days are
 * ten percent apart in bar length, and the eye can compare the two encodings
 * against each other. Inverting it makes the bar a reciprocal of the number
 * next to it — monotonic, but no longer proportional — so a reader comparing
 * bar to figure would be comparing two different scales and would have no way
 * to know. The header word is the design contract's (AC 1 fixes the nine column
 * names), so the units are stated in the bar's own accessible name instead of
 * left to be inferred from its length. Recorded in the Dev Agent Record.
 *
 * **The cell is named, once.** A `<td>` whose only content is a decorative
 * `<span>` reads as an empty cell under a header promising a figure, so the
 * composite and what the fill is a percentage *of* are in the cell as
 * screen-reader text, and the bar itself is `aria-hidden`.
 *
 * **One mechanism, not two.** The same sentence used to be both `sr-only` text
 * *and* the wrapper's `title`. That bought nothing and cost twice: a screen
 * reader may announce an element's `title` in addition to its contents, so the
 * sentence could be read out twice; and a `title` is a promise of a tooltip
 * that only exists for a mouse — it never appears on keyboard focus and never
 * on touch, so the affordance it advertises is absent for exactly the readers
 * who need the number most. The `sr-only` text is the accessible one and is
 * what remains. The composite is also in the Avg Days cell immediately to the
 * right, so nothing is lost for a sighted reader either.
 */
function CycleSpeedBar({ row, tone }: { row: HandlerBenchmark; tone: string }) {
  const pct = row.cycleSpeedPct;
  if (pct === null || row.compositeDays === null) {
    return <span className="text-faint">{EM_DASH}</span>;
  }
  const label = `${compositeFigure(row.compositeDays, "")} days, ${String(pct)}% of the slowest cycle time in this portfolio`;
  return (
    <span className="block">
      <span className="sr-only">{label}</span>
      <span
        aria-hidden
        className="block h-[8px] w-full min-w-[80px] overflow-hidden rounded-full bg-surface-2"
      >
        <span
          data-testid="cycle-speed-fill"
          style={{ width: `${String(pct)}%` }}
          className={`block h-full min-w-[2px] rounded-full ${tone}`}
        />
      </span>
    </span>
  );
}

function Chip({ tone, children }: { tone: string; children: React.ReactNode }) {
  return <span className={`${CHIP_CLASS} ${tone}`}>{children}</span>;
}

interface ColumnSpec {
  /** `data-testid` stem for the cell, and the React key for the column. */
  key: string;
  header: string;
  /** `text-left` for names, `text-center` for figures — the prototype's. */
  align: string;
  cell: (row: HandlerBenchmark) => React.ReactNode;
}

/**
 * The nine columns, in the prototype's order (`renderSV`, line 1078).
 *
 * Declared once as data rather than laid out twice in JSX, for `DashboardPage`'s
 * `CardSpec` reason: the order *is* the design contract, so "the columns render
 * in this order" should be a list a reviewer can check against the prototype
 * line by line — and the header row and the body row are then generated from
 * one source and cannot drift apart.
 */
const COLUMNS: readonly ColumnSpec[] = [
  {
    key: "rank",
    header: "#",
    align: "text-center",
    // `row.rank`, never the map index. The server ranked this list under a
    // rules version and a scope predicate the browser has neither of, and it
    // publishes `null` for a row it could not rank — which `{index + 1}` would
    // paper over with a confident number beside a row of em dashes.
    cell: (row) => (
      <span className="font-mono text-faint">
        {row.rank === null ? EM_DASH : row.rank}
      </span>
    ),
  },
  {
    key: "handler",
    header: "Handler",
    align: "text-left",
    // **The link filters on `handlerId`, never on the name** (Story 5.5, AC 3).
    // The id is what `benchmarks.py` has grouped on since this table was
    // written, precisely because two handlers may share a display name — and a
    // drill-through keyed on the name would merge two desks into one list while
    // looking perfectly correct. A `<Link>` rather than a row `onClick`: it is a
    // navigation to a URL a supervisor can copy, and it keeps the rest of the
    // row selectable text.
    cell: (row) => (
      <Link
        data-testid="benchmark-handler-link"
        data-handler-id={row.handlerId}
        to={drillHref({ handlerId: String(row.handlerId) })}
        className="rounded font-semibold text-text hover:underline focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
      >
        {row.handlerName}
      </Link>
    ),
  },
  {
    key: "cases",
    header: "Cases",
    align: "text-center",
    cell: (row) => <span className="font-mono">{row.caseCount}</span>,
  },
  {
    key: "cycle-speed",
    header: "Cycle Speed",
    align: "text-left",
    cell: (row) => (
      <CycleSpeedBar
        row={row}
        // A row with no composite has no status either — they are one condition
        // on the wire — so the fallback tone is never reached with a bar to
        // paint. Spelled out anyway because the map is exhaustive over the enum
        // and `cycleStatus` is nullable.
        tone={row.cycleStatus === null ? "bg-surface-2" : STATUS_FILL[row.cycleStatus]}
      />
    ),
  },
  {
    key: "avg-days",
    header: "Avg Days",
    align: "text-center",
    cell: (row) => (
      <span className="font-mono text-muted-text">
        {compositeFigure(row.compositeDays, "d")}
      </span>
    ),
  },
  {
    key: "rtw",
    header: "RTW %",
    align: "text-center",
    // Deliberately *not* toned by a threshold. The prototype colours this cell
    // green/amber/red from two cut-offs written into the render function, and
    // there is no rule document behind them — reproducing that here would be a
    // band decided in the browser, which is the one thing this directory may
    // not do.
    cell: (row) => <span className="font-mono font-bold">{figure(row.rtwPct, "%")}</span>,
  },
  {
    key: "complexity",
    header: "Complexity",
    align: "text-center",
    cell: (row) => (
      <Chip tone={COMPLEXITY_TONE[row.complexityBand]}>
        {COMPLEXITY_LABEL[row.complexityBand]} ({row.complexityScore})
      </Chip>
    ),
  },
  {
    key: "pending",
    header: "Pending Approvals",
    align: "text-center",
    cell: (row) => <span className="font-mono">{row.pendingApprovals}</span>,
  },
  {
    key: "status",
    header: "Status",
    align: "text-center",
    // **The chip carries the deviation it was banded on.** The footnote below
    // quotes both thresholds as percentages; a chip banded by a percentage the
    // page never shows would leave that sentence measuring against nothing and
    // the chip unexplainable from the screen — a reader could not tell "Watch"
    // at -7% from "Watch" at +7%, which is the difference between a desk that is
    // fine and one about to need a check-in. It is the server's number,
    // formatted and not recomputed.
    cell: (row) =>
      row.cycleStatus === null || row.deviationPct === null ? (
        <span className="text-faint">{EM_DASH}</span>
      ) : (
        <Chip tone={STATUS_TONE[row.cycleStatus]}>
          {STATUS_LABEL[row.cycleStatus]} {DEVIATION_FORMAT.format(row.deviationPct)}%
        </Chip>
      ),
  },
];

/**
 * The prototype's callout, with the two names the server decided.
 *
 * Rendered only when the two differ: on a scope with a single ranked handler
 * the sentence would name the same person as both the fastest and the slowest
 * desk, which is true, useless and faintly absurd. The server publishes both
 * names rather than leaving the UI to index the first and last row, so this
 * component never assumes the list is ordered.
 *
 * **What `leader === laggard` establishes, exactly.** The server picks both
 * from the *rankable* rows — the ones that acquired a composite — so the
 * condition means "exactly one handler in this scope could be ranked", which is
 * not the same as "exactly one handler has claims here": a scope whose
 * portfolio is missing a whole cycle-time segment publishes rows with `rank:
 * null` for every handler, and one that is missing none ranks all of them. The
 * sentence says what the condition supports and nothing more; claiming a count
 * of handlers from a fact about ranking is the kind of small, confident lie a
 * supervisor has no way to check.
 */
function LeaderCallout({
  leader,
  laggard,
}: {
  leader: string | null;
  laggard: string | null;
}) {
  if (leader === null || laggard === null) return null;
  if (leader === laggard) {
    return (
      <p data-testid="benchmark-callout" className="mb-[10px] text-[11.5px] text-muted-text">
        🏆 <b className="text-text">{leader}</b> is the only handler this
        portfolio ranks — there is no peer to compare against.
      </p>
    );
  }
  return (
    <p data-testid="benchmark-callout" className="mb-[10px] text-[11.5px] text-muted-text">
      🏆 <b className="text-text">{leader}</b> leads the desk — fastest overall
      cycle time. ⚠️ <b className="text-text">{laggard}</b> is running slowest and
      could use a workload check-in.
    </p>
  );
}

/**
 * The bands the chips came from, quoted from the response.
 *
 * The table's counterpart to the KPI cards' two captions, and it exists for the
 * same reason: superseding `handler_performance` must move the chips *and* the
 * sentence that explains them, together, with nothing deployed. A constant here
 * would be a second copy of a rule the browser cannot see change.
 *
 * Rendered under the **empty** state as well as under a populated table. The
 * server populates the thresholds for a scope with no handlers on purpose —
 * "nothing in this book" says nothing about which rules were in force — and
 * dropping the footnote there would throw away the only statement of that, on
 * the one screen where a reader has nothing else to go on.
 *
 * **The portfolio clause is a branch, not a slot with an em dash in it.** An
 * unknown figure inside a sentence is not the same problem as an unknown figure
 * in a cell: a cell reading "—" says "this is not known", while
 * "…against this portfolio&apos;s — On Track at or below -8%" is not a sentence
 * at all, and it is the empty state — the screen with no rows to fall back on —
 * where it rendered. So the clause naming the portfolio composite is dropped
 * when there is no portfolio composite, and the bands, which are known either
 * way, are stated on their own.
 */
function BandFootnote({ data }: { data: HandlerBenchmarks }) {
  const bands = (
    <>
      On Track at or below {data.onTrackDeviationPctMax}%, Attention at or above{" "}
      {data.attentionDeviationPctMin}%. Complexity is Medium from{" "}
      {data.complexityMedMin} and High from {data.complexityHighMin}.
    </>
  );
  return (
    <p data-testid="benchmark-bands" className="mt-2 text-[10px] text-faint">
      {data.portfolioCompositeDays === null ? (
        <>
          Status bands each handler&apos;s composite cycle time by its deviation
          from this portfolio. {bands}
        </>
      ) : (
        <>
          Status is each handler&apos;s composite cycle time against this
          portfolio&apos;s {compositeFigure(data.portfolioCompositeDays, "d")} —{" "}
          {bands}
        </>
      )}
    </p>
  );
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: SKELETON_ROWS }, (_, index) => (
        <tr key={index} data-testid="handler-row-skeleton" aria-hidden>
          <td colSpan={COLUMNS.length} className="py-[7px]">
            <span className="block h-[14px] animate-pulse rounded bg-surface-2" />
          </td>
        </tr>
      ))}
    </>
  );
}

export function HandlerBenchmarkTable({
  data,
  isPending,
  isError,
}: {
  /** The server's table, or `undefined` while it is unknown. */
  data: HandlerBenchmarks | undefined;
  isPending: boolean;
  isError: boolean;
}) {
  /**
   * **One predicate for `aria-busy` and for the skeleton rows.**
   *
   * These were two facts before: the section was busy when `isPending`, and the
   * rows were placeholders when `data === undefined`. They agree in every state
   * TanStack Query produces today, which is precisely why the drift would be
   * silent — `placeholderData`, a `refetchOnMount` that keeps the last rows, or
   * an error branch that renders stale data would move one and not the other,
   * and a screen reader told the section is busy while six real rows are on
   * screen is describing something nobody sees. Now the section says it is busy
   * exactly when it is drawing placeholders, because the same boolean decides
   * both — and it is the conjunction rather than either half, so neither a
   * query status without a payload nor a payload without a status can put the
   * two branches out of step.
   */
  const isLoading = isPending && data === undefined;

  return (
    <section
      data-testid="handler-benchmarks"
      aria-labelledby="handler-benchmarks-heading"
      // Its own busy state as well as the page's: this section can be loading
      // while the KPI cards above it have landed, and a screen reader told the
      // whole dashboard was busy would be describing a screen nobody sees.
      aria-busy={isLoading}
      className="mb-[14px] rounded-lg border border-border bg-surface p-3"
    >
      <h3
        id="handler-benchmarks-heading"
        className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase"
      >
        Claim handler performance
      </h3>

      {isError ? (
        // Inline, never a dialog (NFR-3), and in place of the table rather than
        // above an empty one: a headless table reads as "this scope has no
        // handlers", which is a different and much quieter lie.
        <p
          role="alert"
          data-testid="handler-benchmarks-error"
          className="rounded-md border border-border bg-error-soft px-3 py-2 text-[11.5px] font-semibold text-error"
        >
          ⚠ Handler performance could not be loaded. Try again in a moment.
        </p>
      ) : data !== undefined && data.items.length === 0 ? (
        // The scoped-supervisor empty state (NFR-3). Reachable: a supervisor
        // whose employers have no claims yet has no handlers to rank, which is
        // a fact about her book rather than a failure. The footnote stays — see
        // `BandFootnote`.
        <>
          <p data-testid="handler-benchmarks-empty" className="text-[11.5px] text-faint">
            No handler carries a claim in this portfolio yet.
          </p>
          <BandFootnote data={data} />
        </>
      ) : (
        <>
          {data !== undefined && (
            <LeaderCallout leader={data.leader} laggard={data.laggard} />
          )}
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[11.5px]">
              <caption className="sr-only">
                Handlers with claims in this portfolio, ranked by composite
                cycle time — fastest first
              </caption>
              <thead>
                <tr className="text-[10px] tracking-[0.3px] text-faint uppercase">
                  {COLUMNS.map((column) => (
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
                  <SkeletonRows />
                ) : (
                  data?.items.map((row) => (
                    // Keyed on the handler's name paired with the rank rather
                    // than on the rank alone: `rank` is nullable, and two
                    // handlers can share a display name, so neither is a key on
                    // its own.
                    <tr
                      key={`${String(row.rank)}-${row.handlerName}`}
                      data-testid="handler-row"
                      data-handler={row.handlerName}
                      className="border-b border-hairline last:border-b-0"
                    >
                      {COLUMNS.map((column) => (
                        <td
                          key={column.key}
                          data-testid={`handler-${column.key}`}
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
          {data !== undefined && <BandFootnote data={data} />}
        </>
      )}
    </section>
  );
}
