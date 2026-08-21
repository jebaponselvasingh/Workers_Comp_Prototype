/**
 * Story 2.1 AC 2 and Story 2.2 AC 5, structurally — **no derivation exists
 * in TypeScript.**
 *
 * The acceptance criterion does not say "the cards happen to be correct";
 * it says the scoring, banding, counting and flag logic is *absent* from the
 * components that render the queue, and asks for that to be asserted. Every
 * other test in this directory checks that the pane renders what the server
 * sent, which is a different claim: a component that also computed something
 * on the side would pass all of them.
 *
 * This is the web twin of the server's
 * `test_no_module_outside_the_registry_hardcodes_the_band`, and it is blunt
 * in the same way on purpose. It reads the sources as text and fails on a
 * pattern, so the fix is either to delete the arithmetic or to justify an
 * entry below — a choice a reviewer sees rather than infers.
 *
 * **What it scans, and why the answer is not "one directory".** A guard
 * whose failure mode is "silently covers less than it says" is worse than no
 * guard, because the green tick reads as coverage. This one used a
 * non-recursive `readdirSync` over `features/queue/` alone, which missed
 * `features/shell/WorkspaceShell.tsx` — the file where this very story put
 * its reasoning about the queue payload. The roots below are declared, walked
 * recursively, and asserted non-empty, so a directory that moves or empties
 * fails loudly instead of passing vacuously.
 *
 * **What it does not scan.** Comments and string literals are stripped
 * first: the prose in these files argues about thresholds and the marker
 * rule at length (it has to — that is where a reader learns why none of it
 * is here), and Tailwind class names are full of numbers. Test files are
 * excluded for the reason the server excludes `tests/`: they are the
 * independent oracle and are allowed to restate a rule in order to disagree
 * with it.
 */
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "vitest";

const SRC_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

/**
 * The directories that render or reason about the queue payload. Named
 * rather than "all of `src`" so the guard stays a statement about this
 * story's surface: `api/queryClient.ts` legitimately compares an HTTP status
 * to 500, and a guard that had to grow an allowlist for unrelated code is a
 * guard the next person turns off.
 */
const ROOTS = [
  "features/queue",
  "features/shell",
  "features/claim-detail",
  // Story 4.1's two. The diary is the newest place a component is handed a
  // rule's answer beside the facts it was computed from: a meeting card
  // carries `status` *and* `meetingDate` *and* `isDone`, and the prototype
  // writes `m.date >= today && !m.done` inside the function that draws the
  // card (line 1898). That comparison is exactly what this guard refuses, and
  // a scan that stopped at Epic 3's surfaces would not have seen it.
  "features/diary",
  "features/copilot",
  // Story 5.1's. The supervisor dashboard is handed twelve server-computed
  // figures at once — ten card values and the chip's two counts — which is
  // exactly the shape a component starts totalling: the prototype's `renderSV`
  // does ten `filter`/`reduce` passes over the claim array to produce these
  // very numbers, and porting it "just for the chip" is one line. It is also
  // the first surface whose *captions* quote rule thresholds, so a constant
  // named `HIGH_RISK_MIN` here would look like formatting and be a second copy
  // of a JDM parameter the browser cannot see change.
  "features/dashboard",
];

/**
 * Individual files outside those directories that own a derived payload.
 *
 * **`src/api/` is not a root, and `src/api/meetings.ts` is.** The whole
 * directory cannot be scanned: `api/queryClient.ts` legitimately compares an
 * HTTP status to 500 and `api/fieldLimits.ts` legitimately names three column
 * widths — a guard that had to grow an allowlist for those is a guard the next
 * person turns off, which is the reason recorded above.
 *
 * But "components only" was a hole with a name on it. `useMeetings` owns the
 * meetings payload *and already has a `select`*, which makes it the single most
 * plausible home for a re-derived `status`: planting the prototype's
 * `meetingDate >= today && !isDone` inside that `select` left all five guards
 * below green, because the file the rule would live in was not read. The rule
 * would then be one function away from every diary surface at once, and the
 * components this guard does scan would look innocent.
 *
 * A file joins this list when it holds a `select`, a transform or an
 * accumulation over a payload carrying a `DERIVED_FIELDS` member.
 */
const ROOT_FILES = ["api/meetings.ts", "api/emails.ts"];

/**
 * Source with comments and literal text removed, so the patterns below see
 * code and only code.
 *
 * A single left-to-right pass rather than a chain of regexes, because the
 * order the two are removed in changes the answer and the chain had it
 * backwards: line comments went first, so a `//` *inside a string* — a URL,
 * a regex written as text — swallowed the rest of its line, and anything
 * after it on that line was never scanned. Whichever construct opens first
 * wins here, which is what a tokenizer does and what JavaScript itself does.
 *
 * Template literals keep their `${…}` expressions — that is real code, and
 * `${score > 30 ? …}` in a class name would still be a derivation — while
 * their literal segments are dropped like any other string.
 */
export function code(source: string): string {
  let out = "";
  let i = 0;

  /** Consume a quoted string starting at `i`, emitting a placeholder. */
  function quoted(quote: string): void {
    i += 1;
    while (i < source.length && source[i] !== quote) {
      if (source[i] === "\\") i += 1;
      i += 1;
    }
    i += 1;
    out += '""';
  }

  /** Consume a template literal, keeping only its `${…}` expressions. */
  function template(): void {
    i += 1;
    out += '""';
    while (i < source.length && source[i] !== "`") {
      if (source[i] === "\\") {
        i += 2;
        continue;
      }
      if (source[i] === "$" && source[i + 1] === "{") {
        out += " ";
        i += 2;
        // Nested braces, strings and templates all count, so the
        // expression ends at *its* closing brace rather than the first one.
        let depth = 1;
        while (i < source.length && depth > 0) {
          const c = source[i];
          if (c === "{") depth += 1;
          else if (c === "}") depth -= 1;
          if (depth === 0) break;
          if (c === '"' || c === "'") {
            quoted(c);
            continue;
          }
          if (c === "`") {
            template();
            continue;
          }
          out += c;
          i += 1;
        }
        i += 1;
        out += " ";
        continue;
      }
      i += 1;
    }
    i += 1;
  }

  while (i < source.length) {
    const c = source[i];
    if (c === "/" && source[i + 1] === "*") {
      const end = source.indexOf("*/", i + 2);
      i = end < 0 ? source.length : end + 2;
      out += " ";
      continue;
    }
    if (c === "/" && source[i + 1] === "/") {
      const end = source.indexOf("\n", i);
      i = end < 0 ? source.length : end;
      out += " ";
      continue;
    }
    if (c === '"' || c === "'") {
      quoted(c);
      continue;
    }
    if (c === "`") {
      template();
      continue;
    }
    out += c;
    i += 1;
  }
  return out;
}

