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

interface SeedClaim {
  employer: string;
  stage: string;
  severity_score: number;
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

export function expectedStatsFor(name: string, role: string): ExpectedStats {
  const user = seed.app_users.find((u) => u.name === name && u.role === role);
  if (!user) throw new Error(`no seeded persona ${name}/${role}`);

  const visible = user.scope_all
    ? seed.claims
    : seed.claims.filter((claim) => user.employers.includes(claim.employer));

  return {
    caseload: visible.length,
    activeTx: visible.filter((claim) => claim.stage === "treatment").length,
    highRisk: visible.filter((claim) => claim.severity_score >= HIGH_RISK_MIN).length,
  };
}
