import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/**
 * Expected caseload numbers, computed from the seed file the stack is
 * migrated with — not typed into the spec.
 *
 * A spec asserting `Caseload 27` says nothing about *why* 27, and goes
 * quietly stale the day a claim is added to the seed. Reading the dataset
 * and counting it here keeps the assertion meaningful and self-updating.
 *
 * This is deliberately an **independent oracle**: it re-states the rules
 * (what a persona may see, where the high-risk band starts) rather than
 * importing them from the server, because a test that computed its
 * expectation with the code under test would agree with that code no
 * matter what either of them did.
 */

const SEED_PATH = fileURLToPath(new URL("../../server/data/seed/seed_data.json", import.meta.url));
const GLOSSARY_PATH = fileURLToPath(
  new URL("../../server/data/seed/glossary_terms.json", import.meta.url),
);

/** Independent restatement of `services/derivations/risk` — see above. */
const HIGH_RISK_MIN = 65;

/**
 * Independent restatement of `services/worklist/sla` — same reasoning as
 * the risk band above. The targets are the deployment defaults; a stack
 * started with different ones would legitimately fail these specs, which
 * is the point of them being written down here rather than read off the
 * response.
 */
const SLA_TARGETS = { pick: 1, approve: 5, settle: 30, rtwRate: 80 } as const;
const FULLY_RECOVERED = "returned_and_fully_recovered";

interface SeedClaim {
  claim_id: string;
  employer: string;
  stage: string;
  status: string;
  severity_score: number;
  return_status: string;
  froi_date: string;
  surgery_required: boolean;
  litigation_flag: boolean;
  fraud_flag: boolean;
  fraud_score: number;
  sla_pick_days: number | null;
  sla_approve_days: number | null;
  settlement_days: number | null;
}

interface SeedUser {
  name: string;
  role: string;
  scope_all: boolean;
  employers: string[];
}

interface Seed {
  claims: SeedClaim[];
  app_users: SeedUser[];
}

const seed = JSON.parse(readFileSync(SEED_PATH, "utf8")) as Seed;

export interface ExpectedStats {
  caseload: number;
  activeTx: number;
  highRisk: number;
}

function claimsFor(name: string, role: string): SeedClaim[] {
  const user = seed.app_users.find((u) => u.name === name && u.role === role);
  if (!user) throw new Error(`no seeded persona ${name}/${role}`);

  return user.scope_all
    ? seed.claims
    : seed.claims.filter((claim) => user.employers.includes(claim.employer));
}

export function expectedStatsFor(name: string, role: string): ExpectedStats {
  const visible = claimsFor(name, role);

  return {
    caseload: visible.length,
    activeTx: visible.filter((claim) => claim.stage === "treatment").length,
    highRisk: visible.filter((claim) => claim.severity_score >= HIGH_RISK_MIN).length,
  };
}

export interface ExpectedSlaTile {
  /** What the tile should read, formatted the way the strip formats it. */
  text: string;
  status: "pass" | "warn" | "no_data";
}

/**
 * Half-up rounding at the strip's display precision — the browser shows
 * whatever the service rounded to, so the oracle rounds the same way
 * rather than comparing against an unrounded mean.
 *
 * The sum is scaled *before* the division, not after. `(sum / len) * 10`
 * evaluates a true 2.65 as 26.499999999999996 and rounds it to 2.6 while
 * the server (which divides in `Decimal`) reports 2.7 — a spec failure
 * with no defect behind it. Scaling first keeps the numerator exact,
 * since every SLA input is a whole number of days.
 */
function mean(values: number[], decimals: number): number {
  const factor = 10 ** decimals;
  const sum = values.reduce((a, b) => a + b, 0);
  return Math.round((sum * factor) / values.length) / factor;
}

/** The four tiles a persona's seeded book should produce. */
export function expectedSlaFor(name: string, role: string): Record<string, ExpectedSlaTile> {
  const visible = claimsFor(name, role);
  const settled = visible.filter((claim) => claim.stage === "settled");

  const picks = visible.map((c) => c.sla_pick_days).filter((d): d is number => d !== null);
  const approves = visible.map((c) => c.sla_approve_days).filter((d): d is number => d !== null);
  const settles = settled.map((c) => c.settlement_days).filter((d): d is number => d !== null);
  // 100 or 0 per settled claim, then averaged: the rate is over *all*
  // settled claims, including those with no recorded settlement duration.
  const recovered = settled.map((c) => (c.return_status === FULLY_RECOVERED ? 100 : 0));

  /**
   * An empty segment is a legitimate outcome, not an impossible one — so
   * the oracle predicts the em dash rather than dividing by zero. Without
   * this branch a seed change that empties any segment for any e2e persona
   * makes the spec assert `"NaNd"` against a correctly-rendered `—`, and
   * the failure reads as a bug in the component. Mirrors the `if picks
   * else None` guard in `server/tests/seed_fixture.py`.
   */
  const tile = (
    values: number[],
    decimals: number,
    unit: string,
    target: number,
    above = false,
  ): ExpectedSlaTile => {
    if (values.length === 0) return { text: "—", status: "no_data" };
    const value = mean(values, decimals);
    return {
      text: `${value.toFixed(decimals)}${unit}`,
      status: (above ? value > target : value < target) ? "pass" : "warn",
    };
  };

  return {
    pick: tile(picks, 1, "d", SLA_TARGETS.pick),
    approve: tile(approves, 1, "d", SLA_TARGETS.approve),
    settle: tile(settles, 0, "d", SLA_TARGETS.settle),
    rtwRate: tile(recovered, 0, "%", SLA_TARGETS.rtwRate, true),
  };
}