/**
 * Every payload field whose value is a *rule's answer* rather than a fact.
 *
 * The counts belong here beside the flags, and their absence was a real
 * hole: `total`, `unfilteredTotal` and `filteredTotal` are the numbers the
 * client was summing and subtracting to decide which empty message to show
 * and what to write on "Show more" — a derivation over a payload, done in
 * the browser, which is exactly what the guard exists to refuse. `nextCursor`
 * is here for completeness: it is an opaque token, and any arithmetic on one
 * means somebody has started computing an offset.
 */
const DERIVED_FIELDS =
  "priorityScore|priorityMarker|severityScore|fraudScore|daysOpen|risk|" +
  "siuReview|rtwBlocked|paymentDue|fraudFlag|litigationFlag|surgeryRequired|" +
  "total|unfilteredTotal|filteredTotal|nextCursor|" +
  // Story 2.2's derived payload values. The percentages and the expected
  // window are the ones a component is most tempted to "just" recompute —
  // `expectedDays` from the recovery text, a share from two cent amounts —
  // and both are registered derivations with rule-document parameters.
  "phase|coordinationStatus|expectedDays|indemnityPct|medicalPct|expensePct|" +
  "totalPaidCents|timelineTruncated|" +
  // Story 2.4's. `band` is the marker's severity band and the single most
  // tempting thing on this list to recompute: the prototype's `injHTML`
  // does exactly that, inside the function that draws the marker, from a
  // cut-off pair that appears in no rule document. Here it arrives decided.
  "band|" +
  // Story 2.6's. `count` is the Photos tab label's number, and the prototype
  // writes `c.photos.length` into that label from the same global object its
  // grid maps. Here the label and the grid are different components, so a
  // browser-side length is a second answer to "how many photos does this claim
  // have" — and `hasBlob` is on the list beside it because inferring it from
  // `blobUrl === null` is the same mistake in the other direction: a
  // volume-backed store answers null for a photo that exists.
  "count|hasBlob|" +
  // Story 3.1's. `weeklyCents` is the most tempting recomputation in the
  // console — an AWW and a percentage are both on screen — and `isOverridden`
  // is the one a client would most plausibly *infer*, by comparing the two
  // rates, which answers wrongly for a handler who typed the default back in.
  // `indemnityType` is a four-way classification with a rules-tier cut-off
  // behind it. The comp rate's *bounds* are deliberately absent from this
  // list, exactly as `severityMin`/`severityMax` are: they are served so the
  // input can pre-flight refuse, and comparing against them is the intended
  // use rather than a second rule.
  "weeklyCents|compRateBp|defaultCompRateBp|isOverridden|indemnityType|" +
  // Story 3.2's. `verdict` is the reserve adequacy answer and the three cent
  // figures are what it was computed from — all four on the same card, one
  // subtraction apart from a second opinion. The prototype's `reserveCheck`
  // does exactly that comparison in the browser, against band constants that
  // appear in no rule document, which is the failure this entry names.
  // `ratioBp` is here too because it is the most inviting: a component that
  // divided the exposure by the reserve to draw a bar would be re-deriving the
  // number the server already published, with its own rounding.
  "verdict|ratioBp|projectedRemainingCents|remainingIndemnityCents|" +
  "remainingMedicalCents|" +
  // Story 3.3's. This tab hands a component a totals row *and* the rows it
  // totals, which is the strongest invitation to arithmetic anywhere in the
  // console: `paidCents` and `totalCents` sit beside a list whose amounts add
  // up to them, and `installmentsPaid` beside a schedule a component could
  // just count. Each is a registered derivation, and each has a rule behind it
  // that a re-implementation would get wrong — `paidToDateCents` falls back
  // from the `paid_*` columns to the live figures on a rule the browser cannot
  // see, `installmentsPaid` counts rows rather than dividing (paid weeks keep
  // the amount they were paid at), and `nextPaymentDue` skips two statuses.
  // `weekCount` is here because it is *not* the projection's week count: a
  // shortened schedule keeps its decided weeks, so `schedule.length` is the
  // answer and a recomputation from the recovery window is not.
  "paidToDateCents|totalClaimProjectedCents|paidIndemnityCents|paidMedicalCents|" +
  "paidExpenseCents|paidFromColumns|installmentsPaid|weekCount|nextPaymentDue|" +
  "billsOnFile|scheduledIndemnityCents|disbursedIndemnityCents|paidCents|totalCents|" +
  // Story 3.4's two. `approvable` is the server's answer to whether the ✓
  // button belongs on a row, and it is the single most tempting thing on this
  // list to reconstruct — `status === "pending_approval"` looks like the whole
  // rule and is not: a week is approvable from three statuses and a line item
  // from exactly one, so a browser deciding it would offer a button the
  // command refuses. `nextBatchDate` is the disbursement calendar, computed
  // from a *deployment* cadence the SPA has never been told; "the next
  // Tuesday" worked out here would be right until somebody changed
  // `PAYMENT_BATCH_WEEKDAYS`, and then silently wrong on a date a handler is
  // told to expect money on.
  "approvable|nextBatchDate|" +
  // Story 3.5's. The whole checklist is a rule's answer, and three of these
  // are the ones a component would most plausibly reconstruct: `urgency` is
  // the chip *and* the ranking, so comparing two of them is one line from
  // re-sorting a list the server ordered; `cap` and `paddingFloor` are the
  // card's length, which the prototype decides in the browser with a `budget`
  // counter (line 1241) and which is a rule document's number here; and
  // `enabled` is which epics have shipped — a browser deciding that would be a
  // second copy of the seam table, stale in exactly the release where one of
  // them landed. `command` is on the list beside it because "reviewed but not
  // confirmed" looks like the whole rule and is not.
  "urgency|cap|paddingFloor|rulesVersion|enabled|disabledReason|command|" +
  // Story 2.5's. `path` is the claim's statutory classification and is the
  // single most consequential derived value in the console — it decides which
  // death-benefit forms a handler is shown — so a comparison against it, or
  // any arithmetic near it, is a browser deciding a regulatory question.
  "path|" +
  // Story 4.1's. Adding `features/diary` to the roots above was only half the
  // guard: the rule the comment there describes — `m.date >= today && !m.done`
  // inside the function that draws the card — matches nothing unless the field
  // names themselves are on this list, because the only comparison rule that
  // fires without them needs a numeric *literal* on the right and `today` is
  // not one. `status` is the derivation's answer; `meetingDate`, `meetingTime`
  // and `isDone` are the three facts it was computed from, all three on the
  // same payload one line away from being recombined into a second copy of it.
  "status|isDone|meetingDate|meetingTime|" +
  // Story 4.2's. `upcomingCount` is the greeting's "📅 N upcoming meetings",
  // and it is the single most tempting number in the diary to reconstruct: the
  // summary right beneath it renders a list of meetings each carrying
  // `status`, so `items.filter(m => m.status === "upcoming").length` looks like
  // the same answer and is not — the count is over the **whole book** and the
  // list is one filtered day of it. It is also what a component would reach for
  // to decrement after a ✓ Done, which is the other half of the same mistake.
  // `notedAt` joins the field list because a note's header is formatted from
  // it and a comparison against it would be a client deciding recency — the
  // rule that closes the diary check-in lives in `services/worklist`.
  "upcomingCount|notedAt|" +
  // Story 4.3's two. `sentAt` is the "Sent" badge's instant and the sent log's
  // **sort key**, which is the reason it belongs here rather than beside
  // `createdAt`: a browser that compared two of them would be re-deciding an
  // ordering the server publishes, and one that compared one against `now`
  // would be inventing a delivery state for a row that records composition and
  // nothing else. `priority` is the card's accent *and* a value a component
  // would plausibly rank by — three tones is one comparison away from a sort —
  // and it is a stored enum rather than a scale.
  "sentAt|priority|" +
  // Story 5.1's twelve. Every one is a *count over a scoped set the browser
  // does not hold*, which is the strongest form of this guard's argument: the
  // SPA has never seen the hundred claims, so any arithmetic on these is not a
  // second opinion, it is a guess. `highRisk` is the most rule-laden of them —
  // it is a band of `severityScore` decided by a JDM document, and the card
  // beside it publishes that document's cut-off, so a component holding both
  // is one comparison away from re-banding the portfolio itself.
  // `employerCount` and `plantCount` are the most tempting — they read like
  // chip decoration rather than like figures — and the prototype writes both as
  // literals that are wrong for every persona. `highRiskSeverityMin` and
  // `fraudScoreMin` are here for the other reason: they are rule-document
  // values the captions quote, so a comparison against one would be the browser
  // re-deciding a band the server already banded. (`totalPaidCents` is covered
  // by Story 3.x's entry and `surgeryRequired` by `FLAGS`, so neither repeats
  // here.)
  "totalClaims|underTreatment|settledClosed|highRisk|totalReserveCents|" +
  "fraudFlagged|oshaRecordable|litigation|employerCount|plantCount|" +
  "highRiskSeverityMin|fraudScoreMin|" +
  // Story 5.2's. A whole ranked table of them, and the pull is stronger than
  // anywhere else in the console because the browser is handed a *list* it
  // could plausibly re-order: `rank` and `compositeDays` are one `.sort()`
  // away from a client-side ranking of a list the server ordered under a rules
  // version it has never seen, and `cycleSpeedPct` is one division away from a
  // peer ratio recomputed against whichever rows happen to be on screen.
  // `deviationPct`, `complexityScore`, `complexityBand` and `cycleStatus` are
  // the four a component would most plausibly *re-band*: the response carries
  // the deviation, the score and the four cut-offs that produced the two chips,
  // so all the material for a second opinion is on the same object.
  // `portfolioCompositeDays` is on the list beside them because it is the
  // denominator every deviation was computed from — dividing by it in the
  // browser is the recomputation, not a formatting choice.
  //
  // **`rank` is first on the list and is the one that matters most.** Rendering
  // `{index + 1}` in the `#` column instead of `row.rank` is the single most
  // likely way to get this table wrong: it looks identical on every fixture
  // where the server ranked every row, and it silently invents a ranking the
  // moment one row cannot be ranked — the response publishes `rank: null`
  // alongside `compositeDays: null` precisely so the column can decline to
  // answer. Being honest about the limit: a literal `{index + 1}` mentions no
  // payload field at all, so no textual guard can see it — what this entry buys
  // is that every *other* shape of the same mistake fails the scan (`.sort()`
  // on `rank`, `row.rank - 1`, a comparison against a rank), and the component
  // test pins the rendered numbers to the fixture's so the index version fails
  // there. A guard that claimed more than it does would be worse than one that
  // says where it stops.
  "rank|handlerName|caseCount|cycleSpeedPct|compositeDays|rtwPct|" +
  "complexityScore|complexityBand|pendingApprovals|deviationPct|cycleStatus|" +
  "portfolioCompositeDays|onTrackDeviationPctMax|attentionDeviationPctMin|" +
  "complexityHighMin|complexityMedMin|leader|laggard|" +
  // Story 5.3's. Six finished distributions and the four fields that describe
  // each one, and the pull here is different in kind from 5.2's: the browser is
  // handed a *series* together with the total it was counted out of and the
  // number of categories it was cut from, which is every ingredient of a
  // percentage, an "other" bucket and a re-ranking, on one object.
  //
  // - The six series names are on the list because a `.filter` or a `.sort`
  //   over one of them is the shape the guard's fourth and sixth rules catch,
  //   and neither fires without the field name.
  // - `totalCategories`, `truncated` and `limit` are the truncation contract.
  //   `limit` is guarded as `\.limit` — the *property* — not as a bare word.
  //   A bare `limit` would fail the build on ordinary pagination: `offset +
  //   limit` and `limit - 1` are page arithmetic, not a band cut-off, and
  //   `queryKeys`/list surfaces are free to write them. Guarding the property
  //   access still catches the thing worth catching, which is a component doing
  //   arithmetic on the server's published cut.
  //   `series.totalCategories - series.limit` is one line and reads as
  //   arithmetic on a caption; it is in fact the client deciding how much of a
  //   scope it cannot see was left out, which is a question only the server can
  //   answer — the categories beyond the cut were never sent.
  // - `paidCents` is the money on the employer bars. Dividing it by the
  //   series' `total` to draw a share is the single most plausible
  //   recomputation on this page, and `total` is already on this list.
  // - `medRiskSeverityMin` joins `highRiskSeverityMin` from 5.1 for that
  //   field's reason: it is a rule-document value the severity legend sits
  //   beside, so a comparison against it would be the browser re-banding a
  //   portfolio the server already banded.
  // - `employerLabel` is the server-side projection's name for the joined
  //   `employer.short_name`; the wire spells it `label`, which is far too
  //   common a word in this codebase to put on a textual guard. It is listed so
  //   the guard still fires if a later story ships the field under its
  //   server-side name.
  "byStage|bySeverity|byRecoveryStatus|byInjuryType|byEmployer|byState|" +
  "paidCents|totalCategories|truncated|\\.limit|medRiskSeverityMin|employerLabel|" +
  // Story 5.4's. A ranked, capped, cursor-paged list of claims — 5.2's pull and
  // 5.3's pull at once, on the one surface that has both a server-decided order
  // and a server-decided cut.
  //
  // - `nextBestAction` is the deterministic generator's top row. A component
  //   that compared two of them, or partitioned rows by one, would be re-doing a
  //   ranking `services/worklist/actions.py` made total *for this column*.
  // - `severityBand` is the chip, and it is the band rather than the score
  //   precisely so nothing here can re-band it — but `highRiskSeverityMin` is on
  //   the same payload, so the material for a second opinion is one object away.
  // - `fraudFlagged` is the Fraud Score cell's tint, and it is the single most
  //   tempting entry on this list: `fraudScore` and `fraudFlagScoreMin` are both
  //   on the row's own payload, so `row.fraudScore >= data.fraudFlagScoreMin` is
  //   one line, reads like formatting, and is the browser re-deciding the rule
  //   the Fraud Flags card was counted with. The prototype writes exactly that
  //   comparison — twice, against two cut-offs, one of which is in no rule
  //   document at all. Like `cap`, it is **not repeated below**: Story 5.1 put
  //   it on this list for the Fraud Flags card, and one token guards both.
  // - `truncated` is the caption's branch, and it is a boolean on the wire for
  //   exactly the reason the rest of this list exists: `total > cap` in the
  //   browser is a rule comparison wearing a formatting costume, and the server
  //   sends the answer so nothing here has to reach for the operator.
  // - `cap` is the worklist's length and a rule document's answer, so
  //   `items.slice(0, data.cap)` or `data.total - data.cap` is a client
  //   re-cutting a population the server capped — and the claims past the cut
  //   were never sent, so the arithmetic would be about rows the browser has
  //   never seen. It is **not repeated below**: Story 3.5 already put `cap` on
  //   this list for the action checklist's row budget, and the token guards both.
  // - `fraudFlagScoreMin` joins 5.1's two thresholds for their reason: it is a
  //   rule-document value on a payload whose cells were decided at it.
  // - `handlerName` is the first per-row named person on any payload, and
  //   grouping or counting rows by one — "how many of these are Kaya's?" — is
  //   the aggregate this table deliberately does not publish. **Not repeated
  //   below either**: Story 5.2 put it on this list for the benchmark table.
  "nextBestAction|severityBand|truncated|fraudFlagScoreMin|" +
  // Story 5.5's two. Read the alternation above before adding to it: 5.4's
  // review caught exactly the mistake of appending tokens that were already
  // alternatives, in a guard block three lines below a comment stating the
  // discipline. `total`, `risk`, `priorityScore`, `priorityMarker`, `daysOpen`
  // and the five queue flags are all already here — a drill row is the queue
  // card's field set, so it adds no field name of its own — and `handlerName`
  // arrived with 5.2. These two are genuinely new.
  //
  // - `appliedFilters` is the server's reading of the URL, and it is the single
  //   most plausible place for a second one: the browser holds the query string
  //   *and* the list the server made of it, so `params.filter(…)` beside it
  //   looks like the same answer and is not — the server ignored what it did not
  //   recognise, and a client re-parsing would draw a chip for a narrowing that
  //   never happened. Counting it to decide whether to show "Clear all" is the
  //   other half, which is why the count comparison in `FilterChips` is written
  //   as an equality.
  // - `handlerId` is the identity a row's drill-through link filters on, and it
  //   is on this list beside `handlerName` for the reason that field is: any
  //   arithmetic on it — an index recovered from it, a comparison against
  //   another — would be the browser treating a surrogate key as a position.
  "appliedFilters|handlerId|" +
  // Story 7.1's four, and the alternation above was read before adding them —
  // 5.4's review caught exactly the mistake of appending tokens that were
  // already alternatives, three lines below a comment stating the discipline.
  // `truncated`, `total`, `limit`, `handlerName`, `handlerId`, `fraudFlagged`
  // and `fraudFlagScoreMin` are all already here; a fraud panel and a rate table
  // add these four names and nothing else.
  //
  // - `fraudBand` is the analyst workspace's whole subject and the single most
  //   tempting entry on this list to reconstruct: the panel publishes
  //   `fraudBandHighMin` and `fraudBandMedMin` beside the distribution, and the
  //   *per-claim* fraud score is on every drill row one route over — so
  //   `row.fraudScore >= data.fraudBandHighMin ? "high" : …` is one line, reads
  //   like formatting, and is the browser re-deciding a rule whose high edge
  //   happens to equal the review threshold today and will not tomorrow. The two
  //   published edges are guarded as *fields* rather than by name for the reason
  //   `medRiskSeverityMin` is: they are rule-document values a caption quotes.
  // - `rateBp` is the flagged rate in basis points, and it is the one field on
  //   this surface with an obvious unit conversion attached. Dividing it by a
  //   hundred in a component is exactly the "unit conversion at a call site"
  //   `lib/rate.ts` exists to prevent — that module is outside the scanned roots
  //   and is the only place allowed to do it.
  // - `flagged` is the numerator every rate was computed from, published beside
  //   its denominator so the thin-bucket case is legible. Both on one row means
  //   `row.flagged / row.claims` is one line and would round differently from
  //   the server. (`claims` is deliberately **not** on this list: it is far too
  //   common a word in this codebase to guard textually — `list.data.claims`,
  //   `claims.length` — and guarding the numerator catches the same division.)
  // - `claimsWithInsight` is the red-flag card's coverage numerator, and
  //   `claimsWithInsight / claimsInScope` is the percentage a caption would
  //   "just" show. The card states both figures instead, which is what makes the
  //   coverage checkable rather than presented.
  "fraudBand|fraudBandHighMin|fraudBandMedMin|rateBp|flagged|claimsWithInsight";

