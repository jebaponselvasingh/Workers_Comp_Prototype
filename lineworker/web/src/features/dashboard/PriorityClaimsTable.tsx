/**
 * The top-30 priority claims worklist (UX-DR7, FR-SUP-5/D).
 *
 * The prototype builds this inside `renderSV` (line 1056) and gets it wrong
 * twice: it cuts the top thirty with `.slice(0, 30)` over a `.filter(...)` in
 * *dataset order*, so the table is priority-ordered in its heading and nowhere
 * else, and it fills the "Priority Next Best Action" column from
 * `cpNextActions[0]` — a pre-authored copilot string on the claim record, which
 * is exactly what AD-2 forbids that column from being. Both are corrected in
 * `services/worklist/priority_claims.py`: the population is ranked by the same
 * scorer the handler queue uses before the cap is applied, and the action is
 * element 0 of the deterministic Epic 3 generator. The visual result differs
 * from the prototype screenshot — the same thirty-ish claims in a different
 * order, with different action text — and the ten headers, the "showing top 30
 * of N" caption and the LITIG-chip-instead-of-a-stage-chip Status cell are
 * ported exactly.
 *
 * This file does four things with the response and nothing else: pick a colour
 * token, pick a display label, choose which of two chips the Status cell draws,
 * and set a `title` so CSS truncation does not lose text.
 * `noDerivation.test.ts` walks `features/dashboard` and fails the build over
 * anything more — which is why there is no `.sort()` here even though the rows
 * arrive in an order this component could trivially re-impose, why the fraud
 * score is tinted by `fraudFlagged` rather than by comparing it with the
 * `fraudFlagScoreMin` published on the same payload, and why the caption quotes
 * `total` and `cap` rather than counting `items`.
 *
 * **A hand-written semantic `<table>`, not TanStack Table**, and this is a
 * deliberate departure from the story's Task 4 wording. The precedent is
 * `HandlerBenchmarkTable.tsx`, which made the same call for the same reason one
 * story earlier: the dependency has zero uses anywhere in `src/`, and what
 * TanStack buys is client-side sorting, filtering and paging over a dataset the
 * browser holds. This browser holds a page of an already-ranked list that it
 * must not re-sort, because the ranking is a function of three rule documents'
 * versions and a scope predicate it has neither of — `getSortedRowModel` is
 * precisely the guard-violating derivation the story is built to prevent, and
 * `getFilteredRowModel` would re-cut a population the server capped. Recorded
 * as a departure in this story's spec, beside the task that asked for the
 * library.
 *
 * **Story 5.5 declared the row affordance 5.4 deliberately withheld, and it is
 * a `<Link>` rather than the `onRowClick` that was withheld.** The reasoning
 * that kept the prop out still holds — an optional handler that type-checks and
 * silently does nothing is worse than its absence — and it also answers what to
 * put in its place: the destination is a *URL*, so the control is an anchor on
 * the claim-id cell rather than a click handler on a `<tr>`. That gets keyboard
 * focus, middle-click and a copyable address for free, and it leaves the other
 * nine cells selectable rather than swallowing every click in the row. The
 * heading gains a "View all" link to the same population without the cap.
 * Nothing on this surface writes — it offers no command, so there is no toast
 * and no audit event on the other end.
 *
 * **Truncation is CSS, and the full string is in `title`.** The prototype cuts
 * the injury type at 22 characters and the action at 48 inside its render
 * function. Truncation is a property of the column a value is drawn in rather
 * than of the claim, so the server sends the whole string, the cell clamps it
 * visually, and a screen reader and a hover both get all of it.
 */
import { useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import type {
  PriorityClaimRow,
  PriorityClaims,
  RiskBand,
} from "@/api/dashboard";
import { usePriorityClaimPages } from "@/api/dashboard";
import { queryKeys } from "@/api/queryKeys";

import { Link } from "react-router";

import { CHIP_CLASS } from "../claim-detail/bills/statusTone";
import { RISK_LABEL } from "../claim-detail/labels";
import { STAGE_LABEL } from "../queue/stageLabels";

import { DASHBOARD_ROUTE } from "@/features/shell/routes";

import { claimHref, drillHref, type DrillOrigin } from "./drill/filters";

/** How many placeholder rows are held open while the request is in flight. */
const SKELETON_ROWS = 10;

/**
 * Severity band → chip colour.
 *
 * The queue's `RISK_DOT` palette on a chip instead of a dot — same three tokens,
 * same meanings, because a claim's band must look the same wherever it is drawn
 * (UX notes: "no new colours"). A `Record<RiskBand, string>` rather than a
 * lookup with a fallback, so a fourth band would fail the build here, beside the
 * other two maps, instead of rendering an unstyled chip next to a worker's name.
 */
const SEVERITY_TONE: Record<RiskBand, string> = {
  high: "border-error/40 bg-error-soft text-error",
  med: "border-warn/40 bg-warn-soft text-warn",
  low: "border-ok/40 bg-ok-soft text-ok",
};

/**
 * Stage → pill colour, the prototype's `.stag` palette.
 *
 * `ClaimCard`'s `STAGE_PILL`, restated here rather than imported: that constant
 * is module-private to the queue card and exporting it would make a queue
 * card's styling a shared contract two features have to agree on. The *labels*
 * are imported (`STAGE_LABEL` is already the shared vocabulary); only the four
 * class strings are local.
 */
const STAGE_PILL: Record<PriorityClaimRow["stage"], string> = {
  intake: "bg-steel-soft text-steel",
  investigation: "bg-warn-soft text-warn",
  treatment: "bg-ok-soft text-ok",
  settled: "bg-surface-2 text-faint",
};

/** First occurrence of each claim id wins — the base page before its pages. */
function dedupe(rows: PriorityClaimRow[]): PriorityClaimRow[] {
  const seen = new Set<string>();
  const unique: PriorityClaimRow[] = [];
  for (const row of rows) {
    if (seen.has(row.claimId)) continue;
    seen.add(row.claimId);
    unique.push(row);
  }
  return unique;
}

function Chip({ tone, children }: { tone: string; children: React.ReactNode }) {
  return <span className={`${CHIP_CLASS} ${tone}`}>{children}</span>;
}

/**
 * The Status cell: a LITIG chip on a litigated claim, the stage pill otherwise.
 *
 * The prototype's own rule, ported exactly — a litigated claim shows *only* the
 * LITIG chip, and its stage is not drawn beside it. Both facts arrive on the
 * row; which of them is displayed is a presentation decision, which is why the
 * server sends `stage` and `litigationFlag` rather than a pre-resolved chip.
 *
 * The badge vocabulary is `ClaimCard`'s: `bg-error-soft text-error` and
 * `title="In litigation"`, so the chip a supervisor sees on this table is the
 * chip a handler sees on her queue card, down to the hover text.
 */
function StatusCell({ row }: { row: PriorityClaimRow }) {
  if (row.litigationFlag) {
    return (
      <span
        data-testid="priority-litig"
        title="In litigation"
        className="inline-block rounded-[2px] bg-error-soft px-[6px] py-[2px] text-[9px] font-bold text-error"
      >
        LITIG
      </span>
    );
  }
  return (
    <span
      className={`inline-block rounded-[2px] px-[6px] py-[2px] text-[9px] font-bold ${STAGE_PILL[row.stage]}`}
    >
      {STAGE_LABEL[row.stage]}
    </span>
  );
}

interface ColumnSpec {
  /** `data-testid` stem for the cell, and the React key for the column. */
  key: string;
  header: string;
  /** `text-left` for names and prose, `text-center` for figures and chips. */
  align: string;
  cell: (row: PriorityClaimRow) => React.ReactNode;
}

/**
 * The ten columns, in the prototype's order (`renderSV`, line 1114).
 *
 * Declared once as data rather than laid out twice in JSX, for
 * `HandlerBenchmarkTable`'s and `DashboardPage`'s reason: the order *is* the
 * design contract, so "the columns render in this order" should be a list a
 * reviewer can check against the prototype line by line — and the header row
 * and the body row are then generated from one array and cannot drift apart.
 */
const COLUMNS: readonly ColumnSpec[] = [
  {
    key: "claim-id",
    header: "Claim ID",
    align: "text-left",
    // Monospace, the prototype's JetBrains Mono cell — a business id is read
    // character by character and compared against others in the column.
    cell: (row) => (
      <Link
        data-testid="priority-claim-link"
        data-claim-id={row.claimId}
        to={claimHref(row.claimId)}
        // This row lives on the dashboard, not in a filtered list, so that is
        // where its claim view's Back belongs — sending it to the unfiltered
        // drill list would answer a question nobody asked.
        state={{ from: DASHBOARD_ROUTE } satisfies DrillOrigin}
        className="rounded font-mono text-faint hover:text-text hover:underline focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
      >
        {row.claimId}
      </Link>
    ),
  },
  {
    key: "worker",
    header: "Worker",
    align: "text-left",
    cell: (row) => <span className="font-semibold">{row.worker}</span>,
  },
  {
    key: "employer",
    header: "Employer",
    align: "text-left",
    // `employerShortName` — the joined `employer.short_name` column, not a
    // client-side `.split(" ")[0]` of the full name, which is what the
    // prototype does in this cell and is a canonicalization AD-1 removes.
    cell: (row) => <span className="text-muted-text">{row.employerShortName}</span>,
  },
  {
    key: "injury",
    header: "Injury Type",
    align: "text-left",
    cell: (row) => (
      <span className="block max-w-[130px] truncate" title={row.injuryType}>
        {row.injuryType}
      </span>
    ),
  },
  {
    key: "severity",
    header: "Severity",
    align: "text-center",
    cell: (row) => (
      <Chip tone={SEVERITY_TONE[row.severityBand]}>{RISK_LABEL[row.severityBand]}</Chip>
    ),
  },
  {
    key: "fraud-score",
    header: "Fraud Score",
    align: "text-center",
    // **Tinted by the flag, never by comparing the score with a cut-off.** The
    // prototype writes `c.fraudScore>=55?ER:c.fraudScore>=35?WN:OK` straight
    // into the style attribute — two thresholds decided in the browser, one of
    // which is a real JDM parameter (`fraudFlagScoreMin`, published on this very
    // payload) and the other of which appears in no rule document at all. The
    // server sends the flag it counted the Fraud Flags card with, and this cell
    // reads it: two tones instead of three, and no band decided here.
    cell: (row) => (
      <span
        className={`font-mono font-bold ${row.fraudFlagged ? "text-warn" : "text-muted-text"}`}
      >
        {row.fraudScore}
      </span>
    ),
  },
  {
    key: "handler",
    header: "Handler",
    align: "text-left",
    // The whole name. The prototype prints `c.handler.split(" ")[0]`, which
    // collapses two handlers sharing a first name into one label — and the
    // column exists so a supervisor knows whose desk a claim is on.
    cell: (row) => <span>{row.handlerName}</span>,
  },
  {
    key: "days-open",
    header: "Days Open",
    align: "text-center",
    // Monospace, matching the queue card's `{card.daysOpen}d`. The unit is in
    // the header rather than the cell, which is the prototype's own choice for
    // this table.
    cell: (row) => <span className="font-mono">{row.daysOpen}</span>,
  },
  {
    key: "next-action",
    header: "Priority Next Best Action",
    align: "text-left",
    cell: (row) => (
      <span className="block max-w-[220px] truncate" title={row.nextBestAction}>
        {row.nextBestAction}
      </span>
    ),
  },
  {
    key: "status",
    header: "Status",
    align: "text-center",
    cell: (row) => <StatusCell row={row} />,
  },
];

/**
 * The prototype's heading, with both of its numbers quoted from the response.
 *
 * "showing top 30 of 100" in the prototype, where both figures are the whole
 * dataset's and neither is scoped. Here `cap` is the rule document's answer and
 * `total` is the size of the population *before* the cap, both computed over the
 * caller's book — so a scoped supervisor is told how much of *her* portfolio
 * qualified, and superseding `worklist_actions` moves the row count and this
 * sentence together with nothing deployed.
 *
 * Rendered from the payload or not at all: with no data there is no honest
 * number to show, and a caption reading "top — of —" is not a sentence. The
 * heading itself is always present, so the section never loses its label.
 *
 * **Which sentence, on `truncated` rather than on `total > cap`.** A scoped
 * supervisor with eight qualifying claims must not be told "showing top 30 of
 * 8" — a cap that cut nothing, quoted as though it had, promising thirty rows
 * beside eight. Deciding that here would mean comparing two rule-decided
 * numbers in the browser, which is the client-side rule evaluation AD-1 forbids
 * and `noDerivation.test.ts` catches by name. So the server, which holds both
 * numbers, sends the answer instead.
 */
function Caption({ data }: { data: PriorityClaims | undefined }) {
  if (data === undefined) return null;
  return (
    <span data-testid="priority-caption" className="font-normal text-faint normal-case">
      {" "}
      {data.truncated ? `(showing top ${data.cap} of ${data.total})` : `(${data.total} claims)`}
    </span>
  );
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: SKELETON_ROWS }, (_, index) => (
        <tr key={index} data-testid="priority-row-skeleton" aria-hidden>
          <td colSpan={COLUMNS.length} className="py-[7px]">
            <span className="block h-[14px] animate-pulse rounded bg-surface-2" />
          </td>
        </tr>
      ))}
    </>
  );
}