/**
 * Story 1.6 — the glossary, read from the file the migration seeds.
 *
 * Not an independent restatement like the two oracles above, because there
 * is no rule to restate: the glossary's whole contract is "the seed file's
 * rows, verbatim, in the file's order". The one thing worth restating is
 * the *search predicate*, which is a rule, so `glossaryMatching` spells it
 * out rather than importing it from the component under test.
 *
 * Both functions return a fresh array. Handing out the module-level
 * `glossary` would let one spec's in-place `.sort()` rewrite the
 * expectation for every spec that runs after it in the same worker, and the
 * failure would surface in an unrelated file.
 */
export interface SeedGlossaryTerm {
  abbreviation: string;
  term: string;
  definition: string;
  sort_order: number;
}

const glossary = (JSON.parse(readFileSync(GLOSSARY_PATH, "utf8")) as SeedGlossaryTerm[])
  .slice()
  .sort((a, b) => a.sort_order - b.sort_order);

/** Every seeded term, in the prototype's display order. */
export function expectedGlossary(): SeedGlossaryTerm[] {
  return glossary.slice();
}

/**
 * The **implemented** predicate, restated: case-insensitive substring
 * across abbreviation OR term OR definition, over a trimmed query.
 *
 * The trim is deliberate and is knowingly reproduced here. `GlossaryPanel`
 * trims where the prototype's `renderGloss` does not — a named deviation,
 * argued in that component's `matches()` docstring — so an oracle that
 * restated the *prototype's* rule would disagree with the shipped console
 * on any query carrying a stray space, and the spec would be asserting
 * behaviour nothing implements.
 */
export function glossaryMatching(query: string): SeedGlossaryTerm[] {
  const q = query.trim().toLowerCase();
  if (!q) return glossary.slice();
  return glossary.filter(
    (t) =>
      t.abbreviation.toLowerCase().includes(q) ||
      t.term.toLowerCase().includes(q) ||
      t.definition.toLowerCase().includes(q),
  );
}

/**
 * Story 2.1 — the claim queue, restated independently.
 *
 * Every rule the queue applies, written out here from the story text and
 * the prototype (`docs/Workers_Comp_Prototype.html`: the derived-flag pass
 * at 951-956, `priorityScore` at 1120-1132, the filter mapping at
 * 1155-1165) rather than imported from the server or read out of the JDM
 * documents. That discipline matters more here than for any earlier
 * oracle: a queue card is the product of five derivations, thirteen
 * weights, a filter, a sort and a marker rule, and an oracle sharing any
 * one of them with the implementation would be validating the rest by
 * accident.
 *
 * The numbers below are the deployment's seeded rule documents. A stack
 * migrated with different ones would legitimately fail these specs, which
 * is the point of writing them down here rather than reading them off the
 * response.
 */

const MED_RISK_MIN = 35;
const SIU_FRAUD_SCORE_MIN = 60;
const RTW_HASH_MODULUS = 5;
const PAYMENT_HASH_MODULUS = 3;

const WEIGHTS = {
  litigation: 40,
  siuReview: 35,
  rtwBlocked: 30,
  pendingApproval: 25,
  paymentDue: 20,
  surgery: 15,
} as const;
const SEVERITY_FACTOR = 0.3;
const DAYS_OPEN_FACTOR = 0.2;
const DAYS_OPEN_CAP = 60;
const SETTLED_PENALTY = -100;
const MARKER_THRESHOLD = 30;
const MARKER_COUNT = 3;
const PENDING_APPROVAL_STATUSES = ["initial", "ch_assessment_process"];
const UNDER_TREATMENT = "under_treatment";

export const STAGES = ["intake", "investigation", "treatment", "settled"] as const;
export type SeedStage = (typeof STAGES)[number];

export type SeedFilter =
  | "all"
  | "active"
  | "high_risk"
  | "fraud"
  | "litigation"
  | "payment_due"
  | "surgery"
  | "siu";

/** The prototype's `hashStr` (line 646), restated. */
function hashBucket(businessId: string): number {
  let h = 0;
  for (let i = 0; i < businessId.length; i++) {
    h = (h * 31 + businessId.charCodeAt(i)) >>> 0;
  }
  return h;
}