const FLAGS = "siuReview|rtwBlocked|paymentDue|fraudFlag|litigationFlag|surgeryRequired";

interface Forbidden {
  /** What a reader is told when it fires. */
  why: string;
  pattern: RegExp;
}

const FORBIDDEN: readonly Forbidden[] = [
  {
    why: "compares something against a numeric threshold — bands, cut-offs and marker thresholds are JDM parameters the server reads (AD-8)",
    pattern: /[\w)\]]\s*(?:<=|>=|<|>)\s*-?\d/,
  },
  {
    why: "does arithmetic on or compares a derived payload value — every one of them was already decided by services/derivations or services/worklist (AD-1, AD-10)",
    // `(?![/>])` after the comparison operators is not a loosening — it is
    // the one thing the stripper cannot do. JSX *text* is not a string
    // literal, so `code()` leaves it in place, and `>Claim risk</span>`
    // therefore reads as the field `risk` followed by a `<`. That is a
    // false positive on a heading, and the previous fix for it would have
    // been to reword the heading, which is the guard training the code
    // rather than the other way round. A real comparison never has `/` or
    // `>` as its right-hand side, so excluding those two characters costs
    // the check nothing — `phase > 2` and `daysOpen >= 30` still match, as
    // the smell test below asserts.
    // `(?<!=)` before the right-hand form is the second exclusion of the same
    // kind, and the scan of `api/meetings.ts` is what needed it: a fat arrow
    // returning a payload field — `(last) => last.nextCursor ?? undefined` —
    // reads as `> last.nextCursor` to a pattern that cannot see the `=`. That
    // is not a comparison and never was; a real one never has `=` immediately
    // before its operator, so excluding it costs the check nothing, as the
    // smell list below asserts.
    pattern: new RegExp(
      `\\b(?:${DERIVED_FIELDS})\\s*(?:[-+*/%]|[<>]=?(?![/>]))|(?:[-+*/%]|(?<!=)[<>]=?)\\s*\\w*\\.(?:${DERIVED_FIELDS})\\b`,
    ),
  },
  {
    why: "combines two derived flags — a flag derived from other flags is a second computer for a value that must have exactly one (AD-10)",
    pattern: new RegExp(`\\b(?:${FLAGS})\\b[^\\n]{0,40}(?:&&|\\|\\|)[^\\n]{0,40}\\b(?:${FLAGS})\\b`),
  },
  {
    why: "re-orders a list the server ranked — the priority order is a function of a rules version the browser does not have",
    pattern: /\.sort\s*\(/,
  },
  {
    why: "sums a list the server already counted — the totals are on the wire (AD-1)",
    pattern: /\.reduce\s*\(/,
  },
  {
    // The rule that had to be written down when `(?<!=)` removed the arrow's
    // false positive: `items.filter((m) => m.status).length` was being caught
    // *only* because `=>` read as `>`, which is to say by accident, in a place
    // it happened to point at a real defect. Partitioning a list by a derived
    // field is its own mistake — the greeting's count is over the whole book
    // and the summary beneath it is one filtered day, so counting the rows on
    // screen answers a different question that looks like the same one — and it
    // deserves a rule rather than a coincidence. A `.filter` over anything else
    // is ordinary presentation and is left alone (`ClaimCard`'s badge list).
    why: "filters or partitions a list by a derived payload value — the server already answered that question, over data the client does not hold in full (AD-1, AD-10)",
    pattern: new RegExp(`\\.filter\\s*\\([^\\n]{0,80}\\b(?:${DERIVED_FIELDS})\\b`),
  },
  {
    // Story 5.4's rule, and the one shape the five above cannot see. A
    // server-capped list is handed to the browser already cut, and re-cutting
    // it — `rows.slice(0, data.cap)`, `items.slice(0, data.total)` — involves no
    // operator, no comparison and no `.sort`, so every existing pattern reads it
    // as innocent. It is not: the claims past the cut were never sent, so the
    // arithmetic is about rows the client has never seen, and the count it
    // applies is a rule document's answer. Truncating an *array* by a derived
    // value is therefore its own mistake and gets its own rule; `.slice` over a
    // string (`characters.slice(0, SNIPPET_LENGTH)`) mentions no payload field
    // and is left alone, as the innocent list below asserts.
    why: "re-cuts a list the server already capped — the rows past the cut were never sent (AD-1, AD-8)",
    pattern: new RegExp(`\\.slice\\s*\\([^\\n]{0,60}\\b(?:${DERIVED_FIELDS})\\b`),
  },
  {
    why: "names a threshold constant — the numbers live in the rule documents, not in the client",
    pattern: /\b(?:const|let)\s+\w*(?:THRESHOLD|Threshold|MIN|Min|MAX|Max|WEIGHT|Weight)\w*\s*=\s*-?\d/,
  },
];

/** Non-test sources under every declared root, recursively, as `[name, code]`. */
function scannedSources(): [string, string][] {
  const found: [string, string][] = [];

  function walk(dir: string): void {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (!/\.tsx?$/.test(entry.name) || /\.test\.tsx?$/.test(entry.name)) continue;
      found.push([path.relative(SRC_DIR, full), code(readFileSync(full, "utf8"))]);
    }
  }

  for (const root of ROOTS) walk(path.join(SRC_DIR, root));
  for (const file of ROOT_FILES) {
    found.push([file, code(readFileSync(path.join(SRC_DIR, file), "utf8"))]);
  }
  return found;
}