export function PriorityClaimsTable({
  data,
  isPending,
  isError,
}: {
  /** The server's first page, or `undefined` while it is unknown. */
  data: PriorityClaims | undefined;
  isPending: boolean;
  isError: boolean;
}) {
  /**
   * **One predicate for `aria-busy` and for the skeleton rows.**
   *
   * `HandlerBenchmarkTable`'s idiom and its argument: these were two facts
   * before — the section was busy when `isPending`, the rows were placeholders
   * when `data === undefined` — and they agree in every state TanStack Query
   * produces today, which is precisely why any drift would be silent. The
   * conjunction rather than either half, so neither a query status without a
   * payload nor a payload without a status can put the two branches out of step.
   */
  const isLoading = isPending && data === undefined;

  const [expanded, setExpanded] = useState(false);
  const queryClient = useQueryClient();
  const firstCursor = data?.nextCursor ?? null;

  // **An expansion belongs to the cursor it was opened against** (Story 9.8).
  //
  // The infinite query is keyed on `firstCursor`. When the base query refetches
  // onto a different one — a staleness refetch, a window refocus, an
  // invalidation after an edit — the component swings onto a fresh, empty cache
  // entry while `expanded` is still `true`, and `enabled: expanded && …` fires a
  // page-two request the supervisor never clicked. There is no `useEffect` and
  // no `IntersectionObserver` here; the eager fetch is the cache key moving
  // underneath a boolean that outlived it.
  //
  // Adjusting state during render rather than in an effect, which is React's
  // documented way to reset state on a prop change: the re-render happens before
  // anything commits, so no effect from the discarded pass runs and no request
  // goes out. `isExpanded` is the value read *this* pass, so the hook below sees
  // `false` immediately rather than one render later.
  const [expandedFor, setExpandedFor] = useState<string | null>(firstCursor);
  const cursorMoved = expandedFor !== firstCursor;
  if (cursorMoved) {
    setExpandedFor(firstCursor);
    setExpanded(false);
  }
  const isExpanded = expanded && !cursorMoved;

  const pages = usePriorityClaimPages(firstCursor, isExpanded);

  // Gated on `isExpanded` rather than merely on whether the entry has data: an
  // expansion that expired (the base query refetched onto a new first cursor)
  // leaves its pages in the cache, and rendering them would show rows the
  // supervisor never asked to see, cut from a ranking that no longer applies.
  const extra: PriorityClaimRow[] = isExpanded
    ? (pages.data?.pages.flatMap((page) => page.items) ?? [])
    : [];
  // Deduped by claim id, because the two sources can overlap: the base query
  // refetches (staleness, a window refocus) while accumulated pages sit beside
  // it, and a row in both would be two React children with one key — a warning
  // at best and a dropped row at worst.
  const rows = dedupe([...(data?.items ?? []), ...extra]);
  // Before the first "Show more" the server's first-page cursor is the
  // authority; afterwards the infinite query's own `hasNextPage` is — but only
  // once it has answered. `hasNextPage` is false while the first fetch is in
  // flight, so reading it too early unmounts the button on the click that
  // triggered it, and its disabled and "Loading…" states become unreachable.
  const hasMore = isExpanded && pages.isSuccess ? pages.hasNextPage : firstCursor !== null;

  function reload() {
    // Three steps, and each is needed — `StageGroup.reload`'s argument, over
    // this list. Drop the accumulated pages, or the rejected cursor is still the
    // entry's last page param. Collapse, or the query re-enables against it
    // immediately. Invalidate the base query, because the cursor it handed out
    // is the one that was refused — only a fresh first page can produce one the
    // server will accept. Retrying is deliberately not offered: it would replay
    // the same rejected cursor for ever.
    queryClient.removeQueries({
      queryKey: queryKeys.dashboard.priorityClaimPages(firstCursor),
    });
    setExpanded(false);
    void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.priorityClaims });
  }

  return (
    <section
      data-testid="priority-claims"
      aria-labelledby="priority-claims-heading"
      // Its own busy state as well as the page's: this section can be loading
      // while the three above it have landed, and a screen reader told the whole
      // dashboard was busy would be describing a screen nobody sees.
      aria-busy={isLoading}
      className="mb-[14px] rounded-lg border border-border bg-surface p-3"
    >
      <h3
        id="priority-claims-heading"
        className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase"
      >
        Priority claims — active treatment, litigation &amp; fraud flags
        <Caption data={data} />{" "}
        {/* The same population, **without the cap** — `filter[priority]` is the
            worklist's own predicate, imported by the server rather than
            restated, so this link opens all 38 of a book whose table shows the
            top 30. That is the one place on this dashboard where the list is
            deliberately longer than the surface that opened it, and it is why
            the caption quotes both numbers. */}
        <Link
          data-testid="priority-view-all"
          to={drillHref({ priority: "true" })}
          className="rounded font-sans text-[10px] font-semibold text-steel normal-case hover:underline focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
        >
          View all →
        </Link>
      </h3>

      {isError && rows.length === 0 ? (
        // Inline, never a dialog (NFR-3), and in place of the table rather than
        // above an empty one: a headless table reads as "nothing in this
        // portfolio needs attention", which is a different and much quieter lie.
        //
        // **`rows.length === 0` is Story 9.8's addition, and it is the whole
        // fix.** TanStack retains the last successful `data` through a failed
        // refetch, so this branch used to replace rows the supervisor had
        // already walked — up to a whole worklist — with one sentence, and
        // destroy the accumulated pages with them. A refetch that fails while
        // there is something on screen is a *warning*; only a load that has
        // produced nothing at all is a full-height alert. The inline warning
        // below the table is the other half.
        <p
          role="alert"
          data-testid="priority-claims-error"
          className="rounded-md border border-border bg-error-soft px-3 py-2 text-[11.5px] font-semibold text-error"
        >
          ⚠ Priority claims could not be loaded. Try again in a moment.
        </p>
      ) : data !== undefined && rows.length === 0 ? (
        // The empty state (NFR-3). Reachable: a scoped supervisor whose book has
        // nothing in treatment, no fraud flag and no litigation has an empty
        // worklist, which is a fact about her portfolio rather than a failure —
        // and is the one state on this dashboard that is genuinely good news.
        <p data-testid="priority-claims-empty" className="text-[11.5px] text-faint">
          No claim in this portfolio needs priority attention.
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[11.5px]">
              <caption className="sr-only">
                Claims in active treatment, litigation or under a fraud flag,
                ranked by the same priority score as the handler queue
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
                  rows.map((row) => (
                    // Keyed on the business id, which is unique by construction
                    // and is what the server deduped the population on — never
                    // on the map index, which would re-key every row when a page
                    // is appended.
                    <tr
                      key={row.claimId}
                      data-testid="priority-row"
                      data-claim-id={row.claimId}
                      className="border-b border-hairline last:border-b-0"
                    >
                      {COLUMNS.map((column) => (
                        <td
                          key={column.key}
                          data-testid={`priority-${column.key}`}
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

          {isError && (
            // The non-destructive half of the pair above (Story 9.8): the base
            // query failed while rows are on screen, so the rows stay and this
            // says the figures may be behind. `role="alert"` because it appears
            // after a state a reader was not watching for; below the table
            // rather than above it, so nothing shifts under the pointer of
            // somebody mid-scroll.
            <p
              role="alert"
              data-testid="priority-claims-stale"
              className="px-1 py-2 text-[11px] text-error"
            >
              ⚠ These rows could not be refreshed and may be out of date.
            </p>
          )}

          {pages.isError ? (
            <>
              <p
                role="alert"
                data-testid="priority-claims-page-error"
                className="px-1 py-2 text-[11px] text-error"
              >
                ⚠ The rest of this worklist could not be loaded.
              </p>
              <button
                type="button"
                data-testid="priority-claims-reload"
                onClick={reload}
                className="w-full border-t border-border px-1 py-2 text-[11px] font-semibold text-steel hover:bg-surface-2"
              >
                Reload this section
              </button>
            </>
          ) : (
            hasMore && (
              <button
                type="button"
                data-testid="priority-claims-more"
                disabled={pages.isFetchingNextPage || pages.isFetching}
                onClick={() => {
                  // The first click turns the infinite query on, which fetches
                  // `initialPageParam` — the cursor the section already holds.
                  // Later clicks ask it for one more.
                  if (!isExpanded) setExpanded(true);
                  else void pages.fetchNextPage();
                }}
                className="w-full border-t border-border px-1 py-2 text-[11px] font-semibold text-steel hover:bg-surface-2 disabled:opacity-60"
              >
                {pages.isFetching ? "Loading…" : "Show more"}
              </button>
            )
          )}
        </>
      )}
    </section>
  );
}
