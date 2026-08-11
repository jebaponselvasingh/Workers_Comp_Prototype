/**
 * Story 2.1 AC 2, structurally — **no derivation exists in TypeScript.**
 *
 * The acceptance criterion does not say "the cards happen to be correct";
 * it says the scoring, banding and flag logic is *absent* from
 * `web/src/features/queue/`, and asks for that to be asserted. Every other
 * test in this directory checks that the pane renders what the server sent,
 * which is a different claim: a component that also computed something on
 * the side would pass all of them.
 *
 * This is the web twin of the server's
 * `test_no_module_outside_the_registry_hardcodes_the_band`, and it is blunt
 * in the same way on purpose. It reads the directory as text and fails on a
 * pattern, so the fix is either to delete the arithmetic or to justify an
 * entry below — a choice a reviewer sees rather than infers.
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

const QUEUE_DIR = path.dirname(fileURLToPath(import.meta.url));

/**
 * Source with comments and literal text removed, so the patterns below see
 * code and only code. Template literals keep their `${…}` expressions —
 * that is real code, and `${score > 30 ? …}` in a class name would still be
 * a derivation.
 */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/\/\/[^\n]*/g, " ")
    .replace(/`(?:[^`\\$]|\\.|\$(?!\{))*`/g, '""')
    .replace(/`(?:[^`\\$]|\\.|\$(?!\{))*(\$\{)/g, '"" $1')
    .replace(/\}(?:[^`\\$]|\\.|\$(?!\{))*`/g, '} ""')
    .replace(/'(?:[^'\\]|\\.)*'/g, '""')
    .replace(/"(?:[^"\\]|\\.)*"/g, '""');
}

/** Every payload field whose value is a *rule's answer* rather than a fact. */
const DERIVED_FIELDS =
  "priorityScore|priorityMarker|severityScore|fraudScore|daysOpen|risk|" +
  "siuReview|rtwBlocked|paymentDue|fraudFlag|litigationFlag|surgeryRequired";

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
    why: "names a threshold constant — the numbers live in the rule documents, not in the client",
    pattern: /\b(?:const|let)\s+\w*(?:THRESHOLD|Threshold|MIN|Min|MAX|Max|WEIGHT|Weight)\w*\s*=\s*-?\d/,
  },
];

/** Non-test sources under `features/queue/`, as `[name, code]`. */
function queueSources(): [string, string][] {
  return readdirSync(QUEUE_DIR)
    .filter((name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name))
    .map((name) => [name, code(readFileSync(path.join(QUEUE_DIR, name), "utf8"))]);
}

test("the queue directory holds no scoring, banding or flag logic", () => {
  const offenders = queueSources().flatMap(([name, source]) =>
    FORBIDDEN.filter(({ pattern }) => pattern.test(source)).map(
      ({ why, pattern }) => `${name} ${why} (matched ${String(pattern)})`,
    ),
  );

  expect(offenders).toEqual([]);
});

test("the guard would notice a derivation if one were added", () => {
  // A test that only ever reads clean files cannot tell "nothing is wrong"
  // from "nothing is checked". These are the four shapes the criterion is
  // about, written out, and each must be caught by something above.
  const smells = [
    'const band = card.severityScore >= 65 ? "high" : "low";',
    "const score = card.priorityScore * 2 + card.daysOpen;",
    "const blocked = card.rtwBlocked && card.paymentDue;",
    "items.sort((a, b) => b.priorityScore - a.priorityScore);",
    "const MARKER_THRESHOLD = 30;",
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
    "const remaining = Math.max(0, group.total - items.length);",
    'const label = STAGE_LABEL[stage];',
  ];

  for (const line of innocent) {
    expect(
      FORBIDDEN.find(({ pattern }) => pattern.test(code(line)))?.why,
      `false positive on: ${line}`,
    ).toBeUndefined();
  }
});