test("the scan reaches the files it claims to", () => {
  // The failure this prevents is the quiet one: a root that was renamed, or
  // a `readdirSync` that never descended, leaves the assertion below passing
  // over an empty list. Naming two files it must contain — one per root, one
  // of them the shell file the non-recursive version missed — turns that
  // into a failure with a reason.
  const scanned = scannedSources().map(([name]) => name);

  expect(scanned).toContain(path.join("features", "queue", "StageGroup.tsx"));
  expect(scanned).toContain(path.join("features", "shell", "WorkspaceShell.tsx"));
  // Story 2.2's surface, including the nested `overview/` folder — the four
  // stage variants are where a percentage or a phase boundary would most
  // plausibly be recomputed, so a scan that stopped at the folder's top
  // level would miss exactly the files this guard is now for.
  expect(scanned).toContain(path.join("features", "claim-detail", "CaseHeader.tsx"));
  // Story 4.1's card, for the same reason: it is the file that would hold the
  // date comparison if anybody re-derived Upcoming/Done.
  expect(scanned).toContain(path.join("features", "diary", "MeetingCard.tsx"));
  // Story 4.2's sub-tab, and it is the strongest pull in the feature: the
  // component holds a greeting count, a filtered day of meetings and a note
  // list at once, and every one of "which of these are today", "how many are
  // ahead" and "is this one still upcoming" is one line away from being
  // answered here instead of read off the wire. `lib/clock.ts` is deliberately
  // *outside* the scanned roots — see its docstring on where the line is.
  expect(scanned).toContain(path.join("features", "diary", "NotesSubTab.tsx"));
  // Story 4.2's follow-up review: the hook that owns the meetings payload. It
  // has a `select` already, so a re-derived `status` there is one line and
  // reaches every diary surface at once — and none of the component roots would
  // see it. `ROOT_FILES` explains why the directory around it is not scanned.
  expect(scanned).toContain("api/meetings.ts");
  // Story 4.3's three. The sub-tab is handed a list the server ordered and
  // counted, so `.sort()` on `sentAt` and a `total` worked out from
  // `items.length` are both one line away; the composer is handed a
  // server-merged letter, and the single most tempting thing in this feature is
  // to "just" substitute a claim field into it, which is the AD-1 violation the
  // whole story is built to prevent. `api/emails.ts` joins `api/meetings.ts` in
  // `ROOT_FILES` for that module's reason: it owns the email payload and has a
  // `select`, so a re-derived value planted there would reach every email
  // surface at once while every component root stayed green.
  expect(scanned).toContain(path.join("features", "diary", "EmailsSubTab.tsx"));
  expect(scanned).toContain(path.join("features", "diary", "EmailComposerDialog.tsx"));
  // Story 5.1's dashboard, and `DashboardPage.tsx` is the tempting file in it:
  // it holds the card table, so it is the one place where all twelve figures
  // are in scope at once and a caption's threshold sits beside the count that
  // threshold produced. A scan that stopped at Epic 4's surfaces would leave
  // the whole supervisor half of the console unguarded.
  expect(scanned).toContain(path.join("features", "dashboard", "DashboardPage.tsx"));
  // Story 5.2's table, and it is the strongest pull on this page: the component
  // holds nine ranked rows, the thresholds the two chips were banded by, and the
  // portfolio composite every deviation is a percentage of — so re-sorting the
  // list, re-banding a chip and recomputing a bar ratio are each one line away,
  // in one file. `DashboardPage.tsx` being scanned says nothing about this file.
  expect(scanned).toContain(
    path.join("features", "dashboard", "HandlerBenchmarkTable.tsx"),
  );
  // Story 5.3's chart surfaces, all six of them. `DashboardPage.tsx` being
  // scanned says nothing about a nested folder, and this is the folder with the
  // strongest pull on the page: each component is handed a finished series
  // *plus* the total it was counted out of and the number of categories it was
  // cut from, so a percentage, an "other" bucket and a re-ranking are each one
  // line away — and `chartTheme.ts` is where a threshold would most plausibly
  // be smuggled in as a colour rule ("red above 65").
  for (const file of [
    "PortfolioCharts.tsx",
    "DistributionDonut.tsx",
    "DistributionBars.tsx",
    "SlaTiles.tsx",
    "ChartFrame.tsx",
    "chartTheme.ts",
  ]) {
    expect(scanned).toContain(path.join("features", "dashboard", "charts", file));
  }
  // Story 5.4's table, and it is the strongest pull on the page after 5.2's:
  // the component holds a page of a *ranked* list, the cap it was cut at, the
  // population it was cut from, and — on every row — a fraud score beside the
  // published threshold that decided its tint. Re-sorting the table, re-cutting
  // it at `cap`, and re-banding the fraud cell are each one line, in one file.
  // `DashboardPage.tsx` being scanned says nothing about this file.
  expect(scanned).toContain(
    path.join("features", "dashboard", "PriorityClaimsTable.tsx"),
  );
  // Story 5.5's four, in a nested folder `DashboardPage.tsx` being scanned says
  // nothing about — and the folder with the strongest pull on the page after
  // 5.2's, because it is the first surface where the browser holds a *filter*.
  // `filters.ts` is the one that matters most: it owns the vocabulary, it is
  // pure, and it looks like plumbing, which is exactly the shape of file where a
  // "just narrow it here" would be least noticed. `DrillClaimsPage.tsx` holds a
  // page of a server-filtered, server-ranked list beside the filter set that
  // produced it and the population count, so re-filtering, re-sorting and
  // re-counting are each one line. `FilterChips.tsx` decides how many chips to
  // draw, which is where a count comparison would go. `ReadOnlyClaimPage.tsx`
  // renders four stage variants' money side by side, which is the strongest pull
  // in the console towards adding two cent figures together.
  for (const file of [
    "filters.ts",
    "FilterChips.tsx",
    "DrillClaimsPage.tsx",
    "ReadOnlyClaimPage.tsx",
  ]) {
    expect(scanned).toContain(path.join("features", "dashboard", "drill", file));
  }
  // The SLA tile vocabulary Story 5.3 lifted out of `SlaStrip.tsx` so both
  // surfaces render one server value through one spec. It holds the tone map
  // and both formatters, which is precisely where a client-side verdict would
  // go if anybody decided to compute one from `value` and `target`.
  expect(scanned).toContain(path.join("features", "shell", "slaTiles.ts"));
  expect(scanned).toContain("api/emails.ts");
  expect(scanned).toContain(
    path.join("features", "claim-detail", "overview", "TreatmentOverview.tsx"),
  );
  // Story 2.4's diagram, in a second nested folder. It is the file with the
  // strongest pull towards a local rule — the prototype bands its marker
  // colours inside the drawing function — so a scan that missed it would
  // miss the one place this guard is most for.
  expect(scanned).toContain(path.join("features", "claim-detail", "injury", "BodyMap.tsx"));
  // Story 2.5's tab, in a third nested folder. The pull towards a local rule
  // here is the *path*: the prototype classifies with `c.path || "B"` in the
  // browser, and a component that reached for `severityScore` to decide which
  // banner to draw would be that bug with a different default.
  expect(scanned).toContain(
    path.join("features", "claim-detail", "documents", "RequiredFormsCard.tsx"),
  );
  // Story 2.6's grid, in a fourth nested folder. The pull here is the *count*
  // — `photos.length` is right there, one line from the label — and the
  // thumbnail state, which a component would get wrong by reading the URL
  // instead of the flag.
  expect(scanned).toContain(path.join("features", "claim-detail", "photos", "PhotoCard.tsx"));
  // Story 3.1's benefit card. The pull here is the strongest of the lot: an
  // AWW and a comp rate are on screen together, and multiplying them is one
  // line — which is exactly what the prototype's `computeBenefit` does in the
  // browser, clamp and all.
  expect(scanned).toContain(path.join("features", "claim-detail", "BenefitCard.tsx"));
  // Story 3.2's verdict renders inside the treatment variant, which the scan
  // already reaches (asserted above). Named here as the *rule* rather than the
  // file: the reserve check is the one place in the console where the payload
  // hands a component both a judgement and the two figures behind it, so
  // "compare them and see" is one line away on a card that already renders
  // `reserveCents`.
  // Story 3.3's tab, in a fifth nested folder. The pull here is the whole
  // shape of the payload: a summary served beside the very rows it summarises,
  // so every figure in the heading is one `reduce` away from being recomputed
  // — and the recomputation would be *right* on most claims and wrong on the
  // ones where the fallback rule or a frozen paid week applies.
  expect(scanned).toContain(path.join("features", "claim-detail", "bills", "BillsTab.tsx"));
  expect(scanned).toContain(
    path.join("features", "claim-detail", "bills", "FinancialSummaryCard.tsx"),
  );
  // Story 3.5's card, in a sixth nested folder. The pull here is the ranking:
  // the payload hands a component a list *and* the urgency each row was ranked
  // by, so `.sort()` or a `urgency === "high"` partition is one line away — and
  // it would be right on most claims and wrong on the ordering Story 5.4 reads
  // element 0 of.
  expect(scanned).toContain(
    path.join("features", "claim-detail", "actions", "ActionsCard.tsx"),
  );
  // Story 7.1's five, in a nested folder `DashboardPage.tsx` being scanned says
  // nothing about — and the folder with the strongest pull on the page since
  // 5.2's, because it is the first surface where the browser holds a *rule's
  // vocabulary* beside the score that vocabulary bands.
  //
  // `FraudPage.tsx` holds all four query results at once, including two
  // populations over one column pair that are equal on today's data and are two
  // different rules — so deriving either from the other is one line and would be
  // right until an operator retuned a threshold. `FraudDistributionCard.tsx`
  // renders a band legend quoting both published edges, which is where a
  // re-banding would go. `SiuPipelineCard.tsx` draws two segment sets whose
  // drill-throughs carry two facets each, which is where a browser-side
  // intersection would go. `FraudRateTables.tsx` is handed three sortable tables
  // with a numerator and a denominator on every row, so `.sort()` and a division
  // are each one line. `RedFlagFrequencyCard.tsx` holds a ranked list, a cut and
  // a coverage pair — a re-rank, a re-cut and a percentage, on one object.
  for (const file of [
    "FraudPage.tsx",
    "FraudDistributionCard.tsx",
    "SiuPipelineCard.tsx",
    "FraudRateTables.tsx",
    "RedFlagFrequencyCard.tsx",
  ]) {
    expect(scanned).toContain(path.join("features", "dashboard", "fraud", file));
  }
  // Story 7.1's navigation, in `features/shell` — which is scanned, but by a
  // root added for the *queue* payload five stories ago. Named because it is the
  // first component in that folder that branches on a value from `/api/me`, and
  // "which persona sees which section" is exactly the kind of rule that grows a
  // comparison.
  expect(scanned).toContain(path.join("features", "shell", "WorkspaceNav.tsx"));
  expect(scanned.some((name) => name.includes(".test."))).toBe(false);
});

