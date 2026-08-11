/**
 * Story 2.1 AC 2, structurally — **no derivation exists in TypeScript.**
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
const ROOTS = ["features/queue", "features/shell"];

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
  "total|unfilteredTotal|filteredTotal|nextCursor";

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
    pattern: new RegExp(
      `\\b(?:${DERIVED_FIELDS})\\s*(?:[-+*/%]|[<>]=?)|(?:[-+*/%]|[<>]=?)\\s*\\w*\\.(?:${DERIVED_FIELDS})\\b`,
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