function riskBand(severityScore: number): "high" | "med" | "low" {
  if (severityScore >= HIGH_RISK_MIN) return "high";
  if (severityScore >= MED_RISK_MIN) return "med";
  return "low";
}

/**
 * Claim age in whole days, floored at zero, counted from the FROI date
 * against **today in UTC**.
 *
 * UTC deliberately: the API container and this process may sit in
 * different zones, and the server resolves `as_of` in UTC for exactly that
 * reason. The one residual risk is a run that straddles midnight UTC — the
 * oracle and the server would then disagree by a day. That is a few
 * seconds of exposure per day against the alternative of pinning a date
 * the running stack does not share.
 */
function daysOpen(claim: SeedClaim): number {
  const now = new Date();
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const [year, month, day] = claim.froi_date.split("-").map(Number);
  const froi = Date.UTC(year, month - 1, day);
  return Math.max(Math.round((today - froi) / 86_400_000), 0);
}

interface QueueFlags {
  risk: "high" | "med" | "low";
  siuReview: boolean;
  rtwBlocked: boolean;
  paymentDue: boolean;
}

function queueFlags(claim: SeedClaim): QueueFlags {
  const bucket = hashBucket(claim.claim_id);
  const risk = riskBand(claim.severity_score);
  return {
    risk,
    siuReview: claim.fraud_flag && claim.fraud_score >= SIU_FRAUD_SCORE_MIN,
    rtwBlocked:
      claim.stage === "treatment" &&
      claim.return_status === UNDER_TREATMENT &&
      (bucket % RTW_HASH_MODULUS === 0 || risk === "high"),
    paymentDue: claim.stage === "treatment" && bucket % PAYMENT_HASH_MODULUS !== 0,
  };
}

function priorityScore(claim: SeedClaim): number {
  const flags = queueFlags(claim);
  let score = 0;
  if (claim.litigation_flag) score += WEIGHTS.litigation;
  if (flags.siuReview) score += WEIGHTS.siuReview;
  if (flags.rtwBlocked) score += WEIGHTS.rtwBlocked;
  if (PENDING_APPROVAL_STATUSES.includes(claim.status)) score += WEIGHTS.pendingApproval;
  if (flags.paymentDue) score += WEIGHTS.paymentDue;
  if (claim.surgery_required) score += WEIGHTS.surgery;
  score += claim.severity_score * SEVERITY_FACTOR;
  score += Math.min(daysOpen(claim), DAYS_OPEN_CAP) * DAYS_OPEN_FACTOR;
  if (claim.stage === "settled") score += SETTLED_PENALTY;
  return score;
}

function matchesFilter(claim: SeedClaim, filter: SeedFilter): boolean {
  const flags = queueFlags(claim);
  switch (filter) {
    case "all":
      return true;
    case "active":
      return claim.stage === "treatment";
    case "high_risk":
      return flags.risk === "high";
    case "fraud":
      return claim.fraud_flag;
    case "litigation":
      return claim.litigation_flag;
    case "payment_due":
      return flags.paymentDue;
    case "surgery":
      return claim.surgery_required;
    case "siu":
      return flags.siuReview;
  }
}

export interface ExpectedCard {
  claimId: string;
  daysOpen: number;
  risk: "high" | "med" | "low";
  priorityScore: number;
  priorityMarker: boolean;
}

export type ExpectedQueue = Record<SeedStage, ExpectedCard[]>;

/**
 * The four groups a persona's seeded book should produce, in the order the
 * pane renders them.
 *
 * Sorted on `(-score, claimId)` — the tie-break included, because ties are
 * common in the settled group and an oracle without one would disagree with
 * a correct implementation about which of two equal claims comes first.
 */
export function expectedQueueFor(
  name: string,
  role: string,
  filter: SeedFilter = "all",
): ExpectedQueue {
  const visible = claimsFor(name, role).filter((claim) => matchesFilter(claim, filter));
  const groups = {} as ExpectedQueue;

  for (const stage of STAGES) {
    const members = visible
      .filter((claim) => claim.stage === stage)
      .sort(
        (a, b) => priorityScore(b) - priorityScore(a) || a.claim_id.localeCompare(b.claim_id),
      );

    let marked = 0;
    groups[stage] = members.map((claim) => {
      const score = priorityScore(claim);
      const marker = marked < MARKER_COUNT && score > MARKER_THRESHOLD;
      if (marker) marked += 1;
      return {
        claimId: claim.claim_id,
        daysOpen: daysOpen(claim),
        risk: queueFlags(claim).risk,
        priorityScore: score,
        priorityMarker: marker,
      };
    });
  }
  return groups;
}

/** Every claim id in the portfolio the persona must NOT be shown. */
export function claimIdsOutsideScopeOf(name: string, role: string): string[] {
  const mine = new Set(claimsFor(name, role).map((claim) => claim.claim_id));
  return seed.claims.map((claim) => claim.claim_id).filter((id) => !mine.has(id));
}