test("the queue and shell sources hold no scoring, banding, counting or flag logic", () => {
  const offenders = scannedSources().flatMap(([name, source]) =>
    FORBIDDEN.filter(({ pattern }) => pattern.test(source)).map(
      ({ why, pattern }) => `${name} ${why} (matched ${String(pattern)})`,
    ),
  );

  expect(offenders).toEqual([]);
});

test("the guard would notice a derivation if one were added", () => {
  // A test that only ever reads clean files cannot tell "nothing is wrong"
  // from "nothing is checked". These are the shapes the criterion is about,
  // written out, and each must be caught by something above.
  const smells = [
    'const band = card.severityScore >= 65 ? "high" : "low";',
    "const score = card.priorityScore * 2 + card.daysOpen;",
    "const blocked = card.rtwBlocked && card.paymentDue;",
    "items.sort((a, b) => b.priorityScore - a.priorityScore);",
    "const MARKER_THRESHOLD = 30;",
    // The two the counts were added for.
    "const total = STAGE_ORDER.reduce((sum, s) => sum + q.groups[s].total, 0);",
    "const remaining = group.total - items.length;",
    // And the one the old stripper hid: a `//` inside a string ate the rest
    // of its line, so anything after a URL on the same line went unscanned.
    'const doc = "https://example.com/rules"; const band = card.severityScore >= 65;',
    // The comparisons the `(?![/>])` exclusion must still catch — it would
    // be a quiet hole otherwise, and a hole in exactly the operator a phase
    // boundary would be written with.
    'if (overview.expectedDays > 42) return "late";',
    "const late = overview.daysOpen >= 30;",
    // Story 2.4's: the prototype's marker colouring, transliterated.
    'const col = marker.severityScore >= 70 ? ER : WN;',
    "const SEVERITY_MAX = 100;",
    // Story 3.2's: the prototype's `reserveCheck`, transliterated. Both halves
    // — banding the ratio, and computing the ratio at all — because a card
    // that only drew a bar from the two figures would still be the second
    // computer AD-10 forbids, and would round differently from the server's.
    'const verdict = check.projectedRemainingCents / check.reserveCents > 1.15 ? "light" : "ok";',
    "const pct = (check.remainingIndemnityCents / check.reserveCents) * 100;",
    // Story 3.3's: the three shapes the Bills tab invites. Summing the rows
    // the heading already totals, counting the paid weeks the summary already
    // counted, and adding the reserve to the paid figure to get the projected
    // total — each one line, each a second computer for a published number.
    "const paid = bills.items.reduce((s, b) => s + b.paidCents, 0);",
    "const done = summary.installmentsPaid + 1;",
    "const projected = summary.paidToDateCents + summary.reserveCents;",
    // Story 3.5's: the prototype's `budget=7` counter, transliterated, and the
    // partition a component would reach for to draw the high-urgency rows
    // first — both of which the server has already done.
    "const room = actions.cap - shown.length;",
    "items.sort((a, b) => a.urgency - b.urgency);",
    "const spare = payload.paddingFloor - rows.length;",
    // Story 4.1's: the prototype's own line, transliterated. This is the
    // shape the diary root was added for, and until the four field names went
    // on the list above nothing here caught it — `today` is not a numeric
    // literal, so the threshold rule never fired.
    "const upcoming = meeting.meetingDate >= today && !meeting.isDone;",
    'const tone = m.status < "done" ? UPCOMING : DONE;',
    // Story 4.2's: the two shapes the Notes sub-tab invites. Counting the
    // upcoming meetings out of a list that is one filtered day of the book,
    // and decrementing the server's count after a ✓ Done rather than letting
    // the refetch answer.
    "const ahead = today.items.filter((m) => m.status).length - 1;",
    "const left = summary.upcomingCount - 1;",
    "const stale = note.notedAt < cutoff;",
    // Story 4.3's: re-sorting a log the server already ordered `sentAt DESC`,
    // ranking two priorities as if the enum were a scale, and inventing a
    // delivery state by comparing a composition instant with the clock.
    "items.sort((a, b) => b.sentAt - a.sentAt);",
    "const louder = a.priority > b.priority;",
    "const late = email.sentAt < deadline;",
    // The `(?<!=)` exclusion must not have opened a hole: a genuine comparison
    // whose right-hand side is a payload field is still caught.
    "if (shown < page.total) return true;",
    // Story 5.2's two. Re-ranking the table the server ranked — the temptation
    // a nine-column table with a numeric first column creates all by itself —
    // and dividing a composite by the portfolio's to draw the bar, which is the
    // server's `cycleSpeedPct` recomputed with the browser's own rounding
    // against whichever rows are on screen.
    "rows.sort((a, b) => a.compositeDays - b.compositeDays);",
    "const ratio = (row.compositeDays / data.portfolioCompositeDays) * 100;",
    // …and the third: re-banding a chip from the score and the cut-off the
    // response publishes side by side.
    'const band = row.complexityScore >= data.complexityHighMin ? "high" : "med";',
    // …and the fourth and fifth, which are why `rank` joined the field list:
    // re-sorting the table on the very column that records the server's order,
    // and doing arithmetic on a rank to turn it back into an index.
    "rows.sort((a, b) => a.rank - b.rank);",
    "const position = row.rank - 1;",
    // Story 5.3's four, and they are the four things a chart component is most
    // tempted by. Rolling the truncated tail into an "other" slice from a total
    // the categories behind it were never sent for; turning a count into a
    // share of the scope; re-ranking a series the server ordered under a
    // tie-break the browser has never seen; and re-banding the severity donut
    // from the cut-off published beside it.
    "const other = series.total - series.items.reduce((s, i) => s + i.count, 0);",
    "const share = (item.count / series.total) * 100;",
    "items.sort((a, b) => b.paidCents - a.paidCents);",
    "const hidden = series.totalCategories - series.limit;",
    'const band = item.count >= data.medRiskSeverityMin ? "med" : "low";',
    "const top = charts.byState.filter((s) => s.count > 5);",
    // Story 5.4's four, and they are the four things this table is most tempted
    // by. Re-banding the fraud cell from the score and the cut-off published
    // side by side — the prototype's own line, transliterated; re-cutting the
    // page at the server's cap; re-sorting a list the server ranked under three
    // rule versions the browser has never seen; and partitioning the rows by
    // the generator's answer to draw the loud ones first.
    'const tone = row.fraudScore >= data.fraudFlagScoreMin ? ER : OK;',
    "const shown = rows.slice(0, data.cap);",
    "rows.sort((a, b) => a.severityBand - b.severityBand);",
    'const urgent = rows.filter((r) => r.nextBestAction);',
    // Story 5.5's two, and they are the two this surface is most tempted by.
    // Re-parsing the URL the server has already told you what it made of — the
    // chip row drawn from a second reading rather than from `appliedFilters` —
    // and re-cutting a page of a filtered list at a count the server published.
    "const chips = params.filter((p) => p.appliedFilters);",
    "const shown = rows.slice(0, data.total);",
    // Story 7.1's four, and they are the four this workspace is most tempted by.
    // Re-banding the fraud score from the edge published beside it — the
    // prototype's own uncoloured-score gap, filled in the browser instead of by
    // the derivation; turning basis points into a percentage at a call site;
    // recomputing a rate from the numerator and denominator on one row; and
    // turning the coverage pair into a percentage in a caption.
    'const band = row.fraudScore >= data.fraudBandHighMin ? "high" : "medium";',
    "const pct = row.rateBp / 100;",
    "const rate = (row.flagged / row.claims) * 100;",
    "const covered = data.claimsWithInsight - data.unreadable;",
    "rows.sort((a, b) => b.rateBp - a.rateBp);",
  ];

  for (const smell of smells) {
    expect(
      FORBIDDEN.some(({ pattern }) => pattern.test(code(smell))),
      `no rule caught: ${smell}`,
    ).toBe(true);
  }
});

