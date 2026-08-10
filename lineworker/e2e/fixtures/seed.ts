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
  employer: string;
  stage: string;
  severity_score: number;
  return_status: string;
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
