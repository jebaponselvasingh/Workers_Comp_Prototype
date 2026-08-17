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
const ROOT_FILES = ["api/meetings.ts"];

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
  "upcomingCount|notedAt";

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
    // The `(?<!=)` exclusion must not have opened a hole: a genuine comparison
    // whose right-hand side is a payload field is still caught.
    "if (shown < page.total) return true;",
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