test("the guard does not fire on rendering the server's answers", () => {
  // The other half of a blunt check: it must leave legitimate presentation
  // alone, or the next person turns it off instead of fixing their code.
  const innocent = [
    // Page arithmetic, which is not a band cut-off. These are the lines that
    // made `limit` too broad a token to guard as a bare word: a list surface is
    // free to compute its next offset, and only arithmetic on the *property* —
    // the server's published cut — is the thing worth failing over.
    "const nextOffset = offset + limit;",
    "const lastIndex = limit - 1;",
    "{card.daysOpen}d",
    "className={RISK_DOT[card.risk]}",
    "{card.priorityMarker && <span>🔺</span>}",
    "const total = queue.data?.filteredTotal ?? 0;",
    "if (group.nextCursor !== null) return true;",
    "const label = STAGE_LABEL[stage];",
    // A comment *inside a template literal expression* still gets stripped,
    // and the literal text around it still does not reach the patterns.
    "const cls = `px-2 ${selected ? BRAND : NONE} py-1`;",
    // JSX text is not a string literal, so the stripper leaves it — and a
    // heading is allowed to contain the words the payload uses.
    "<span>Claim risk</span>",
    "<h3>Current treatment phase</h3>",
    "<p>{overview.daysOpen} days open</p>",
    // A fat arrow returning a payload field is not a comparison — the shape
    // `api/meetings.ts` is full of, and the false positive `(?<!=)` removes.
    "getNextPageParam: (last) => last.nextCursor ?? undefined,",
    "const pick = (page) => page.total;",
    // Story 4.3's: reading a stored enum through a tone map, and formatting an
    // instant. Both are presentation, and both mention a field on the list.
    "className={EMAIL_PRIORITY_TONE[email.priority]}",
    "<span>Sent {formatSentAt(email.sentAt)}</span>",
    // Story 5.3's: reading the truncation contract in order to *state* it, and
    // handing an accessor to a chart component. Neither computes anything, and
    // both are the shape `DistributionBars.tsx` is full of — a guard that fired
    // on them would be the guard training the code.
    "series?.truncated === true && series.limit !== null",
    "{truncationCaption(series.limit, series.totalCategories)}",
    "value={(item) => item.paidCents}",
    "series={data?.byInjuryType}",
    // Story 5.4's: reading a band through a tone map, reading the server's flag
    // to pick between two classes, and *stating* the two numbers the caption
    // quotes. None computes anything, and all three are the shape
    // `PriorityClaimsTable.tsx` is full of — a guard that fired on them would be
    // the guard training the code.
    "className={SEVERITY_TONE[row.severityBand]}",
    'className={row.fraudFlagged ? "text-warn" : "text-muted-text"}',
    "<span>(showing top {data.cap} of {data.total})</span>",
    "title={row.nextBestAction}",
    // …and the `.slice` the new rule must *not* fire on: a string clamped by a
    // local display constant, which is what `EmailsSubTab` and `NotesSubTab`
    // already do and which mentions no payload field at all.
    "const snippet = characters.slice(0, SNIPPET_LENGTH).join(\"\");",
    "const initials = name.split(\" \").slice(0, 2);",
    // Story 5.5's: reading the server's chip list to draw it, and handing a
    // handler's id to a link builder. Neither computes anything, and both are
    // the shape `FilterChips.tsx` and `HandlerBenchmarkTable.tsx` are full of.
    "const applied = list.data?.appliedFilters ?? [];",
    "to={drillHref({ handlerId: String(row.handlerId) })}",
    // Story 7.1's: rendering a rate through the one function allowed to convert
    // basis points, reading two published counts to *state* them, and handing a
    // band's wire value to a link builder. None computes anything, and all three
    // are the shape `FraudRateTables.tsx` and `RedFlagFrequencyCard.tsx` are full
    // of — a guard that fired on them would be the guard training the code.
    "<span>{formatBasisPoints(row.rateBp)}%</span>",
    "<span>{row.flagged} of {row.claims}</span>",
    "<p>{data.claimsWithInsight} of {data.claimsInScope} claims</p>",
    "onSelect={(band) => void navigate(drillHref({ fraudBand: band }))}",
  ];

  for (const line of innocent) {
    expect(
      FORBIDDEN.find(({ pattern }) => pattern.test(code(line)))?.why,
      `false positive on: ${line}`,
    ).toBeUndefined();
  }
});

test("the stripper removes whichever construct opens first", () => {
  // The bug, isolated: the chain removed line comments before strings, so
  // the `//` in a URL won and everything after it on that line vanished.
  expect(code('const u = "a//b"; x >= 1;')).toContain(">=");
  // …and the converse still holds — code inside a comment stays invisible.
  expect(code('// x >= 1\nconst u = "";')).not.toContain(">=");
  // A string inside a comment does not re-open scanning.
  expect(code('/* "unterminated */ y >= 2;')).toContain(">=");
});
