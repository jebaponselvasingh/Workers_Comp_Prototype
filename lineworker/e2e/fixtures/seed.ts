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

const SEED_PATH = fileURLToPath(
  new URL("../../server/data/seed/seed_data.json", import.meta.url),
);
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
  // Story 5.2's: the handler-benchmark table groups the scoped claim set by the
  // assigned handler, so the oracle has to group the *seed* the same way. Read
  // from the claim rather than from `app_users[].employers` on purpose — that is
  // the roster, and the roster is exactly what this table must not be built
  // from (Kaya Johnson is assigned John Deere and handles none of its claims).
  handler: string;
  stage: string;
  status: string;
  severity_score: number;
  return_status: string;
  // Story 3.1's four: the benefit is a function of the wage, the
  // jurisdiction and the disability.
  state: string;
  aww: number;
  disability: string;
  froi_date: string;
  // Story 3.3's three: the paid snapshot the summary falls back *from*. Zero
  // on every open seeded claim, which is the whole reason there is a fallback.
  paid_indemnity: number;
  paid_medical: number;
  paid_expense: number;
  // Story 3.2's three: the schedule runs from the date of injury for as many
  // weeks as the recovery window implies, and the reserve is what its
  // remaining total is judged against.
  doi: string;
  recovery: string;
  reserve: number;
  surgery_required: boolean;
  // Story 3.5's: the OSHA trigger fires on the gap between "must be logged"
  // and "has been logged", and only the first is in the seed.
  osha_recordable: boolean;
  litigation_flag: boolean;
  fraud_flag: boolean;
  fraud_score: number;
  // Story 5.3's: the injury-type chart ranks the free-text column as stored,
  // with no trimming, case-folding or merging of near-duplicates — grouping
  // identity is a data-quality decision, not something a chart may invent.
  injury_type: string;
  // Story 5.1's: the dataset chip counts distinct plants over the scoped set,
  // because the prototype's "15 US plants" is a literal that is wrong for the
  // one portfolio it was written for and scope-blind for every other persona.
  plant: string;
  // Story 5.4's: the priority worklist's Worker column names the injured
  // employee, which is on `employee` rather than on `claim` — the join
  // `select_priority_rows` makes and the only thing this file needs the
  // `employees` array for.
  employee_id: string;
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

/**
 * Story 5.3's: the employer bars are labelled with `employer.short_name` — the
 * column `select_claim_columns_with_employer` joins — rather than with the full
 * name the claim rows carry. The prototype derives these labels by stripping
 * four suffixes off the full name with a chain of `String.replace`; the short
 * name is a column, and this is what it is for.
 */
interface SeedEmployer {
  name: string;
  short_name: string;
  /**
   * Story 7.2's: the sector cohort splits on an **employer** attribute reached
   * through the join the one scoped read already carries — nine free-text values
   * across ten employers (Automotive twice, the rest 1:1). It is not an industry
   * rollup and the card says so; this oracle reads the column and nothing more.
   */
  sector: string;
}

/** Story 5.4's: the injured worker's display name, keyed by business id. */
interface SeedEmployee {
  employee_id: string;
  name: string;
}

interface Seed {
  claims: SeedClaim[];
  app_users: SeedUser[];
  employers: SeedEmployer[];
  employees: SeedEmployee[];
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
    highRisk: visible.filter((claim) => claim.severity_score >= HIGH_RISK_MIN)
      .length,
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
export function expectedSlaFor(
  name: string,
  role: string,
): Record<string, ExpectedSlaTile> {
  const visible = claimsFor(name, role);
  const settled = visible.filter((claim) => claim.stage === "settled");

  const picks = visible
    .map((c) => c.sla_pick_days)
    .filter((d): d is number => d !== null);
  const approves = visible
    .map((c) => c.sla_approve_days)
    .filter((d): d is number => d !== null);
  const settles = settled
    .map((c) => c.settlement_days)
    .filter((d): d is number => d !== null);
  // 100 or 0 per settled claim, then averaged: the rate is over *all*
  // settled claims, including those with no recorded settlement duration.
  const recovered = settled.map((c) =>
    c.return_status === FULLY_RECOVERED ? 100 : 0,
  );

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

const glossary = (
  JSON.parse(readFileSync(GLOSSARY_PATH, "utf8")) as SeedGlossaryTerm[]
)
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

export const STAGES = [
  "intake",
  "investigation",
  "treatment",
  "settled",
] as const;
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
  const today = Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate(),
  );
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
    paymentDue:
      claim.stage === "treatment" && bucket % PAYMENT_HASH_MODULUS !== 0,
  };
}

function priorityScore(claim: SeedClaim): number {
  const flags = queueFlags(claim);
  let score = 0;
  if (claim.litigation_flag) score += WEIGHTS.litigation;
  if (flags.siuReview) score += WEIGHTS.siuReview;
  if (flags.rtwBlocked) score += WEIGHTS.rtwBlocked;
  if (PENDING_APPROVAL_STATUSES.includes(claim.status))
    score += WEIGHTS.pendingApproval;
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
  const visible = claimsFor(name, role).filter((claim) =>
    matchesFilter(claim, filter),
  );
  const groups = {} as ExpectedQueue;

  for (const stage of STAGES) {
    const members = visible
      .filter((claim) => claim.stage === stage)
      .sort(
        (a, b) =>
          priorityScore(b) - priorityScore(a) ||
          a.claim_id.localeCompare(b.claim_id),
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
  return seed.claims
    .map((claim) => claim.claim_id)
    .filter((id) => !mine.has(id));
}

// --- Story 2.2: the case file, restated independently --------------------
//
// Same discipline as the queue oracle above: every rule the case file
// applies is written out here from the story text and the prototype rather
// than read from the API or imported from the server. The two
// implementations agree in a spec or one of them is wrong.

const CASE_FILE_PATH = fileURLToPath(
  new URL("../../server/data/seed/case_file_seed.json", import.meta.url),
);

interface SeedTimelineEvent {
  claim_id: string;
  event_date: string | null;
  description: string;
  tag: string;
}

interface SeedDocument {
  claim_id: string;
  name: string;
  doc_type: string;
  filed_date: string | null;
}

interface CaseFileSeed {
  timeline_events: SeedTimelineEvent[];
  documents: SeedDocument[];
}

const caseFile = JSON.parse(
  readFileSync(CASE_FILE_PATH, "utf8"),
) as CaseFileSeed;

/** The prototype's `.slice(-6)` on the treatment overview. */
export const RECENT_TIMELINE_COUNT = 6;

/** The story's intake requirement list — restated, not read from the JDM document. */
export const REQUIRED_INTAKE_DOC_TYPES = [
  "froi",
  "incident",
  "medauth",
  "wage",
] as const;

export interface ExpectedTimelineEntry {
  eventDate: string | null;
  description: string;
  tag: string;
}

/** A claim's timeline in the order the migration appended it. */
export function expectedTimelineFor(claimId: string): ExpectedTimelineEntry[] {
  return caseFile.timeline_events
    .filter((event) => event.claim_id === claimId)
    .map((event) => ({
      eventDate: event.event_date,
      description: event.description,
      tag: event.tag,
    }));
}

/** What the treatment variant shows: the last six, or all of them. */
export function expectedRecentTimelineFor(
  claimId: string,
): ExpectedTimelineEntry[] {
  return expectedTimelineFor(claimId).slice(-RECENT_TIMELINE_COUNT);
}

export interface ExpectedChecklistRow {
  docType: string;
  received: boolean;
}

/** Received/Missing per required type, in the requirement list's order. */
export function expectedChecklistFor(claimId: string): ExpectedChecklistRow[] {
  const onFile = new Set(
    caseFile.documents
      .filter((doc) => doc.claim_id === claimId)
      .map((doc) => doc.doc_type),
  );
  return REQUIRED_INTAKE_DOC_TYPES.map((docType) => ({
    docType,
    received: onFile.has(docType),
  }));
}

/**
 * The stepper's marks for a stage — `done` before it, `current` on it.
 *
 * Restated here rather than derived from the payload so the spec can
 * disagree with the server about which steps a claim has left behind.
 */
export function expectedStepperFor(
  stage: SeedStage,
): { stage: string; state: string }[] {
  const position = STAGES.indexOf(stage);
  return STAGES.map((step, index) => ({
    stage: step,
    state:
      index < position ? "done" : index === position ? "current" : "upcoming",
  }));
}

/** The first claim of a persona's book in a given stage, by business id. */
export function firstClaimInStage(
  name: string,
  role: string,
  stage: SeedStage,
): string {
  const claims = claimsFor(name, role)
    .filter((claim) => claim.stage === stage)
    .map((claim) => claim.claim_id)
    .sort();
  if (claims.length === 0)
    throw new Error(`no seeded ${stage} claim for ${name}/${role}`);
  return claims[0];
}

/**
 * *Every* claim of a persona's book in a stage, by business id, sorted.
 *
 * Added by Story 3.2, where one claim is not enough evidence: a reserve
 * verdict computed from a schedule projection has to be right across a
 * portfolio whose injury dates span nine months — fully elapsed schedules,
 * partly paid ones, and one that has not started. A single sample can agree
 * with an oracle by luck.
 */
export function claimIdsInStage(
  name: string,
  role: string,
  stage: SeedStage,
): string[] {
  const claims = claimsFor(name, role)
    .filter((claim) => claim.stage === stage)
    .map((claim) => claim.claim_id)
    .sort();
  if (claims.length === 0)
    throw new Error(`no seeded ${stage} claim for ${name}/${role}`);
  return claims;
}

/**
 * The **last** claim of a persona's book in a stage, by business id.
 *
 * `firstClaimInStage`'s counterpart, and it exists for a specific hazard: a
 * spec file shares one database, so a test that *mutates* a claim has to pick
 * one no other test in the file reads. Reaching for "the first settled claim"
 * is the obvious move and is usually the same row `firstClaimOnPath(…, "b")`
 * returns — which turns a destructive test into a failure two tests later,
 * pointing at the wrong code (Story 2.5, found in the first e2e run).
 */
export function lastClaimInStage(
  name: string,
  role: string,
  stage: SeedStage,
): string {
  const claims = claimsFor(name, role)
    .filter((claim) => claim.stage === stage)
    .map((claim) => claim.claim_id)
    .sort();
  if (claims.length === 0)
    throw new Error(`no seeded ${stage} claim for ${name}/${role}`);
  return claims[claims.length - 1];
}

/**
 * Every claim of a persona's book carrying one assessment status, sorted (3.5).
 *
 * `claimIdsInStage`'s counterpart over the *status* column, which Story 3.5 is
 * the first spec to care about: the assessment approval is guarded on
 * `initial`/`ch_assessment_process`, and "a claim in the treatment stage" and
 * "a claim awaiting approval" are different sets — a claim can be in treatment
 * and already `ch_approved`. Reading the seed rather than hardcoding a claim id
 * keeps the spec meaningful if the dataset moves.
 */
export function claimIdsWithStatus(
  name: string,
  role: string,
  status: string,
): string[] {
  const claims = claimsFor(name, role)
    .filter((claim) => claim.status === status)
    .map((claim) => claim.claim_id)
    .sort();
  if (claims.length === 0)
    throw new Error(`no seeded ${status} claim for ${name}/${role}`);
  return claims;
}

/** Whether a seeded claim's injury is OSHA recordable — the trigger's input. */
export function isOshaRecordable(claimId: string): boolean {
  const claim = seed.claims.find((c) => c.claim_id === claimId);
  if (!claim) throw new Error(`no seeded claim ${claimId}`);
  return Boolean(claim.osha_recordable);
}

/** The risk band the gauge must be coloured by — the queue oracle's, reused. */
export function expectedRiskFor(claimId: string): "high" | "med" | "low" {
  const claim = seed.claims.find((c) => c.claim_id === claimId);
  if (!claim) throw new Error(`no seeded claim ${claimId}`);
  return riskBand(claim.severity_score);
}

/**
 * The editable vocabularies (Story 2.3), restated from the prototype.
 *
 * Written out here rather than read from the payload the spec is asserting
 * against — the whole point is that a spec can disagree with the server
 * about what the eleven regions and five windows are. The server asserts the
 * same lists against the prototype's HTML in
 * `test_claim_edit_validation.py`; these are the browser's independent copy.
 */
export const BODY_PART_OPTIONS = [
  { key: "head", label: "Head" },
  { key: "ears", label: "Ears" },
  { key: "shoulder_right", label: "Right Shoulder" },
  { key: "shoulder_left", label: "Left Shoulder" },
  { key: "forearm_right", label: "Right Forearm" },
  { key: "hand_right", label: "Right Hand" },
  { key: "hand_left", label: "Left Hand" },
  { key: "torso", label: "Torso" },
  { key: "lumbar", label: "Lower Back (Lumbar)" },
  { key: "tibia_left", label: "Left Lower Leg" },
  { key: "tibia_right", label: "Right Lower Leg" },
] as const;

/**
 * The five windows as `{token, label}` — the column holds tokens since the
 * Story 2.3 code review, and the label is the browser's.
 *
 * Restated here rather than read from the payload, like every other oracle in
 * this file: the spec has to be able to disagree with the server about both
 * halves of the mapping.
 */
export const RECOVERY_WINDOWS = [
  { token: "weeks_0_2", label: "0-2 Weeks" },
  { token: "weeks_2_4", label: "2-4 Weeks" },
  { token: "weeks_4_6", label: "4-6 Weeks" },
  { token: "weeks_6_8", label: "6-8 Weeks" },
  { token: "over_1_year", label: "Greater than 1 Year" },
] as const;

/** The token migration 0013 converts a seeded display string into. */
export function recoveryToken(display: string): string {
  const match = RECOVERY_WINDOWS.find((window) => window.label === display);
  if (!match) throw new Error(`no recovery window token for ${display}`);
  return match.token;
}

/** A window the claim is *not* already in, so an edit is a real change. */
export function otherRecoveryWindow(claimId: string): {
  token: string;
  label: string;
} {
  const current = recoveryToken(seededField(claimId, "recovery"));
  const option = RECOVERY_WINDOWS.find((window) => window.token !== current);
  if (!option)
    throw new Error("the recovery vocabulary has fewer than two members");
  return option;
}

/** A seeded claim's stored value for one of the editable columns. */
export function seededField(
  claimId: string,
  field:
    | "injury_type"
    | "cause"
    | "body_key"
    | "body_part"
    | "icd"
    | "recovery"
    | "disability",
): string {
  const claim = seed.claims.find((c) => c.claim_id === claimId);
  if (!claim) throw new Error(`no seeded claim ${claimId}`);
  return String((claim as unknown as Record<string, unknown>)[field]);
}

/** A body key the claim is *not* already assigned to, so an edit is a change. */
export function otherBodyKey(claimId: string): { key: string; label: string } {
  const current = seededField(claimId, "body_key");
  const option = BODY_PART_OPTIONS.find((o) => o.key !== current);
  if (!option)
    throw new Error("the body-part vocabulary has fewer than two members");
  return { key: option.key, label: option.label };
}

// --- Story 2.4: the injury diagram, restated independently ---------------
//
// The treatment plan and the prognosis come from `seed_data.json`'s
// `deferred` block — the part of the prototype's dataset Story 1.2 extracted
// but had no columns for until migration 0015. Read here and transformed
// independently, like every other oracle in this file.

interface SeedDeferred {
  claim_id: string;
  treatment_plan: string[];
  prognosis: {
    mmi: string;
    rtw: string;
    impairment: string;
    litigation: string;
  };
}

const deferred = new Map(
  ((seed as unknown as { deferred: SeedDeferred[] }).deferred ?? []).map(
    (row) => [row.claim_id, row],
  ),
);

function deferredFor(claimId: string): SeedDeferred {
  const row = deferred.get(claimId);
  if (!row) throw new Error(`no seeded deferred block for ${claimId}`);
  return row;
}

/** A claim's treatment-plan steps, in the array's order (= `step_no` order). */
export function expectedTreatmentPlan(claimId: string): string[] {
  return deferredFor(claimId).treatment_plan.slice();
}

/** A claim's four prognosis strings. */
export function expectedPrognosis(claimId: string): SeedDeferred["prognosis"] {
  return { ...deferredFor(claimId).prognosis };
}

/** A claim's restrictions text — the `contraindications` column. */
export function expectedContraindications(claimId: string): string {
  const claim = seed.claims.find((c) => c.claim_id === claimId) as unknown as
    { contraindications?: string } | undefined;
  if (!claim?.contraindications)
    throw new Error(`no seeded contraindications for ${claimId}`);
  return claim.contraindications;
}

/**
 * The seeded severity score, and the band it must be drawn in.
 *
 * The band is `riskBand`'s — the same restatement the queue oracle uses, so
 * the diagram's marker colour is checked against the *console's* rule rather
 * than against the prototype's second pair of cut-offs (which this build
 * deliberately does not port).
 */
export function expectedPrimaryMarker(claimId: string): {
  bodyKey: string;
  severityScore: number;
  band: "high" | "med" | "low";
} {
  const claim = seed.claims.find((c) => c.claim_id === claimId);
  if (!claim) throw new Error(`no seeded claim ${claimId}`);
  return {
    bodyKey: (claim as unknown as { body_key: string }).body_key,
    severityScore: claim.severity_score,
    band: riskBand(claim.severity_score),
  };
}

// --- Story 2.5: statutory forms and documents, restated independently ----
//
// The classification rule is written out here from the story text rather than
// imported from the server or read off the response, exactly as the queue's
// five derivations are. It matters more than usual: the thing under test is a
// rule the prototype never actually applies, so an oracle that read the path
// off the payload would confirm nothing at all.

const PATH_FORMS_PATH = fileURLToPath(
  new URL("../../server/data/seed/path_required_forms.json", import.meta.url),
);

interface SeedRequiredForm {
  path: string;
  form_code: string;
  form_name: string;
  description: string;
  timing: string;
  download_url: string;
  sort_order: number;
}

const requiredForms = JSON.parse(
  readFileSync(PATH_FORMS_PATH, "utf8"),
) as SeedRequiredForm[];

/**
 * The deployed `derivation_thresholds` v3 path parameters, restated.
 *
 * A stack migrated with different ones would legitimately fail these specs,
 * which is the point of writing them down here rather than reading them off
 * the response.
 */
const PATH_MINOR_SEVERITY_MAX = 35;
const PATH_MINOR_RECOVERY_WINDOWS = ["weeks_0_2"];
const PATH_FATALITY_SEVERITY_MIN = 100;

export type SeedPath = "a" | "b" | "c";

/**
 * `services/derivations/path_classification.py`, restated.
 *
 * Most severe first, as the implementation tests it — and B is the fallback,
 * which is also the prototype's default and the reason its bug was invisible.
 */
export function claimPath(claimId: string): SeedPath {
  const claim = seed.claims.find((c) => c.claim_id === claimId) as unknown as
    | {
        severity_score: number;
        disability: string;
        recovery: string;
        surgery_required: boolean;
        return_status: string;
      }
    | undefined;
  if (!claim) throw new Error(`no seeded claim ${claimId}`);

  if (
    claim.severity_score >= PATH_FATALITY_SEVERITY_MIN &&
    claim.disability === "permanent" &&
    claim.return_status === UNDER_TREATMENT
  ) {
    return "c";
  }
  if (
    claim.severity_score < PATH_MINOR_SEVERITY_MAX &&
    !claim.surgery_required &&
    claim.disability === "temporary" &&
    PATH_MINOR_RECOVERY_WINDOWS.includes(recoveryToken(claim.recovery))
  ) {
    return "a";
  }
  return "b";
}

/** The form codes one path requires, in the order the card renders them. */
export function expectedFormCodes(path: SeedPath): string[] {
  return requiredForms
    .filter((form) => form.path === path)
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((form) => form.form_code);
}

/** One form's full row, for asserting the description and timing render. */
export function expectedForm(
  path: SeedPath,
  formCode: string,
): SeedRequiredForm {
  const form = requiredForms.find(
    (f) => f.path === path && f.form_code === formCode,
  );
  if (!form) throw new Error(`no seeded ${formCode} on path ${path}`);
  return form;
}

/**
 * The first claim of a persona's book on a given path, by business id.
 *
 * Throws rather than returning undefined: a spec that silently skipped its
 * Path-A case because the seed had none would be a green tick over an
 * untested banner.
 */
export function firstClaimOnPath(
  name: string,
  role: string,
  path: SeedPath,
): string {
  const claims = claimsFor(name, role)
    .map((claim) => claim.claim_id)
    .filter((id) => claimPath(id) === path)
    .sort();
  if (claims.length === 0)
    throw new Error(`no seeded path-${path} claim for ${name}/${role}`);
  return claims[0];
}

/** A claim's documents, in the order the migration filed them. */
export function expectedDocumentsFor(claimId: string): SeedDocument[] {
  return caseFile.documents.filter((doc) => doc.claim_id === claimId);
}

/** The ID card's fields, read from the claim and employee rows. */
export function expectedIdCardFor(claimId: string): {
  policyNum: string;
  doi: string;
  plant: string;
  state: string;
  region: string;
} {
  const claim = seed.claims.find((c) => c.claim_id === claimId) as unknown as
    | {
        policy_num: string;
        doi: string;
        plant: string;
        state: string;
        region: string;
      }
    | undefined;
  if (!claim) throw new Error(`no seeded claim ${claimId}`);
  return {
    policyNum: claim.policy_num,
    doi: claim.doi,
    plant: claim.plant,
    state: claim.state,
    region: claim.region,
  };
}

/** A score that lands in a band the claim is not currently in. */
export function scoreInAnotherBand(claimId: string): {
  score: number;
  band: string;
} {
  const current = expectedPrimaryMarker(claimId).band;
  // 0 and 100 are the ends of the domain, so one of them is always in a
  // different band from any claim — and both are legal values, so the test
  // exercises the command rather than its refusal.
  const candidate = current === "high" ? 0 : 100;
  return { score: candidate, band: riskBand(candidate) };
}

/* --- Story 2.6: incident photos ---------------------------------------- */

const PHOTOS_PATH = fileURLToPath(
  new URL("../../server/data/seed/photos.json", import.meta.url),
);

interface SeedPhoto {
  claim_id: string;
  caption: string;
  source: string;
}

const photos = JSON.parse(readFileSync(PHOTOS_PATH, "utf8")) as SeedPhoto[];

/**
 * A claim's photos, in the order the migration filed them.
 *
 * Order is the assertion, not a convenience: `photo` carries no `sort_order`
 * because the grid renders the prototype's array order, which the seed
 * preserved by inserting in it. A spec comparing sets would pass against a
 * shuffled grid.
 */
export function expectedPhotosFor(claimId: string): SeedPhoto[] {
  return photos.filter((photo) => photo.claim_id === claimId);
}

/**
 * A claim of the persona's book with the **most** photos.
 *
 * Used by the viewer test so that "click the second card" is never "click the
 * only card": every seeded claim has two to four, and a test that happened to
 * pick a two-photo claim would still pass while asserting less.
 */
export function claimWithMostPhotos(name: string, role: string): string {
  const mine = claimsFor(name, role).map((claim) => claim.claim_id);
  const best = mine
    .map((claimId) => ({ claimId, n: expectedPhotosFor(claimId).length }))
    .sort((a, b) => b.n - a.n || a.claimId.localeCompare(b.claimId))[0];
  if (!best || best.n === 0)
    throw new Error(`no photographed claim for ${name}/${role}`);
  return best.claimId;
}

/* --- Story 3.1: the statutory benefit calculation ---------------------- */

const STATE_RATES_PATH = fileURLToPath(
  new URL("../../server/data/seed/state_rates.json", import.meta.url),
);

interface SeedStateRate {
  state_code: string;
  state_name: string;
  weekly_min_cents: number;
  weekly_max_cents: number;
}

const stateRates = JSON.parse(
  readFileSync(STATE_RATES_PATH, "utf8"),
) as SeedStateRate[];

// --- Story 3.3: bills and expenses ---------------------------------------

const BILLS_PATH = fileURLToPath(
  new URL("../../server/data/seed/bills.json", import.meta.url),
);
const EXPENSES_PATH = fileURLToPath(
  new URL("../../server/data/seed/expenses.json", import.meta.url),
);

export interface SeedLineItem {
  claim_id: string;
  category: string;
  label: string;
  amount_cents: number;
  status: string;
}

const bills = JSON.parse(readFileSync(BILLS_PATH, "utf8")) as SeedLineItem[];
const expenses = JSON.parse(
  readFileSync(EXPENSES_PATH, "utf8"),
) as SeedLineItem[];

/** A claim's medical bills, in the order the seed migration inserted them. */
export function expectedBills(claimId: string): SeedLineItem[] {
  return bills.filter((bill) => bill.claim_id === claimId);
}

/** A claim's expenses, in seeded order. */
export function expectedExpenses(claimId: string): SeedLineItem[] {
  return expenses.filter((expense) => expense.claim_id === claimId);
}

function sumWhere(items: SeedLineItem[], paid: boolean): number {
  return items
    .filter((item) => (item.status === "paid") === paid)
    .reduce((total, item) => total + item.amount_cents, 0);
}

/** What the bills card's heading must state: count, paid, total. */
export function expectedBillTotals(claimId: string): {
  count: number;
  paidCents: number;
  totalCents: number;
} {
  const items = expectedBills(claimId);
  return {
    count: items.length,
    paidCents: sumWhere(items, true),
    totalCents: items.reduce((total, item) => total + item.amount_cents, 0),
  };
}

/** The same three for the expenses card. */
export function expectedExpenseTotals(claimId: string): {
  count: number;
  paidCents: number;
  totalCents: number;
} {
  const items = expectedExpenses(claimId);
  return {
    count: items.length,
    paidCents: sumWhere(items, true),
    totalCents: items.reduce((total, item) => total + item.amount_cents, 0),
  };
}

/**
 * The claim's unpaid medical exposure — the reserve check's second term.
 *
 * Story 3.2's oracle hardcoded `null` here because the `bill` table did not
 * exist; this is the half it said it would grow when 3.3 landed its seed.
 */
export function unpaidMedicalCents(claimId: string): number {
  return sumWhere(expectedBills(claimId), false);
}

/**
 * What the summary's "Total Paid To Date" must be, and its three components.
 *
 * The prototype's effective-breakdown rule, restated: the `paid_*` columns
 * answer when their **sum** is positive, and the live figures answer when it
 * is not. All-or-nothing on the sum rather than per component, which is the
 * part a re-implementation gets wrong — see the server's
 * `PaidToDateDerivation`.
 */
export function expectedPaidToDate(claimId: string): {
  totalCents: number;
  indemnityCents: number;
  medicalCents: number;
  expenseCents: number;
  fromColumns: boolean;
} {
  const claim = seed.claims.find((c) => c.claim_id === claimId);
  if (!claim) throw new Error(`no seeded claim ${claimId}`);

  const staticTotal =
    claim.paid_indemnity + claim.paid_medical + claim.paid_expense;
  if (staticTotal > 0) {
    return {
      totalCents: staticTotal,
      indemnityCents: claim.paid_indemnity,
      medicalCents: claim.paid_medical,
      expenseCents: claim.paid_expense,
      fromColumns: true,
    };
  }

  const indemnityCents = expectedReserveCheck(claimId).disbursedIndemnityCents;
  const medicalCents = sumWhere(expectedBills(claimId), true);
  const expenseCents = sumWhere(expectedExpenses(claimId), true);
  return {
    totalCents: indemnityCents + medicalCents + expenseCents,
    indemnityCents,
    medicalCents,
    expenseCents,
    fromColumns: false,
  };
}

/** How many weeks a claim's schedule runs for — the generator's clamp. */
export function expectedWeekCount(claimId: string): number {
  const claim = seed.claims.find((c) => c.claim_id === claimId);
  if (!claim) throw new Error(`no seeded claim ${claimId}`);
  return Math.max(
    MIN_SCHEDULE_WEEKS,
    Math.min(MAX_SCHEDULE_WEEKS, SCHEDULE_WEEKS[recoveryToken(claim.recovery)]),
  );
}

/** The UI's schedule-status labels — `labels.ts`, restated. */
export const SCHEDULE_STATUS_LABEL: Record<string, string> = {
  pending_approval: "Pending Approval",
  due_this_week: "Due This Week",
  upcoming: "Upcoming",
  payment_scheduled: "Payment Scheduled",
  paid: "Paid",
};

/** The UI's line-item status labels — `labels.ts`, restated. */
export const LINE_ITEM_STATUS_LABEL: Record<string, string> = {
  pending_submission: "Pending Submission",
  under_review: "Under Review",
  payment_scheduled: "Payment Scheduled",
  paid: "Paid",
};

/**
 * The benefit rule, restated independently — `expectedRiskFor`'s discipline.
 *
 * These four constants are `benefit_params` v1 and `derivation_thresholds`
 * v4's PTD cut-off, written out rather than read from the documents: a spec
 * that loaded the rule it is testing would agree with any rule at all. The
 * arithmetic below is a second implementation of `compute_benefit` in
 * TypeScript for the same reason.
 */
const DEFAULT_COMP_RATE_BP = 6667;
const PTD_COMP_RATE_BP = 10_000;
const PTD_SEVERITY_THRESHOLD = 85;
const BASIS_POINTS_PER_UNIT = 10_000;

/** The date migration 0023 supplies, which the card cites as provenance. */
export const SCHEDULE_EFFECTIVE_FROM = "2026-01-01";

export function expectedStateRate(claimId: string): SeedStateRate {
  const claim = seed.claims.find((c) => c.claim_id === claimId);
  if (!claim) throw new Error(`no seeded claim ${claimId}`);
  const rate = stateRates.find((r) => r.state_code === claim.state);
  // The prototype falls back to `{max:1200,min:250}` here; this throws,
  // because a jurisdiction with no schedule is the thing Story 3.1 refuses to
  // paper over and a spec that quietly invented bounds would hide it.
  if (!rate) throw new Error(`no statutory rate schedule for ${claim.state}`);
  return rate;
}

export interface ExpectedBenefit {
  weeklyCents: number;
  compRateBp: number;
  defaultCompRateBp: number;
  indemnityType: "ttd" | "tpd" | "ppd" | "ptd";
  stateMinCents: number;
  stateMaxCents: number;
  stateName: string;
}

/** Half up on the cents, matching the engine's one rounding convention. */
function roundHalfUp(value: number, divisor: number): number {
  const quotient = Math.floor(value / divisor);
  const remainder = value - quotient * divisor;
  return remainder * 2 >= divisor ? quotient + 1 : quotient;
}

/**
 * What the card must show for a claim, at the default rate or an override.
 *
 * Written out step by step rather than delegating, because every step is an
 * assertion: the PTD branch, the rate it selects, the rounding and the clamp.
 */
export function expectedBenefit(
  claimId: string,
  overrideBp?: number,
): ExpectedBenefit {
  const claim = seed.claims.find((c) => c.claim_id === claimId);
  if (!claim) throw new Error(`no seeded claim ${claimId}`);
  const rate = expectedStateRate(claimId);

  const permanent = claim.disability === "permanent";
  const isPtd = permanent && claim.severity_score >= PTD_SEVERITY_THRESHOLD;
  const indemnityType: ExpectedBenefit["indemnityType"] = permanent
    ? isPtd
      ? "ptd"
      : "ppd"
    : claim.return_status === "returned_and_under_therapy"
      ? "tpd"
      : "ttd";

  const defaultCompRateBp = isPtd ? PTD_COMP_RATE_BP : DEFAULT_COMP_RATE_BP;
  const compRateBp = overrideBp ?? defaultCompRateBp;
  const unclamped = roundHalfUp(claim.aww * compRateBp, BASIS_POINTS_PER_UNIT);

  return {
    weeklyCents: Math.max(
      rate.weekly_min_cents,
      Math.min(rate.weekly_max_cents, unclamped),
    ),
    compRateBp,
    defaultCompRateBp,
    indemnityType,
    stateMinCents: rate.weekly_min_cents,
    stateMaxCents: rate.weekly_max_cents,
    stateName: rate.state_name,
  };
}

/**
 * Whole dollars with separators — `web/src/lib/money.ts`'s `formatCents`,
 * restated so a spec can assert what is on screen rather than what is on the
 * wire.
 */
export function formatCents(cents: number): string {
  return `$${Math.round(cents / 100).toLocaleString("en-US")}`;
}

/** Basis points as the card renders them — `lib/rate.ts`, restated. */
export function formatBasisPoints(basisPoints: number): string {
  const whole = Math.trunc(basisPoints / 100);
  const hundredths = Math.abs(basisPoints % 100);
  return `${whole}.${String(hundredths).padStart(2, "0")}`;
}

/* --- Story 3.2: the reserve adequacy check ----------------------------- */

/**
 * The reserve rule, restated independently — `expectedBenefit`'s discipline.
 *
 * These are `reserve_bands` v1 and the schedule constants from
 * `services/financials/schedule.py`, written out rather than read from the
 * document and the module: a spec that loaded the rule it is testing would
 * agree with any rule at all. The projection below is a second implementation
 * of `project_payments` in TypeScript for the same reason.
 */
const LIGHT_RATIO_BP = 11_500;
const HEAVY_RATIO_BP = 6_000;
const WAITING_PERIOD_DAYS = 7;
const MIN_SCHEDULE_WEEKS = 4;
const MAX_SCHEDULE_WEEKS = 20;
const SCHEDULE_WEEKS: Record<string, number> = {
  weeks_0_2: 2,
  weeks_2_4: 4,
  weeks_4_6: 6,
  weeks_6_8: 8,
  over_1_year: 26,
};

const DAY_MS = 86_400_000;

export type SeedVerdict =
  "light" | "adequate" | "heavy" | "closed_final" | "indeterminate";

export interface ExpectedReserveCheck {
  verdict: SeedVerdict;
  remainingIndemnityCents: number;
  /** A real sum since Story 3.3 seeded `bill`. `null` would mean "not on file". */
  remainingMedicalCents: number | null;
  /** `null` with it: a total missing a term is not a total. */
  projectedRemainingCents: number | null;
  scheduledIndemnityCents: number;
  disbursedIndemnityCents: number;
  /** The paid half of the bill list whose unpaid half is the medical term. */
  disbursedMedicalCents: number;
  reserveCents: number;
}

/** Midnight UTC today — `daysOpen`'s clock, and for its reason. */
function utcToday(): number {
  const now = new Date();
  return Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
}

function utcDate(iso: string): number {
  const [year, month, day] = iso.split("-").map(Number);
  return Date.UTC(year, month - 1, day);
}

/**
 * What the card must say about a claim's reserve, computed from the seed.
 *
 * **Both exposure terms are on file since Story 3.3.** The 3.2 oracle carried
 * `null` for the medical half and said it would grow the second term when the
 * `bill` table landed; `unpaidMedicalCents` is that term, summed from the same
 * `bills.json` the seed migration inserted. Every open claim therefore gets a
 * complete verdict, and `indeterminate` is unreachable against this seed.
 *
 * **What this oracle is and is not independent of** (code review, 2026-08-14).
 * It is a second implementation, so it catches an arithmetic slip, a boundary
 * flipped from strict to inclusive, a clamp applied in the wrong order or a
 * week counted twice. It does **not** cross-check the modelling assumption it
 * shares with the server: that a week whose end date has passed on an approved
 * claim counts as disbursed. That is the prototype's rule (`buildPaymentSchedule`,
 * and `billsHTML`'s note that the static `paid_indemnity` column "is often 0
 * even though the schedule already show[s] real disbursements"), ported
 * deliberately — but an oracle that restates it cannot also be evidence for
 * it, and saying otherwise would be the more comfortable claim rather than the
 * true one. The assumption is checked where it is checkable: against what the
 * card renders, in the spec's contradiction test.
 */
export function expectedReserveCheck(claimId: string): ExpectedReserveCheck {
  const claim = seed.claims.find((c) => c.claim_id === claimId);
  if (!claim) throw new Error(`no seeded claim ${claimId}`);

  const reserveCents = claim.reserve;
  // Story 3.3 seeded `bill`, so this is a real sum rather than the `null` the
  // 3.2 oracle carried. `0` is now a legitimate answer — a claim whose bills
  // are all paid has no unpaid medical exposure — and the `null` branch below
  // survives only because the server's typed distinction does.
  const remainingMedicalCents: number | null = unpaidMedicalCents(claimId);

  if (claim.stage === "settled") {
    const scheduled =
      expectedBenefit(claimId).weeklyCents *
      Math.max(
        MIN_SCHEDULE_WEEKS,
        Math.min(
          MAX_SCHEDULE_WEEKS,
          SCHEDULE_WEEKS[recoveryToken(claim.recovery)],
        ),
      );
    return {
      verdict: "closed_final",
      remainingIndemnityCents: 0,
      remainingMedicalCents,
      // A settled claim is short-circuited before any band arithmetic, so the
      // total is still reported — it is the *ratio* that is withheld. Written
      // as the sum of its two terms rather than as `remainingMedicalCents`,
      // although the indemnity half is zero here: the server publishes the
      // computed figures rather than hardcoded zeroes, precisely so a settled
      // claim with an unpaid bill shows up instead of being asserted away.
      projectedRemainingCents: 0 + remainingMedicalCents,
      // A settled claim's every week is paid, which is why nothing remains.
      scheduledIndemnityCents: scheduled,
      disbursedIndemnityCents: scheduled,
      disbursedMedicalCents: sumWhere(expectedBills(claimId), true),
      reserveCents,
    };
  }

  const weeks = Math.max(
    MIN_SCHEDULE_WEEKS,
    Math.min(MAX_SCHEDULE_WEEKS, SCHEDULE_WEEKS[recoveryToken(claim.recovery)]),
  );
  const weekly = expectedBenefit(claimId).weeklyCents;
  const firstStart = utcDate(claim.doi) + WAITING_PERIOD_DAYS * DAY_MS;
  const unapproved =
    claim.stage === "intake" || claim.stage === "investigation";
  const today = utcToday();

  let paid = 0;
  for (let i = 0; i < weeks; i += 1) {
    const end = firstStart + (i * 7 + 6) * DAY_MS;
    if (!unapproved && end < today) paid += weekly;
  }

  const remainingIndemnityCents = Math.max(0, weekly * weeks - paid);
  // The exposure that is *known*. With the bills unseen this is a lower bound
  // on the real figure, which is exactly why only one band survives it.
  const known = remainingIndemnityCents + (remainingMedicalCents ?? 0);

  // Cross-multiplied, exactly as `classify_reserve` does — a float ratio here
  // would make the oracle disagree with the server at the boundary, which is
  // the one place the spec is worth having.
  let light = false;
  let heavy = false;
  if (reserveCents > 0) {
    light = known * BASIS_POINTS_PER_UNIT > LIGHT_RATIO_BP * reserveCents;
    heavy = known * BASIS_POINTS_PER_UNIT < HEAVY_RATIO_BP * reserveCents;
  } else {
    light = known > 0;
  }

  // An unknown non-negative term cannot pull an exposure back under a
  // threshold it has already passed, so `light` stands on a lower bound while
  // `adequate` and `heavy` — both claims about an upper bound — are withheld.
  const complete = remainingMedicalCents !== null;
  let verdict: SeedVerdict;
  if (light) verdict = "light";
  else if (!complete) verdict = "indeterminate";
  else if (heavy) verdict = "heavy";
  else verdict = "adequate";

  return {
    verdict,
    remainingIndemnityCents,
    remainingMedicalCents,
    projectedRemainingCents: complete ? known : null,
    scheduledIndemnityCents: weekly * weeks,
    disbursedIndemnityCents: paid,
    disbursedMedicalCents: sumWhere(expectedBills(claimId), true),
    reserveCents,
  };
}

/** The label the card renders for a verdict — `labels.ts`, restated. */
export const RESERVE_VERDICT_LABEL: Record<SeedVerdict, string> = {
  light: "Reserve Light",
  adequate: "Reserve Adequate",
  heavy: "Reserve Heavy",
  closed_final: "Closed — Final",
  indeterminate: "Awaiting Bill Data",
};

// --- Story 4.1: the two demo meetings per handler persona ----------------

/**
 * What migration 0033 seeds, restated from `seed_data.json`.
 *
 * The rule: each handler persona gets two meetings, linked to the two
 * lowest-sorted claim business ids **in their employer scope**, typed
 * `rtw_conference` and `claim_review_supervisor` in that order.
 *
 * Independent of the migration, like every other oracle here: this walks the
 * same seed file the stack migrated with and applies the rule again, so a
 * migration that picked different claims — or seeded three meetings, or one —
 * disagrees with this rather than agreeing with itself.
 *
 * The **date** is deliberately not part of the expectation. The migration
 * stamps its own run date, which is whenever the image was built; a static
 * oracle that named a day would be wrong the morning after. What the spec
 * asserts instead is the type, the claim and the count.
 */
export const DEMO_MEETING_TYPES = [
  "rtw_conference",
  "claim_review_supervisor",
] as const;

export interface ExpectedMeeting {
  claimId: string;
  meetingType: (typeof DEMO_MEETING_TYPES)[number];
}

export function expectedMeetingsFor(
  name: string,
  role: string,
): ExpectedMeeting[] {
  const claimIds = claimsFor(name, role)
    .map((claim) => claim.claim_id)
    .sort((left, right) => left.localeCompare(right));

  if (claimIds.length < DEMO_MEETING_TYPES.length) {
    throw new Error(
      `${name}/${role} has ${claimIds.length} scoped claims; Story 4.1 AC 6 seeds two meetings`,
    );
  }

  return DEMO_MEETING_TYPES.map((meetingType, index) => ({
    claimId: claimIds[index],
    meetingType,
  }));
}

/**
 * The ten scheduler labels — `features/diary/labels.ts`, restated.
 *
 * A second copy on purpose: a spec that imported the app's own map would
 * assert that the map equals itself. These are the words a handler reads, and
 * they are the prototype's (lines 541-551).
 */
export const MEETING_TYPE_LABEL: Record<string, string> = {
  three_point_contact_initial: "3-Point Contact — Initial",
  rtw_conference: "RTW Conference",
  ncm_care_coordination: "NCM Care Coordination",
  ime_preparation: "IME Preparation",
  settlement_discussion: "Settlement Discussion",
  physician_consultation: "Physician Consultation",
  employer_accommodation_review: "Employer Accommodation Review",
  litigation_prep: "Litigation Prep",
  claim_review_supervisor: "Claim Review — Supervisor",
  other: "Other",
};

/**
 * Claims whose worker is back on restricted capacity — the `modified_duty`
 * rule's trigger, and therefore the claims whose checklist carries a
 * `meetings` action (Story 4.1's half of Story 3.5's seam).
 *
 * The *condition* is restated here rather than the action list: which rows a
 * claim produces is eleven rules and belongs in
 * `server/tests/test_action_checklist.py`, but "which claim can demonstrate
 * the deep link" has to be answerable from the seed or the spec is reduced to
 * hunting through a book for one.
 */
export function claimIdsOnModifiedDuty(name: string, role: string): string[] {
  const claims = claimsFor(name, role)
    .filter((claim) => claim.return_status === "returned_and_under_therapy")
    .map((claim) => claim.claim_id)
    .sort((left, right) => left.localeCompare(right));
  if (claims.length === 0) {
    throw new Error(`no seeded modified-duty claim for ${name}/${role}`);
  }
  return claims;
}

// --- Story 4.3: the six seeded stakeholder templates ---------------------

/**
 * What migration 0036 seeds, **restated** rather than read from it.
 *
 * The house rule every oracle in this file follows, and it bites hardest here:
 * the templates are *reference data*, so a fixture that imported the migration's
 * `EMAIL_TEMPLATES` tuple — or read the six rows back off
 * `GET /claims-diary/email-templates` — would assert that the seed equals
 * itself. A default recipient set silently changed in the migration would then
 * still pass, while the handler's checkbox grid quietly addressed a different
 * set of people. Written out here, the spec can disagree.
 *
 * Three facts per template and no more: the key (the wire's identity), the
 * label (the button's words, UI-owned) and the default recipient set (what the
 * checkbox grid becomes when the button is pressed). The subject and body text
 * is deliberately **not** restated — it is four hundred lines of prose whose
 * merged form is `server/tests/test_emails.py`'s business, and what a browser
 * can uniquely say about it is that no `{{…}}` survived the merge and that the
 * claim under the cursor is named in it.
 *
 * The order is the composer's button order, which is the prototype's object
 * order and the order the seed inserts in.
 * [Source: docs/Workers_Comp_Prototype.html lines 599-604, 1955-1999]
 */
export const EMAIL_TEMPLATE_KEYS = [
  "three_point_contact",
  "rtw_offer",
  "ncm_referral",
  "status_update",
  "ime_request",
  "settlement_notice",
] as const;

export type EmailTemplateKey = (typeof EMAIL_TEMPLATE_KEYS)[number];

/** The six buttons' words — `features/diary` owns labels, so these are theirs. */
export const EMAIL_TEMPLATE_LABEL: Record<EmailTemplateKey, string> = {
  three_point_contact: "3-Point Contact",
  rtw_offer: "RTW Offer",
  ncm_referral: "NCM Referral",
  status_update: "Status Update",
  ime_request: "IME Request",
  settlement_notice: "Settlement Notice",
};

/**
 * Who each letter is addressed to by default.
 *
 * The values are `MeetingParticipant`'s — Story 4.3 reuses Story 4.1's six-value
 * vocabulary rather than declaring a second one, so `treating_physician` and
 * `employer_hr` are spelled the long way here even though the prototype's email
 * modal keyed the same two roles `physician` and `employer`.
 *
 * **A set, not a list.** The seed now writes every row in the vocabulary's own
 * order — `ncm_referral` was `ncm · treating_physician · employer_hr` until the
 * 4.3 review made the seed keep the invariant its own docstring claimed — but the
 * composer holds the ticks in a `Set` and the server re-orders on the way in, so
 * ordering is not part of the contract and an oracle that pinned one would be
 * asserting an implementation detail rather than a promise.
 * `expectedTemplateRecipients` therefore sorts, and every caller compares sorted.
 */
const TEMPLATE_RECIPIENTS: Record<EmailTemplateKey, readonly string[]> = {
  three_point_contact: ["employee", "employer_hr", "treating_physician"],
  rtw_offer: ["employee", "employer_hr", "ncm"],
  ncm_referral: ["ncm", "treating_physician", "employer_hr"],
  status_update: ["employer_hr", "supervisor"],
  ime_request: ["treating_physician", "ncm"],
  settlement_notice: ["employee", "attorney"],
};

/** One template's default recipient set, sorted — see `TEMPLATE_RECIPIENTS`. */
export function expectedTemplateRecipients(key: EmailTemplateKey): string[] {
  return [...TEMPLATE_RECIPIENTS[key]].sort();
}

/**
 * What a blank composition opens with — Employee alone (UX-DR10).
 *
 * The one recipient almost every letter has, and the only tick the prototype's
 * modal ships pre-set. Restated here so the spec can assert the *starting* set
 * as well as the set a template replaces it with, which is what makes "replaced,
 * never unioned" a real assertion.
 */
export const BLANK_COMPOSE_RECIPIENTS = ["employee"] as const;

/**
 * Story 5.1 — the portfolio KPI cards, restated independently.
 *
 * The oracle returns what the *cards read*, not what the endpoint answers:
 * formatted strings, keyed by the `data-testid` stem the spec looks each card
 * up under. A spec comparing numbers would still pass if the page divided
 * cents by a hundred twice.
 *
 * Two rules are restated here rather than imported, `HIGH_RISK_MIN`'s
 * discipline extended one story:
 *
 * - `FRAUD_FLAG_SCORE_MIN` is the dashboard's *review* threshold and is
 *   deliberately **not** `SIU_FRAUD_SCORE_MIN` above, which is the queue's
 *   *referral* threshold over the same column pair. Reusing that constant here
 *   would make the single most plausible wiring mistake in this story invisible
 *   to the one suite that runs against a real browser and a real database.
 * - `SETTLED_STAGE` is the stage, not the status. `renderSV` filters
 *   `status === "Settled & Closed"` (54 seeded claims); the console groups by
 *   stage everywhere (62). The delta is a recorded departure from the
 *   prototype, so the oracle states the shipped rule and the Dev Agent Record
 *   states why.
 */
const FRAUD_FLAG_SCORE_MIN = 55;
const SETTLED_STAGE = "settled";
const TREATMENT_STAGE = "treatment";

/**
 * `web/src/lib/money.ts::formatCents`, restated.
 *
 * The same `Intl` options rather than an import: the point of the assertion is
 * that the page turned the server's cents into these characters, and an oracle
 * that called the shipped formatter would agree with it whatever it did.
 */
const DOLLARS = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

export interface ExpectedPortfolioSummary {
  /** `data-testid` stem → the exact text that card's `-value` element must hold. */
  cards: Record<string, string>;
  /** The dataset chip's whole text, counts included. */
  chip: string;
  /** Pulled out so a spec can compare two personas' portfolios directly. */
  totalClaims: number;
}

export function expectedPortfolioSummaryFor(persona: {
  name: string;
  role: string;
}): ExpectedPortfolioSummary {
  const visible = claimsFor(persona.name, persona.role);
  const count = (predicate: (claim: SeedClaim) => boolean): number =>
    visible.filter(predicate).length;
  const sum = (amount: (claim: SeedClaim) => number): number =>
    visible.reduce((total, claim) => total + amount(claim), 0);

  const employers = new Set(visible.map((claim) => claim.employer));
  const plants = new Set(visible.map((claim) => claim.plant));

  return {
    cards: {
      "kpi-total-claims": String(visible.length),
      "kpi-under-treatment": String(count((c) => c.stage === TREATMENT_STAGE)),
      "kpi-settled-closed": String(count((c) => c.stage === SETTLED_STAGE)),
      "kpi-high-risk": String(count((c) => c.severity_score >= HIGH_RISK_MIN)),
      "kpi-total-paid": DOLLARS.format(
        sum((c) => c.paid_indemnity + c.paid_medical + c.paid_expense) / 100,
      ),
      "kpi-total-reserve": DOLLARS.format(sum((c) => c.reserve) / 100),
      "kpi-fraud-flags": String(
        count((c) => c.fraud_flag && c.fraud_score >= FRAUD_FLAG_SCORE_MIN),
      ),
      "kpi-osha-recordable": String(count((c) => c.osha_recordable)),
      "kpi-litigation": String(count((c) => c.litigation_flag)),
      "kpi-surgery-required": String(count((c) => c.surgery_required)),
    },
    chip:
      `📊 WC_Manufacturing_Claims_2026.xlsx · ${visible.length} claims · ` +
      `${employers.size} employers · ${plants.size} plants`,
    totalClaims: visible.length,
  };
}

/** The two rule captions, restated from the thresholds above (AC 2). */
export const EXPECTED_KPI_CAPTIONS = {
  "kpi-high-risk": `Severity ≥ ${HIGH_RISK_MIN}/100`,
  "kpi-fraud-flags": `Score ≥ ${FRAUD_FLAG_SCORE_MIN} — review needed`,
} as const;

/**
 * Story 5.2 — the handler performance table, restated independently.
 *
 * `expectedPortfolioSummaryFor`'s discipline over a much larger rule: a
 * benchmark row is the product of three segment averages, a substitution rule,
 * a percentage deviation, a three-way band, a weighted blend, a cap, a second
 * three-way band and a sort. Every one of those is written out here from the
 * story text and the prototype (`renderSV`, lines 1015-1050) rather than read
 * off the response, because an oracle sharing any one of them with the
 * implementation would be validating the rest by accident.
 *
 * The eight constants below are the deployed `handler_performance` document. A
 * stack migrated with different ones would legitimately fail these specs, which
 * is the point of writing them down here rather than reading them off the
 * response.
 *
 * `COMPLEXITY_HIGH_MIN` is 65 and so is `HIGH_RISK_MIN` at the top of this
 * file. Two constants, deliberately: one bands a claim's severity score, the
 * other bands a handler's blended mix, and sharing them here would hide the
 * most plausible way to get this story wrong.
 */
const ON_TRACK_DEVIATION_PCT_MAX = -8;
const ATTENTION_DEVIATION_PCT_MIN = 8;
const SEVERITY_WEIGHT_BP = 10_000;
const SURGERY_RATE_WEIGHT_BP = 1_000;
const LITIGATION_RATE_WEIGHT_BP = 1_500;
const COMPLEXITY_SCORE_MAX = 100;
const COMPLEXITY_HIGH_MIN = 65;
const COMPLEXITY_MED_MIN = 40;

const BASIS_POINTS_PER_UNIT_BLEND = 10_000;
const PERCENT = 100;
/** The precision the composite is *published* at — see `compositeOf`. */
const COMPOSITE_TENTHS = 10;
/** …and the same precision as a digit count, which is what the cell shows. */
const COMPOSITE_DECIMALS = 1;

/** The UI's complexity labels — `HandlerBenchmarkTable.tsx`, restated. */
const COMPLEXITY_LABEL: Record<string, string> = {
  low: "Low",
  med: "Medium",
  high: "High",
};

/** The UI's status labels, likewise. */
const CYCLE_STATUS_LABEL: Record<string, string> = {
  on_track: "On Track",
  watch: "Watch",
  attention: "Attention",
};

/**
 * Half away from zero, matching the server's `ROUND_HALF_UP` on a `Decimal`.
 *
 * `Math.round` alone rounds -8.5 to -8 (half toward +∞), which would disagree
 * with the server at exactly one point on the negative half of the deviation
 * scale — reachable, and the kind of failure that reads as a defect in the code
 * rather than in the oracle.
 *
 * A second rounding helper beside Story 3.1's `roundHalfUp(value, divisor)`,
 * which takes an integer numerator and an integer divisor and is the *benefit*
 * engine's convention. This one rounds an already-divided signed ratio, which
 * that one cannot express — and merging them would mean one of the two call
 * sites converting its arguments to suit the other's shape.
 */
function roundHalfAwayFromZero(value: number): number {
  return value < 0 ? -Math.round(-value) : Math.round(value);
}

/**
 * One segment average as an exact fraction — a sum over a count, undivided.
 *
 * `null` for an empty segment, never zero.
 *
 * **Why a fraction and not `mean(values, decimals)`.** The composite is the sum
 * of the three segment averages *unrounded*, and it is what the table is ranked
 * on. `mean` above rounds to the precision a tile is displayed at (whole days,
 * for settle), and summing three of those admits half a day of error — larger
 * than the gap between four of the six seeded handlers, and enough to swap two
 * of them. That is the defect this story was re-derived to fix, so the oracle
 * has to be able to see it: keeping the sum and the count lets the three
 * averages be added exactly and rounded exactly once, in `compositeOf`.
 */
interface Segment {
  sum: number;
  count: number;
}

function segmentAverages(claims: SeedClaim[]): (Segment | null)[] {
  const settled = claims.filter((claim) => claim.stage === "settled");
  const picks = claims
    .map((c) => c.sla_pick_days)
    .filter((d): d is number => d !== null);
  const approves = claims
    .map((c) => c.sla_approve_days)
    .filter((d): d is number => d !== null);
  const settles = settled
    .map((c) => c.settlement_days)
    .filter((d): d is number => d !== null);

  return [picks, approves, settles].map((values) =>
    values.length === 0
      ? null
      : {
          sum: values.reduce((total, value) => total + value, 0),
          count: values.length,
        },
  );
}

/**
 * The composite as an exact fraction, substituting the scoped portfolio's
 * average for any segment the handler has none of.
 *
 * The generalised rule: `renderSV` substitutes only the settle average and lets
 * a missing pick or approve fall through as `0`. `null` when a segment is
 * missing from both, which never happens on the seeded book.
 *
 * The three fractions are added over a common denominator rather than as
 * floats. The denominators are claim counts (at most 100 here), so the products
 * stay whole integers well inside `Number.MAX_SAFE_INTEGER` and the total is
 * exact — which is what lets the published figure be rounded once, from the
 * true value, exactly as the server does it.
 */
function compositeOf(
  segments: (Segment | null)[],
  fallback: (Segment | null)[],
): Segment | null {
  let sum = 0;
  let count = 1;
  for (let i = 0; i < segments.length; i += 1) {
    const segment = segments[i] ?? fallback[i];
    if (segment === null) return null;
    sum = sum * segment.count + segment.sum * count;
    count *= segment.count;
  }
  return { sum, count };
}

/** A fraction as the number it is, for comparisons and percentages. */
function value(fraction: Segment): number {
  return fraction.sum / fraction.count;
}

/**
 * The composite as the Avg Days column *reads* — one decimal, trailing zero and
 * all.
 *
 * Rounded from the exact fraction, once, after the order was decided on the
 * fraction itself, and then formatted to a fixed decimal rather than stringified
 * as a number. That last step is the whole point: `String(78)` is `"78"`, so a
 * composite that lands on a whole day used to render beside a neighbour's
 * `"72.7"` with no way for a reader to tell whether the gap was six tenths or
 * six days — reintroducing exactly the ambiguity the composite is computed at
 * full precision to remove. The component formats to `COMPOSITE_DECIMALS`
 * places for the same reason, so the oracle states the same string.
 */
function publishedComposite(fraction: Segment): string {
  return (
    roundHalfAwayFromZero((fraction.sum * COMPOSITE_TENTHS) / fraction.count) /
    COMPOSITE_TENTHS
  ).toFixed(COMPOSITE_DECIMALS);
}

function complexityOf(claims: SeedClaim[]): { score: number; band: string } {
  const weighted =
    SEVERITY_WEIGHT_BP * claims.reduce((sum, c) => sum + c.severity_score, 0) +
    SURGERY_RATE_WEIGHT_BP *
      claims.filter((c) => c.surgery_required).length *
      PERCENT +
    LITIGATION_RATE_WEIGHT_BP *
      claims.filter((c) => c.litigation_flag).length *
      PERCENT;
  const score = Math.min(
    COMPLEXITY_SCORE_MAX,
    roundHalfAwayFromZero(
      weighted / (claims.length * BASIS_POINTS_PER_UNIT_BLEND),
    ),
  );
  const band =
    score >= COMPLEXITY_HIGH_MIN
      ? "high"
      : score >= COMPLEXITY_MED_MIN
        ? "med"
        : "low";
  return { score, band };
}

/**
 * The handler's position in `app_user.id` order, restated from the seed file.
 *
 * The server's third sort key is `handler_id`, and the ids are recoverable here
 * without a query: migration 0004 inserts `app_users` in the seed file's order,
 * so a handler's index in that list is its id order. Restated rather than
 * fetched, like every other rule in this file.
 */
function handlerOrdinal(name: string): number {
  const index = seed.app_users.findIndex(
    (u) => u.name === name && u.role === "handler",
  );
  if (index < 0) throw new Error(`no seeded handler ${name}`);
  return index;
}

/**
 * Two names compared the way the server compares them — by code point.
 *
 * Not `localeCompare`: that applies ICU collation, which folds case and
 * diacritics, while the server sorts Python `str`, which walks code points. The
 * seeded names never reach a pair the two disagree about, and an oracle whose
 * tie-break is "whatever this Node build's locale data says" would be one
 * accented handler name away from disagreeing with the server for reasons
 * nobody could reproduce.
 */
function byCodePoint(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function cycleStatusOf(deviationPct: number): string {
  if (deviationPct <= ON_TRACK_DEVIATION_PCT_MAX) return "on_track";
  if (deviationPct >= ATTENTION_DEVIATION_PCT_MIN) return "attention";
  return "watch";
}

/** The signed deviation as the Status chip prints it — `Intl`'s `exceptZero`. */
function signedPct(deviationPct: number): string {
  return new Intl.NumberFormat("en-US", { signDisplay: "exceptZero" }).format(
    deviationPct,
  );
}

export interface ExpectedHandlerRow {
  /** The row's `data-handler` attribute. */
  handler: string;
  /**
   * The nine cells' text, in the columns' order.
   *
   * The Cycle Speed cell is **not** empty: the bar is `aria-hidden` and the
   * cell's accessible name — the composite and what the fill is a percentage of
   * — is screen-reader text inside it, so a `<td>` under a header promising a
   * figure is not an unnamed empty cell. The fill's width is asserted
   * separately, off `barWidths`.
   */
  cells: string[];
}

export interface ExpectedHandlerBenchmarks {
  rows: ExpectedHandlerRow[];
  leader: string;
  laggard: string;
  /** Each row's bar width, as the style attribute renders it. */
  barWidths: string[];
  /** The footnote's two deviation bands and the portfolio it compares against. */
  footnote: { portfolio: string; onTrack: number; attention: number };
}

/**
 * What the table's cells read for a persona's seeded book.
 *
 * **Grouped over the persona's scoped claims, never over the roster.** That is
 * the property AD-7 names the straddle case for: Kaya Johnson is assigned John
 * Deere and handles none of its seven claims, so Ken Stoker's table contains
 * Liam O'Sullivan and Fatima Al-Mansoori and not her. An oracle built from the
 * employer assignments would agree with the wrong implementation.
 *
 * Ordering is `(composite, handler, handlerId)` ascending on the **exact**
 * composite — fastest first, ties broken by name and then by id — and the rank
 * is the 1-based position in that order. All three keys, matching the server's:
 * the seed has no two handlers sharing a display name, so the third never
 * decides anything, but an oracle carrying two keys would agree with a server
 * that had quietly dropped the third. Ids come from `handlerOrdinal`.
 *
 * **The name comparison is by code point, not `localeCompare`.** The server
 * sorts Python strings, which compares code point by code point; `localeCompare`
 * applies ICU collation, which folds case and diacritics and orders `"a"` before
 * `"B"` where Python orders `"B"` first. The seeded names never reach a pair the
 * two disagree about — but the divergence would surface as an unreproducible
 * ordering the first time a handler was named with an accent, so the oracle
 * states the server's rule rather than a locale's.
 *
 * Ranking on the exact composite rather than on the published one is the whole
 * point of `compositeOf` returning a fraction: David Bline's table puts Marcus
 * Chen (72.036 days) above Fatima Al-Mansoori (72.667), and summing the SLA
 * strip's whole-day settle tiles instead makes 72.4 look slower than 72.3 and
 * swaps them.
 */
export function expectedHandlerBenchmarksFor(persona: {
  name: string;
  role: string;
}): ExpectedHandlerBenchmarks {
  const visible = claimsFor(persona.name, persona.role);
  const portfolioSegments = segmentAverages(visible);
  const portfolio = compositeOf(portfolioSegments, portfolioSegments);
  if (portfolio === null) {
    throw new Error(
      `${persona.name} has no cycle-time data — the seed has moved`,
    );
  }
  const portfolioDays = value(portfolio);

  const byHandler = new Map<string, SeedClaim[]>();
  for (const claim of visible) {
    byHandler.set(claim.handler, [
      ...(byHandler.get(claim.handler) ?? []),
      claim,
    ]);
  }

  const rows = [...byHandler.entries()]
    .map(([handler, claims]) => {
      const composite = compositeOf(segmentAverages(claims), portfolioSegments);
      if (composite === null)
        throw new Error(`${handler} has no composite — the seed has moved`);
      const days = value(composite);
      const published = publishedComposite(composite);
      const handlerId = handlerOrdinal(handler);
      const settled = claims.filter((c) => c.stage === "settled");
      const recovered = settled.map((c) =>
        c.return_status === FULLY_RECOVERED ? 100 : 0,
      );
      const { score, band } = complexityOf(claims);
      const deviation = roundHalfAwayFromZero(
        ((days - portfolioDays) / portfolioDays) * PERCENT,
      );

      return {
        handler,
        handlerId,
        days,
        published,
        cells: [
          "", // the rank, filled in once the order is known
          handler,
          String(claims.length),
          "", // the cycle-speed cell, filled in once the slowest peer is known
          `${published}d`,
          settled.length === 0 ? "—" : `${String(mean(recovered, 0))}%`,
          `${COMPLEXITY_LABEL[band]} (${String(score)})`,
          String(
            claims.filter((c) => PENDING_APPROVAL_STATUSES.includes(c.status))
              .length,
          ),
          `${CYCLE_STATUS_LABEL[cycleStatusOf(deviation)]} ${signedPct(deviation)}%`,
        ],
      };
    })
    .sort(
      (a, b) =>
        a.days - b.days ||
        byCodePoint(a.handler, b.handler) ||
        a.handlerId - b.handlerId,
    );

  const slowest = Math.max(...rows.map((row) => row.days));
  const bars = rows.map((row) =>
    roundHalfAwayFromZero((row.days / slowest) * PERCENT),
  );

  return {
    rows: rows.map((row, index) => ({
      handler: row.handler,
      cells: [
        String(index + 1),
        ...row.cells.slice(1, 3),
        // The cell's accessible name, as `CycleSpeedBar` composes it.
        `${row.published} days, ${String(bars[index])}% of the slowest cycle time in this portfolio`,
        ...row.cells.slice(4),
      ],
    })),
    leader: rows[0].handler,
    laggard: rows[rows.length - 1].handler,
    barWidths: bars.map((bar) => `${String(bar)}%`),
    footnote: {
      portfolio: `${publishedComposite(portfolio)}d`,
      onTrack: ON_TRACK_DEVIATION_PCT_MAX,
      attention: ATTENTION_DEVIATION_PCT_MIN,
    },
  };
}

/**
 * Story 5.3 — the seven analytics surfaces, restated independently.
 *
 * `expectedHandlerBenchmarksFor`'s discipline over six distributions, two
 * orderings and two limits: every rule below is written out from the story text
 * and the prototype (`renderSV`, lines 1003-1010) rather than read off the
 * response, because an oracle sharing any one of them with the implementation
 * would be validating the rest by accident.
 *
 * The oracle returns **what each surface reads** — the exact strings, in render
 * order — rather than what the endpoint answers, for `expectedPortfolioSummaryFor`'s
 * reason: a spec comparing numbers would still pass if the employer chart
 * divided cents by a hundred twice.
 *
 * Two rules deserve naming because they are the ones a re-derivation would drop:
 *
 * - **Enum-keyed series publish in their enum's declaration order, not in count
 *   order.** A legend has a fixed reading order and must not reshuffle when two
 *   categories cross. `STAGES` above is already that tuple for the stage donut;
 *   the other two are written out here.
 * - **Free-text series rank count descending, then label ascending, then cut.**
 *   Both cuts land inside a tie on the full seeded portfolio — injury ranks 7-11
 *   all count five and state ranks 9-11 all count five — so an oracle without
 *   the tie-break would disagree with a correct implementation about which five
 *   claims are on screen.
 */

/** The two chart caps. Module constants on the server, restated here. */
const INJURY_TYPE_LIMIT = 8;
const STATE_LIMIT = 10;

/** `RiskBand`'s declaration order, and the legend copy `PortfolioCharts.tsx` owns. */
const RISK_BAND_ORDER = ["high", "med", "low"] as const;
/** `ReturnStatus`'s declaration order, likewise. */
const RECOVERY_ORDER = [
  "under_treatment",
  "returned_and_under_therapy",
  "returned_and_fully_recovered",
] as const;

/**
 * The settlement donut's legend copy — the prototype's chart wording, which is
 * deliberately not the case file's stage pill ("Settled", "Treatment").
 */
const STAGE_CHART_LABEL: Record<string, string> = {
  intake: "Intake",
  investigation: "Investigation",
  treatment: "Under Treatment",
  settled: "Settled & Closed",
};

const RECOVERY_CHART_LABEL: Record<string, string> = {
  under_treatment: "Under Treatment",
  returned_and_under_therapy: "Under Therapy",
  returned_and_fully_recovered: "Fully Recovered",
};

/** The dashboard SLA tiles' testids — `chart-` prefixed, unlike the top bar's. */
export const CHART_SLA_TILES = {
  pick: "chart-sla-pick",
  approve: "chart-sla-approve",
  settle: "chart-sla-settle",
  rtwRate: "chart-sla-rtw-rate",
} as const;

function countBy<T extends string>(
  claims: SeedClaim[],
  key: (claim: SeedClaim) => T,
): Map<T, number> {
  const counts = new Map<T, number>();
  for (const claim of claims)
    counts.set(key(claim), (counts.get(key(claim)) ?? 0) + 1);
  return counts;
}

/** A donut legend row, as the component concatenates it: `"Intake:6"`. */
function legendRows(
  counts: Map<string, number>,
  order: readonly string[],
  label: Record<string, string>,
): string[] {
  return order
    .filter((key) => counts.has(key))
    .map((key) => `${label[key]}:${String(counts.get(key))}`);
}

/** A bar chart's screen-reader row, as the component writes it: `"MN: 11"`. */
function barRows(
  entries: [string, number][],
  format: (value: number) => string,
): string[] {
  return entries.map(([label, value]) => `${label}: ${format(value)}`);
}

/** Count descending, then label ascending — the server's tie-break, restated. */
function ranked(
  counts: Map<string, number>,
  limit: number,
): [string, number][] {
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || byCodePoint(a[0], b[0]))
    .slice(0, limit);
}

/** The `short_name` a seeded employer is labelled with on the employer bars. */
function employerShortName(name: string): string {
  const employer = seed.employers.find((row) => row.name === name);
  if (!employer) throw new Error(`no seeded employer ${name}`);
  return employer.short_name;
}

export interface ExpectedPortfolioCharts {
  /** The settlement donut's legend rows and its centre total. */
  stage: { legend: string[]; total: string };
  /** The severity donut's, with the High row quoting the seeded band. */
  severity: { legend: string[]; total: string };
  recoveryBars: string[];
  injuryBars: string[];
  employerBars: string[];
  stateBars: string[];
  /**
   * The truncation caption each ranked chart should show, or `null` where the
   * scope was not cut and no caption may appear.
   */
  truncation: {
    injury: string | null;
    state: string | null;
    employer: string | null;
  };
  /** `chart-sla-*` testid → the text that tile must read. */
  slaTiles: Record<string, string>;
}

export function expectedPortfolioChartsFor(persona: {
  name: string;
  role: string;
}): ExpectedPortfolioCharts {
  const visible = claimsFor(persona.name, persona.role);
  const total = String(visible.length);

  const stages = countBy(visible, (claim) => claim.stage);
  const severities = countBy(visible, (claim) =>
    riskBand(claim.severity_score),
  );
  const recoveries = countBy(visible, (claim) => claim.return_status);
  const injuries = countBy(visible, (claim) => claim.injury_type);
  const states = countBy(visible, (claim) => claim.state);

  // Paid descending, then label ascending, on the employer's *short* name —
  // the column the repository joins and the label the ties are broken by. The
  // prototype produces these labels by stripping four suffixes off the full
  // name with a chain of `String.replace`; the short name is a column and this
  // is what it is for.
  const paid = new Map<string, number>();
  for (const claim of visible) {
    const label = employerShortName(claim.employer);
    paid.set(
      label,
      (paid.get(label) ?? 0) +
        claim.paid_indemnity +
        claim.paid_medical +
        claim.paid_expense,
    );
  }
  const employers = [...paid.entries()].sort(
    (a, b) => b[1] - a[1] || byCodePoint(a[0], b[0]),
  );

  const sla = expectedSlaFor(persona.name, persona.role);

  const caption = (
    noun: string,
    shown: number,
    categories: number,
  ): string | null =>
    categories > shown
      ? `Showing ${String(shown)} of ${String(categories)} ${noun}.`
      : null;

  return {
    stage: {
      legend: legendRows(stages, STAGES, STAGE_CHART_LABEL),
      total,
    },
    severity: {
      legend: legendRows(severities, RISK_BAND_ORDER, {
        // The one legend row that quotes a rule value. Restated from
        // `HIGH_RISK_MIN` at the top of this file rather than read off the
        // page, so a caption holding its own constant and one reading the
        // response are told apart by the *component* test, and this spec
        // asserts only that the number on screen is the seeded rule's.
        high: `High (≥ ${String(HIGH_RISK_MIN)})`,
        med: "Medium",
        low: "Low",
      }),
      total,
    },
    recoveryBars: barRows(
      RECOVERY_ORDER.filter((key) => recoveries.has(key)).map((key) => [
        RECOVERY_CHART_LABEL[key],
        recoveries.get(key) ?? 0,
      ]),
      String,
    ),
    injuryBars: barRows(ranked(injuries, INJURY_TYPE_LIMIT), String),
    employerBars: barRows(employers, (cents) => DOLLARS.format(cents / 100)),
    stateBars: barRows(ranked(states, STATE_LIMIT), String),
    truncation: {
      injury: caption("injury types", INJURY_TYPE_LIMIT, injuries.size),
      state: caption("states", STATE_LIMIT, states.size),
      // The employer chart is uncapped: the employers in a book are bounded by
      // the assignment, so there is never a cut to caption.
      employer: null,
    },
    slaTiles: {
      [CHART_SLA_TILES.pick]: sla.pick.text,
      [CHART_SLA_TILES.approve]: sla.approve.text,
      [CHART_SLA_TILES.settle]: sla.settle.text,
      [CHART_SLA_TILES.rtwRate]: sla.rtwRate.text,
    },
  };
}

/**
 * Story 5.4 — the priority claims worklist, restated independently.
 *
 * The population, the ordering and the cap, built on this file's *existing*
 * private scorer rather than on a second copy of it. That is a departure from
 * `HIGH_RISK_MIN`'s "restate rather than import" discipline and a deliberate
 * one: the property this story is actually about is that the worklist and the
 * handler queue rank identically, so both oracles have to be the same oracle. A
 * third scoring rule written here would check the worklist against itself and
 * would agree with an implementation that had quietly re-weighted, so long as
 * this file re-weighted the same way. `expectedQueueFor` above uses the same
 * `priorityScore` for the same reason one story earlier.
 *
 * `FRAUD_FLAG_SCORE_MIN` is likewise reused from Story 5.1's block rather than
 * restated: the population's fraud arm and the Fraud Flags card count *one*
 * rule, and an oracle with its own number could not tell the two apart. It is
 * still deliberately not `SIU_FRAUD_SCORE_MIN`, which is the queue's referral
 * threshold over the same column pair — 13 seeded claims against 9, and the
 * single most plausible way to get this population wrong.
 *
 * The cap is written out, because it is this story's own JDM parameter and
 * nothing else in this file knows it.
 */
const SUPERVISOR_WORKLIST_CAP = 30;

/** The severity chip's copy — `RISK_LABEL`, the abbreviated gauge vocabulary. */
const SEVERITY_CHIP_LABEL: Record<string, string> = {
  high: "High",
  med: "Med",
  low: "Low",
};

/** The Status cell's stage pill copy — `STAGE_LABEL`, not the donut's wording. */
const STAGE_PILL_LABEL: Record<string, string> = {
  intake: "Intake",
  investigation: "Investigation",
  treatment: "Treatment",
  settled: "Settled",
};

/** The injured worker's display name, from the `employees` array. */
function workerName(employeeId: string): string {
  const employee = seed.employees.find((row) => row.employee_id === employeeId);
  if (!employee) throw new Error(`no seeded employee ${employeeId}`);
  return employee.name;
}

/** Active treatment ∪ fraud-flagged ∪ litigation-flagged — one `or`, not three filters. */
function qualifiesForWorklist(claim: SeedClaim): boolean {
  return (
    claim.stage === TREATMENT_STAGE ||
    (claim.fraud_flag && claim.fraud_score >= FRAUD_FLAG_SCORE_MIN) ||
    claim.litigation_flag
  );
}

export interface ExpectedPriorityRow {
  claimId: string;
  /**
   * The nine columns this oracle can predict, as rendered text, in the table's
   * order — Claim ID, Worker, Employer, Injury Type, Severity, Fraud Score,
   * Handler, Days Open, Status.
   *
   * **The tenth is deliberately absent.** "Priority Next Best Action" is the top
   * row of Story 3.5's eleven-rule generator, and restating that here would be
   * several hundred lines of TypeScript that agree with the implementation
   * exactly as often as they were copied from it. Its oracle is the generator
   * itself, asserted server-side in `test_priority_claims.py` against
   * `generate_actions(...)[0].label` for the same claim, inputs and `as_of`.
   * The spec drops the same index from the rendered row and says so.
   */
  cells: string[];
  /** Whether the Status cell should be the LITIG chip rather than a stage pill. */
  litig: boolean;
}

export interface ExpectedPriorityClaims {
  rows: ExpectedPriorityRow[];
  /** The population **before** the cap — what the caption reads "of". */
  total: number;
  /** The JDM cap — what the caption reads "top". */
  cap: number;
  /**
   * The heading's parenthetical, already decided.
   *
   * Which of the two sentences applies is a comparison of the population
   * against the cap, and the server publishes the answer as `truncated` rather
   * than letting a client work it out. The oracle does the same, so a spec
   * cannot assert "showing top 30 of 8" at a persona whose book the cap never
   * touched — which is what it did before this was computed here.
   */
  caption: string;
}

/** The index of the column this oracle does not predict — see `ExpectedPriorityRow`. */
export const NEXT_BEST_ACTION_COLUMN = 8;

/**
 * The worklist a persona's seeded book should render, as rendered strings.
 *
 * Rendered text rather than numbers, `expectedPortfolioSummaryFor`'s discipline:
 * a spec comparing numbers would still pass if the page rendered a band token
 * where a label belongs, or printed a handler's first name only (which is what
 * the prototype does in this very column).
 *
 * Ordering is `(-score, claimId)` — the tie-break included, and it matters more
 * here than on the queue: this list is ungrouped, so every tie in the whole book
 * competes in one sequence rather than within a stage, and the cursor's
 * stability depends on the key being total.
 */
export function expectedPriorityClaimsFor(persona: {
  name: string;
  role: string;
}): ExpectedPriorityClaims {
  const population = claimsFor(persona.name, persona.role).filter(
    qualifiesForWorklist,
  );
  const ordered = [...population].sort(
    (a, b) =>
      priorityScore(b) - priorityScore(a) ||
      a.claim_id.localeCompare(b.claim_id),
  );

  return {
    rows: ordered.slice(0, SUPERVISOR_WORKLIST_CAP).map((claim) => ({
      claimId: claim.claim_id,
      cells: [
        claim.claim_id,
        workerName(claim.employee_id),
        employerShortName(claim.employer),
        claim.injury_type,
        SEVERITY_CHIP_LABEL[riskBand(claim.severity_score)],
        String(claim.fraud_score),
        claim.handler,
        String(daysOpen(claim)),
        claim.litigation_flag ? "LITIG" : STAGE_PILL_LABEL[claim.stage],
      ],
      litig: claim.litigation_flag,
    })),
    total: population.length,
    cap: SUPERVISOR_WORKLIST_CAP,
    // The caption is two sentences and the population decides which. A book
    // under the cap was not cut, so quoting the cap at it would promise thirty
    // rows above a table holding eight — computed here rather than restated in
    // the spec so a persona whose book crosses the cap moves both together.
    caption:
      population.length > SUPERVISOR_WORKLIST_CAP
        ? `(showing top ${String(SUPERVISOR_WORKLIST_CAP)} of ${String(population.length)})`
        : `(${String(population.length)} claims)`,
  };
}

// --- Story 5.5: the dashboard drill-through, restated independently -------
//
// Twelve facets, an ordering and a page size — every one of them built on a
// block already in this file rather than restated a second time, which is
// `expectedPriorityClaimsFor`'s departure from `HIGH_RISK_MIN`'s discipline and
// is the *whole story* here rather than an exception to it.
//
// What a drill-through promises is that the list a number opens holds the
// claims that number counted. An oracle with its own twelfth banding rule could
// not check that: it would agree with a drill-through that had banded
// differently from the card, so long as this file banded the same way. So the
// severity facet reuses `riskBand` (the High Risk card's own restatement), the
// fraud facet reuses `FRAUD_FLAG_SCORE_MIN` (the review rule, deliberately not
// `SIU_FRAUD_SCORE_MIN`), the priority facet reuses `qualifiesForWorklist`, and
// the ordering reuses `priorityScore` and its `(-score, claimId)` tie-break.

/**
 * The drill-through's page size — `priority_weights.pageLimit`, restated.
 *
 * Consumed rather than assumed: `expectedDrillClaimsFor` cuts its sequence into
 * pages with it and publishes the page list, so a spec walks the number of
 * pages the *rule* implies rather than the one page it hoped for. Story 5.4's
 * review recorded the opposite mistake — an oracle that assumed a single page
 * and asserted a caption the server never sends.
 *
 * Deliberately a different number from `SUPERVISOR_WORKLIST_CAP` above and from
 * that document's own page limit: they are three knobs in two documents, and a
 * shared constant here would hide a wiring mistake between them.
 */
const DRILL_PAGE_LIMIT = 50;

/** The facets a spec can ask this oracle for, spelled as the URL spells them. */
export type DrillFacet =
  | "stage"
  | "severityBand"
  | "fraudFlagged"
  | "litigation"
  | "surgery"
  | "oshaRecordable"
  | "recoveryStatus"
  | "injuryType"
  | "state"
  | "employerId"
  | "handlerId"
  | "priority"
  // Story 7.1's two. Read the three fraud facets together before adding to
  // them: `fraudFlagged` is the dashboard's *review* population, `siuReview` is
  // the queue's narrower *referral* one, and `fraudBand` bands `fraud_score`
  // with **no** flag conjunct at all. Three rules over one column pair, and an
  // oracle that shared a restatement between any two would agree with an
  // implementation that had collapsed the same pair.
  | "fraudBand"
  | "siuReview"
  // Story 7.2's six. Four are a new *kind* of facet — an inclusive bound on one
  // of the two dates the Trends section buckets by — and they are two pairs over
  // two columns rather than one pair over "the anchor", because a point on the
  // injury-date series opened with `filter[fnolFrom]` returns a plausible list
  // of the wrong claims. The last two are the cohort split's own columns.
  | "fnolFrom"
  | "fnolTo"
  | "doiFrom"
  | "doiTo"
  | "disability"
  | "sector";

export type DrillFilters = Partial<Record<DrillFacet, string>>;

/** The `filter[handlerId]` value for a seeded handler — the `app_user` id. */
export function handlerIdOf(handlerName: string): number {
  const index = seed.app_users.findIndex(
    (user) => user.name === handlerName && user.role === "handler",
  );
  if (index < 0) throw new Error(`no seeded handler ${handlerName}`);
  // Migration 0004 inserts `app_users` in the seed file's order against an
  // identity column, so the id is the 1-based index. Restated rather than
  // queried, because an oracle that read the id back from the API would agree
  // with an implementation that had published the wrong one.
  return index + 1;
}

/** The `filter[employerId]` value for a seeded employer, by its full name. */
export function employerIdOf(employerName: string): number {
  const index = seed.employers.findIndex((row) => row.name === employerName);
  if (index < 0) throw new Error(`no seeded employer ${employerName}`);
  return index + 1;
}

/** One facet, restated against the surface it has to reconcile with. */
function matchesFacet(claim: SeedClaim, facet: DrillFacet, value: string): boolean {
  switch (facet) {
    // The stage column, never `status` — 62 seeded claims against 54.
    case "stage":
      return claim.stage === value;
    // `riskBand`, the same restatement the High Risk card's oracle uses.
    case "severityBand":
      return riskBand(claim.severity_score) === value;
    // The *review* threshold, never the SIU referral one — 13 against 9.
    case "fraudFlagged":
      return (
        String(claim.fraud_flag && claim.fraud_score >= FRAUD_FLAG_SCORE_MIN) === value
      );
    case "litigation":
      return String(claim.litigation_flag) === value;
    case "surgery":
      return String(claim.surgery_required) === value;
    case "oshaRecordable":
      return String(claim.osha_recordable) === value;
    case "recoveryStatus":
      return claim.return_status === value;
    // The exact stored string, with no trim, case-fold or merge.
    case "injuryType":
      return claim.injury_type === value;
    case "state":
      return claim.state === value;
    case "employerId":
      return String(employerIdOf(claim.employer)) === value;
    case "handlerId":
      return String(handlerIdOf(claim.handler)) === value;
    // The worklist's population, before its cap.
    case "priority":
      return String(qualifiesForWorklist(claim)) === value;
    // The band of the score **alone** — no `fraud_flag` conjunct, which is what
    // makes it a third rule rather than a re-spelling of either fraud facet
    // above. A claim nobody triaged still has a band.
    case "fraudBand":
      return fraudBand(claim.fraud_score) === value;
    // The queue's SIU *referral* rule — 9 seeded claims against the review
    // rule's 13, and restated rather than shared with `fraudFlagged` for that
    // facet's recorded reason.
    case "siuReview":
      return (
        String(claim.fraud_flag && claim.fraud_score >= SIU_FRAUD_SCORE_MIN) === value
      );
    // Story 7.2's four date bounds, **inclusive at both ends**, compared as the
    // stored strings rather than as parsed dates: every date in this dataset is
    // a zero-padded ISO day, and on that domain a lexicographic comparison and a
    // calendar one agree exactly — so the oracle spends no `Date` on a question
    // that is not about time zones. Inclusive because a bucket's last day is a
    // day claims fall on: an exclusive upper bound would silently drop the 31st
    // of every month the analyst clicked.
    case "fnolFrom":
      return claim.froi_date >= value;
    case "fnolTo":
      return claim.froi_date <= value;
    case "doiFrom":
      return claim.doi >= value;
    case "doiTo":
      return claim.doi <= value;
    // The claim's own enum column.
    case "disability":
      return claim.disability === value;
    // The **employer's** attribute, reached through the join — not a column on
    // the claim, which is the whole reason the facet needed a lookup rather than
    // a comparison.
    case "sector":
      return sectorOf(claim) === value;
  }
}

/** The sector of the employer a seeded claim belongs to. */
function sectorOf(claim: SeedClaim): string {
  const employer = seed.employers.find((row) => row.name === claim.employer);
  if (!employer) throw new Error(`no seeded employer ${claim.employer}`);
  return employer.sector;
}

export interface ExpectedDrillClaims {
  /** Every matching claim id, in ranked order — the list is uncapped. */
  claimIds: string[];
  /** The population, as the result sentence renders it. */
  count: string;
  /** The chips the page must draw, in the server's order, as rendered text. */
  chips: string[];
  /** `claimIds` cut into pages at the published page size. */
  pages: string[][];
}

/** What a chip says the facet is — the client's `FILTER_LABEL`, restated. */
// "Stage" and not "Status": `status` is a different column with a different
// value set, and this oracle exists to disagree with the app when the app is
// wrong — so it restates the rule rather than copying the string.
const DRILL_FACET_LABEL: Record<DrillFacet, string> = {
  stage: "Stage",
  severityBand: "Severity",
  fraudFlagged: "Fraud flags",
  litigation: "Litigation",
  surgery: "Surgery required",
  oshaRecordable: "OSHA recordable",
  recoveryStatus: "Recovery",
  injuryType: "Injury type",
  state: "State",
  employerId: "Employer",
  handlerId: "Handler",
  priority: "Priority worklist",
  fraudBand: "Fraud band",
  siuReview: "SIU review",
  // Story 7.2's six. The four bounds name the **anchor** rather than the column,
  // because an analyst who clicked a point on the injury-date series has to see
  // that the list is narrowed on the injury date and not on the filing date —
  // the two are weeks apart on a third of the seeded book, and a chip reading
  // only "From" would make the two drills indistinguishable.
  fnolFrom: "FNOL from",
  fnolTo: "FNOL to",
  doiFrom: "Injury from",
  doiTo: "Injury to",
  disability: "Disability",
  sector: "Sector",
};

/** What a chip says the *value* is, for the ten facets the UI labels. */
const DRILL_VALUE_LABEL: Partial<Record<DrillFacet, Record<string, string>>> = {
  stage: {
    settled: "Settled & Closed",
    treatment: "Under Treatment",
    intake: "Intake",
    investigation: "Investigation",
  },
  severityBand: { high: "High", med: "Medium", low: "Low" },
  recoveryStatus: {
    returned_and_fully_recovered: "Fully Recovered",
    under_treatment: "Under Treatment",
    returned_and_under_therapy: "Under Therapy",
  },
  fraudFlagged: { true: "Yes", false: "No" },
  litigation: { true: "Yes", false: "No" },
  surgery: { true: "Yes", false: "No" },
  oshaRecordable: { true: "Yes", false: "No" },
  priority: { true: "Yes", false: "No" },
  // Three bands, and `medium` spelled out — deliberately not `RiskBand`'s
  // `med`. Two vocabularies over two columns, and the abbreviation is only
  // worth its ambiguity where something already spells it that way.
  fraudBand: { low: "Low", medium: "Medium", high: "High" },
  siuReview: { true: "Yes", false: "No" },
  // Story 7.2's one labelled facet. The four date bounds are absent because a
  // chip prints an ISO day as sent, and `sector` is absent because it is free
  // text where the stored value *is* the label.
  disability: { temporary: "Temporary", permanent: "Permanent" },
};

/**
 * The **order** the chips are drawn in — the server's `FILTER_KEYS`, restated.
 *
 * A chip row that rendered the right two chips in the wrong order would satisfy
 * a set comparison, and the order is what a reader scans; so the oracle carries
 * it and the spec compares a list.
 */
const DRILL_FACET_ORDER: DrillFacet[] = [
  "stage",
  "severityBand",
  "fraudFlagged",
  "litigation",
  "surgery",
  "oshaRecordable",
  "recoveryStatus",
  "injuryType",
  "state",
  "employerId",
  "handlerId",
  "priority",
  // Appended, and the position is load-bearing: the server's `FILTER_KEYS` is
  // read off `DrillFilters`' field order and the two arrived at the end of that
  // dataclass, so inserting them beside `fraudFlagged` — where they read more
  // naturally — would put this oracle out of step with the chip row on every
  // URL carrying both.
  "fraudBand",
  "siuReview",
  // Story 7.2's six, appended for the reason 7.1's two were and in the server's
  // `DrillFilters` field order. The consequence is visible on every trend drill:
  // a cohort chip ("Severity: High") is drawn **before** the two date chips,
  // although the analyst chose the period first.
  "fnolFrom",
  "fnolTo",
  "doiFrom",
  "doiTo",
  "disability",
  "sector",
];

/**
 * What a persona's drill-through must render under one filter set.
 *
 * Rendered strings rather than numbers, `expectedPortfolioSummaryFor`'s
 * discipline: a spec comparing numbers would still pass if the page printed an
 * employer id where a name belongs, which is exactly the failure the two
 * id-valued chips exist to prevent.
 *
 * The two id chips are labelled from the *seed's* display strings — the
 * employer's `short_name` and the handler's name — because that is what the
 * server resolves them to, off rows it had already read. The other ten are
 * labelled from the maps above, which are the client's copy restated.
 */
export function expectedDrillClaimsFor(
  persona: { name: string; role: string },
  filters: DrillFilters = {},
): ExpectedDrillClaims {
  const visible = claimsFor(persona.name, persona.role).filter((claim) =>
    DRILL_FACET_ORDER.every((facet) => {
      const value = filters[facet];
      return value === undefined || matchesFacet(claim, facet, value);
    }),
  );
  const ordered = [...visible].sort(
    (a, b) =>
      priorityScore(b) - priorityScore(a) || a.claim_id.localeCompare(b.claim_id),
  );
  const claimIds = ordered.map((claim) => claim.claim_id);

  const pages: string[][] = [];
  for (let start = 0; start < Math.max(claimIds.length, 1); start += DRILL_PAGE_LIMIT) {
    pages.push(claimIds.slice(start, start + DRILL_PAGE_LIMIT));
  }

  const chips = DRILL_FACET_ORDER.flatMap((facet) => {
    const value = filters[facet];
    if (value === undefined) return [];
    if (facet === "employerId") {
      const employer = seed.employers.find(
        (row) => String(employerIdOf(row.name)) === value,
      );
      // `#id` when the id names nothing in this book — the app cannot publish a
      // name it must not confirm the existence of, and a bare integer names
      // nothing at all.
      return [`${DRILL_FACET_LABEL[facet]}: ${employer?.short_name ?? `#${value}`}`];
    }
    if (facet === "handlerId") {
      const handler = seed.app_users.find(
        (user) =>
          user.role === "handler" && String(handlerIdOf(user.name)) === value,
      );
      return [`${DRILL_FACET_LABEL[facet]}: ${handler?.name ?? `#${value}`}`];
    }
    return [
      `${DRILL_FACET_LABEL[facet]}: ${DRILL_VALUE_LABEL[facet]?.[value] ?? value}`,
    ];
  });

  return {
    claimIds,
    count: `${String(claimIds.length)} in view`,
    chips,
    pages,
  };
}

/** The drill-through URL for one filter set, as the page builds it. */
export function drillUrl(filters: DrillFilters = {}): string {
  const params = new URLSearchParams();
  for (const facet of DRILL_FACET_ORDER) {
    const value = filters[facet];
    if (value !== undefined) params.set(`filter[${facet}]`, value);
  }
  const query = params.toString();
  return query === "" ? "/dashboard/claims" : `/dashboard/claims?${query}`;
}

// --- Story 7.1: the fraud workspace ---------------------------------------
//
// Three rules over one column pair, and this block is where the seed stops
// being able to tell them apart:
//
//   siuReview    = fraud_flag && fraud_score >= SIU_FRAUD_SCORE_MIN   (60)
//   fraudFlagged = fraud_flag && fraud_score >= FRAUD_FLAG_SCORE_MIN  (55)
//   fraudBand    = band(fraud_score) against MED (35) and HIGH (55)
//
// On the seeded portfolio `fraud_flag` and `fraud_score >= 55` coincide exactly,
// so `fraudFlagged` and the `high` band name the same thirteen claims. An oracle
// that shared one number between them would still agree with an implementation
// that had collapsed the two — which is why the two constants below are written
// out separately although they hold the same integer, and why the server suite
// carries synthetic projections where the three disagree. This file cannot: it
// reads the seed, and the seed is the thing that cannot tell them apart.
//
// `FRAUD_BAND_MED_MIN` is likewise not `MED_RISK_MIN` above, although both are
// 35: one bands a severity score and the other a fraud score, and the whole
// reason they are two rule parameters is that they must be able to move apart.

const FRAUD_BAND_HIGH_MIN = 55;
const FRAUD_BAND_MED_MIN = 35;

/** The `FraudBand` declaration order — low to high, a distribution's reading. */
const FRAUD_BAND_ORDER = ["low", "medium", "high"] as const;

/** The injury-type cut the rate breakdown applies — the chart's, restated. */
const FRAUD_INJURY_TYPE_LIMIT = 8;

/** `fraud_score` banded — the score alone, with no `fraud_flag` conjunct. */
function fraudBand(fraudScore: number): string {
  if (fraudScore >= FRAUD_BAND_HIGH_MIN) return "high";
  if (fraudScore >= FRAUD_BAND_MED_MIN) return "medium";
  return "low";
}

/** The dashboard's fraud *review* rule, restated — never the referral one. */
function fraudFlagged(claim: SeedClaim): boolean {
  return claim.fraud_flag && claim.fraud_score >= FRAUD_FLAG_SCORE_MIN;
}

/** The queue's SIU *referral* rule, restated — never the review one. */
function siuReferred(claim: SeedClaim): boolean {
  return claim.fraud_flag && claim.fraud_score >= SIU_FRAUD_SCORE_MIN;
}

export interface ExpectedFraudAnalytics {
  /** The band legend's rows, as rendered text, in the rule's order. */
  bandRows: string[];
  /** The SIU pipeline's stage rows, as rendered text, in `STAGES`' order. */
  siuStageRows: string[];
  /** The SIU pipeline's handler rows, as rendered text, in the server's order. */
  siuHandlerRows: string[];
  /** The two population cards' figures, as rendered text. */
  flagged: string;
  siu: string;
  /** The first row of each rate table under the default order, as rendered. */
  topInjuryRate: string;
  topEmployerRate: string;
  topHandlerRate: string;
}

/**
 * `65` → `"0.65%"`. `web/src/lib/rate.ts::formatBasisPoints`, restated.
 *
 * The same integer arithmetic rather than an import, for this file's standing
 * reason: the point of the assertion is that the page turned the server's basis
 * points into these characters, and an oracle that called the shipped formatter
 * would agree with it whatever it did. Always two decimals, so `2000` reads
 * `"20.00%"`.
 */
function formatRate(basisPoints: number): string {
  const whole = Math.trunc(basisPoints / 100);
  const hundredths = Math.abs(basisPoints % 100);
  return `${String(whole)}.${String(hundredths).padStart(2, "0")}%`;
}

/** `flagged / claims` in basis points, half-up — the service's arithmetic. */
function rateBp(flagged: number, claims: number): number {
  // `Math.round` is half-*up* for positive numbers in JavaScript, which is what
  // the server's `ROUND_HALF_UP` does for a non-negative rate. Stated rather than
  // assumed, because JavaScript's `Math.round(-0.5)` is `-0` and a rate is never
  // negative — so the agreement holds over this function's whole domain.
  return Math.round((flagged / claims) * 10000);
}

/**
 * What a persona's Fraud section must render.
 *
 * **Rendered strings rather than numbers**, `expectedPortfolioSummaryFor`'s
 * discipline: a spec comparing numbers would still pass if the page printed a
 * handler id where a name belongs, or a rate the browser divided itself.
 *
 * The band rows are the **legend's** text, including the edges the caption
 * quotes — so a page holding its own 55 fails here rather than in a comment.
 * They are all three, always, because the server zero-fills a rule's
 * vocabulary; the pipeline rows are only the stages and handlers the scope
 * actually reaches, because a stage is a column. The two rules sit in one oracle
 * so the difference is asserted rather than described.
 */
export function expectedFraudAnalyticsFor(persona: {
  name: string;
  role: string;
}): ExpectedFraudAnalytics {
  const visible = claimsFor(persona.name, persona.role);

  const bands = new Map<string, number>();
  for (const band of FRAUD_BAND_ORDER) bands.set(band, 0);
  for (const claim of visible) {
    const band = fraudBand(claim.fraud_score);
    bands.set(band, (bands.get(band) ?? 0) + 1);
  }
  const BAND_LABEL: Record<string, string> = {
    low: "Low",
    medium: `Medium (≥ ${String(FRAUD_BAND_MED_MIN)})`,
    high: `High (≥ ${String(FRAUD_BAND_HIGH_MIN)})`,
  };

  const referred = visible.filter(siuReferred);

  const stageCounts = new Map<string, number>();
  for (const claim of referred) {
    stageCounts.set(claim.stage, (stageCounts.get(claim.stage) ?? 0) + 1);
  }
  const STAGE_LABEL: Record<string, string> = {
    settled: "Settled & Closed",
    treatment: "Under Treatment",
    intake: "Intake",
    investigation: "Investigation",
  };

  const handlerCounts = new Map<string, number>();
  for (const claim of referred) {
    handlerCounts.set(claim.handler, (handlerCounts.get(claim.handler) ?? 0) + 1);
  }
  const handlerRows = [...handlerCounts.entries()].sort(
    (a, b) =>
      b[1] - a[1] ||
      a[0].localeCompare(b[0]) ||
      handlerIdOf(a[0]) - handlerIdOf(b[0]),
  );

  /**
   * One rate table's top row under the default order — worst rate first.
   *
   * `limit` is the **population** cut, applied before the order and never after
   * it: which rows a capped breakdown carries is decided by claim count
   * descending then label ascending — the same ranking the portfolio's
   * injury-type chart cuts on — so that a display preference cannot change which
   * rows exist. Restating it here matters for exactly the seeded case this oracle
   * is read for: the highest *rate* on the book is a one-claim bucket, which the
   * population ranking puts nowhere near the top eight, so an oracle that cut
   * after ordering would name a row the table does not have.
   */
  function topRate(
    keyOf: (claim: SeedClaim) => string,
    labelOf: (key: string) => string,
    limit?: number,
  ): string {
    const tallies = new Map<string, { flagged: number; claims: number }>();
    for (const claim of visible) {
      const key = keyOf(claim);
      const tally = tallies.get(key) ?? { flagged: 0, claims: 0 };
      if (fraudFlagged(claim)) tally.flagged += 1;
      tally.claims += 1;
      tallies.set(key, tally);
    }
    const kept =
      limit === undefined
        ? [...tallies.entries()]
        : [...tallies.entries()]
            .sort(
              (a, b) =>
                b[1].claims - a[1].claims ||
                labelOf(a[0]).localeCompare(labelOf(b[0])) ||
                a[0].localeCompare(b[0]),
            )
            .slice(0, limit);
    const ordered = kept.sort((a, b) => {
      const rateA = rateBp(a[1].flagged, a[1].claims);
      const rateB = rateBp(b[1].flagged, b[1].claims);
      // Rate descending, then the *label* ascending, then the key — the server's
      // total order, restated. Without the tie-break this oracle would disagree
      // with a correct implementation whenever two rows tie, which on a rate of
      // zero is most of the table.
      return (
        rateB - rateA ||
        labelOf(a[0]).localeCompare(labelOf(b[0])) ||
        a[0].localeCompare(b[0])
      );
    });
    const [key, tally] = ordered[0];
    return `${labelOf(key)} ${formatRate(rateBp(tally.flagged, tally.claims))} ${String(
      tally.flagged,
    )} ${String(tally.claims)}`;
  }

  const shortNames = new Map(seed.employers.map((row) => [row.name, row.short_name]));

  return {
    bandRows: FRAUD_BAND_ORDER.map(
      (band) => `${BAND_LABEL[band]}:${String(bands.get(band) ?? 0)}`,
    ),
    // `STAGES`' declaration order, and only the stages the referred claims are
    // actually in — the omission rule, which is the opposite of the band rows
    // one field up and is the difference this oracle exists to assert.
    siuStageRows: STAGES.filter((stage) => stageCounts.has(stage)).map(
      (stage) => `${STAGE_LABEL[stage]}: ${String(stageCounts.get(stage) ?? 0)}`,
    ),
    siuHandlerRows: handlerRows.map(([name, count]) => `${name}: ${String(count)}`),
    flagged: String(visible.filter(fraudFlagged).length),
    siu: String(referred.length),
    // The only capped breakdown, and therefore the only one whose top row is a
    // row of the *cut* set rather than of the whole dimension.
    topInjuryRate: topRate(
      (claim) => claim.injury_type,
      (key) => key,
      FRAUD_INJURY_TYPE_LIMIT,
    ),
    topEmployerRate: topRate(
      (claim) => claim.employer,
      (key) => shortNames.get(key) ?? key,
    ),
    topHandlerRate: topRate(
      (claim) => claim.handler,
      (key) => key,
    ),
  };
}

/**
 * How many injury types the rate breakdown shows, and how many it cut from.
 *
 * Published so the spec can assert the truncation caption from the *seed*
 * rather than from a number typed into a test — the same reason the drill
 * oracle publishes its page cut.
 */
export function expectedFraudInjuryCut(persona: { name: string; role: string }): {
  shown: number;
  total: number;
} {
  const types = new Set(
    claimsFor(persona.name, persona.role).map((claim) => claim.injury_type),
  );
  return { shown: Math.min(FRAUD_INJURY_TYPE_LIMIT, types.size), total: types.size };
}

// --- Story 7.2: the trend window and its cohorts, restated independently ---
//
// **This block shares no helper with anything above it that decides a
// population, and it shares nothing at all with the server.** Story 7.1's
// review found `topRate` carrying the same sort-then-cut defect as the
// implementation and therefore agreeing with it: an oracle that mirrors the
// code it checks is not a second opinion. A trend story has that shape of
// hazard twice over — **a window is a cut and a cohort split is a partition** —
// so the date arithmetic below is written out from the story's definitions
// (which bucket, which claim, which value) rather than derived from the shape
// of `services/worklist/trends.py`'s pipeline, and the specs assert *which
// buckets and which cohort values exist* as a set alongside their order and
// their values, because an ordering assertion is satisfiable by the wrong
// population.
//
// Three definitions, restated from the story rather than read off a response:
//
//   window   = the last `TREND_DEFAULT_BUCKETS` buckets of the grain, ending in
//              the bucket that contains `asOf`. It is a cut on the *timeline*,
//              so it is computed from the calendar and never from the claims —
//              a window derived from the data would move when the data did and
//              could never disagree with an implementation that had lost a
//              bucket.
//   bucket   = the claims whose **anchor date** falls inside the bucket's
//              inclusive day bounds. `fnol` is `froi_date` and `doi` is `doi`;
//              nothing else anchors, and `days_open` still counts from
//              `froi_date` on both anchors because that is what the age of a
//              claim is.
//   cohort   = a partition of that same population on one dimension, so the
//              cohort series over one metric sum back to the unsplit one. The
//              slot a cohort takes in the palette is its rank among the
//              **wire keys sorted by code unit** — a fact about the vocabulary,
//              not about the data, which is what makes a colour identity rather
//              than rank. The vocabulary is the cohort values present **in the
//              window**, not in the persona's whole book: a value nobody filed
//              a claim under during those twelve periods is a line nobody drew,
//              so it takes no slot and no legend entry. Deriving it from the
//              book instead is green only while today's date happens to leave
//              every value inside the window — a spec that is right in August
//              and wrong in September is not an oracle.
//   evidence = how many claims fed a *metric* on a bucket, which is the
//              bucket's population for three of the five and is not for the
//              other two: a settlement mean is over settled claims carrying a
//              duration and an RTW rate is over settled claims. The
//              low-confidence mark is decided on that count, so one bucket can
//              carry a thin settlement mean under a volume point that is not
//              thin at all.
//
// What is deliberately reused from further up this file is arithmetic and the
// SLA rule, never a cut: `mean` is division, `riskBand` is the one registered
// `risk` derivation this console bands severity with everywhere (a second
// restatement of it here would be pretending it is a fourth rule), and the two
// settled-claim definitions are the ones `expectedSlaFor` already spells,
// because AD-2 says the settlement mean and the RTW rate on a trend bucket are
// that same single aggregation folded per bucket. Spelling them again would
// assert a difference the story says must not exist.

/**
 * How many buckets a default window holds — `trend_periods.defaultBuckets`.
 *
 * Restated rather than read off the payload for this file's standing reason: a
 * spec that took the window length from the response it is checking would
 * accept any window the server chose to send.
 */
const TREND_DEFAULT_BUCKETS = 12;

/**
 * The claim count at or below which a bucket is marked thin —
 * `trend_periods.lowConfidenceClaimMax`. The test is `0 < n <= max`: a bucket
 * with **no** claims is not low confidence, it is no confidence, and its means
 * are already absent.
 */
const TREND_LOW_CONFIDENCE_CLAIM_MAX = 3;

/**
 * The two targets the two graded cards quote, on the scales *those cards* use.
 *
 * Spelled separately from `SLA_TARGETS` at the top of this file although the
 * settle figure coincides and the RTW figure is the same policy: the strip
 * renders a whole-percent tile and the trend card renders a **basis-point**
 * reference line, and the whole reason two numbers exist in one deployment's
 * config is that a unit conversion is where a target silently becomes 80
 * hundredths of a percent. An oracle that shared one constant between the two
 * scales could not see that happen.
 */
const TREND_SETTLE_TARGET_DAYS = 30;
const TREND_RTW_TARGET_BP = 8000;

/** The three grains, the two anchors and the four cohort dimensions. */
export type TrendGrain = "week" | "month" | "quarter";
export type TrendAnchor = "fnol" | "doi";
export type TrendCohortDimension = "none" | "severity_band" | "disability" | "sector";

/** What a caller asks this oracle for; the defaults are the route's defaults. */
export interface TrendQuery {
  grain?: TrendGrain;
  anchor?: TrendAnchor;
  cohort?: TrendCohortDimension;
}

/** The month abbreviations a bucket label uses, in calendar order. */
const TREND_MONTH_ABBR = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

const MS_PER_DAY = 86_400_000;

/** `2026-08-21` → the UTC midnight of that day. */
function utcDay(iso: string): number {
  const [year, month, day] = iso.split("-").map(Number);
  return Date.UTC(year, month - 1, day);
}

/** A UTC millisecond stamp → the `YYYY-MM-DD` the wire and the chips use. */
function isoDay(stamp: number): string {
  return new Date(stamp).toISOString().slice(0, 10);
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

/**
 * The day every age and every window edge in one request is measured against —
 * `rules.engine.utc_today()`, restated.
 *
 * **UTC, not the runner's local midnight**, and that is the one thing worth
 * saying about it: the stack and this file must agree about which day it is or
 * every bucket in the window is off by one, and the only clock both can name is
 * the UTC one. A run started in the last seconds before UTC midnight would
 * legitimately disagree with a response computed in the first seconds after it;
 * that is a property of a time-anchored surface rather than of this oracle, and
 * pinning a date here instead would make the whole suite stale tomorrow.
 */
function trendAsOf(): string {
  return new Date().toISOString().slice(0, 10);
}

interface TrendBucket {
  key: string;
  label: string;
  from: string;
  to: string;
}

/**
 * The window: `TREND_DEFAULT_BUCKETS` buckets of `grain`, ending in `asOf`'s.
 *
 * Written three times over rather than as one parameterised walk, because the
 * three grains are three different calendars and the shared-shape version is
 * exactly the helper the review said an oracle must not have: a month is a
 * variable number of days, a quarter is three months aligned to January, and a
 * week is an **ISO** week that can belong to a different year than the days in
 * it. Each is derived from the calendar and none of them from the claims.
 */
function trendWindow(grain: TrendGrain, asOf: string): TrendBucket[] {
  const [year, month, day] = asOf.split("-").map(Number);

  if (grain === "month") {
    const buckets: TrendBucket[] = [];
    for (let back = TREND_DEFAULT_BUCKETS - 1; back >= 0; back -= 1) {
      // `Date.UTC` normalises an out-of-range month, so counting backwards
      // across a year boundary needs no year arithmetic of its own.
      const first = Date.UTC(year, month - 1 - back, 1);
      const firstDate = new Date(first);
      const y = firstDate.getUTCFullYear();
      const m = firstDate.getUTCMonth();
      // Day zero of the next month is the last day of this one — the only
      // month-length rule that is right in February of a leap year.
      const last = Date.UTC(y, m + 1, 0);
      buckets.push({
        key: `${String(y)}-${pad2(m + 1)}`,
        label: `${TREND_MONTH_ABBR[m]} ${String(y)}`,
        from: isoDay(first),
        to: isoDay(last),
      });
    }
    return buckets;
  }

  if (grain === "quarter") {
    const buckets: TrendBucket[] = [];
    const quarterOfAsOf = Math.floor((month - 1) / 3);
    for (let back = TREND_DEFAULT_BUCKETS - 1; back >= 0; back -= 1) {
      const first = Date.UTC(year, (quarterOfAsOf - back) * 3, 1);
      const firstDate = new Date(first);
      const y = firstDate.getUTCFullYear();
      const q = Math.floor(firstDate.getUTCMonth() / 3);
      const last = Date.UTC(y, (q + 1) * 3, 0);
      buckets.push({
        key: `${String(y)}-Q${String(q + 1)}`,
        label: `Q${String(q + 1)} ${String(y)}`,
        from: isoDay(first),
        to: isoDay(last),
      });
    }
    return buckets;
  }

  // ISO weeks: Monday-start, and the key's year is the week's year rather than
  // the start day's — `2026-W01` begins on 2025-12-29.
  const asOfStamp = Date.UTC(year, month - 1, day);
  // `getUTCDay()` is 0 on Sunday; `(d + 6) % 7` makes Monday 0.
  const mondayOffset = (new Date(asOfStamp).getUTCDay() + 6) % 7;
  const currentMonday = asOfStamp - mondayOffset * MS_PER_DAY;

  const buckets: TrendBucket[] = [];
  for (let back = TREND_DEFAULT_BUCKETS - 1; back >= 0; back -= 1) {
    const first = currentMonday - back * 7 * MS_PER_DAY;
    const last = first + 6 * MS_PER_DAY;
    // The ISO year is the year of the week's Thursday, which is what makes a
    // week that straddles New Year belong to exactly one of the two years.
    const thursday = new Date(first + 3 * MS_PER_DAY);
    const isoYear = thursday.getUTCFullYear();
    const jan1 = Date.UTC(isoYear, 0, 1);
    const week = Math.floor((thursday.getTime() - jan1) / (7 * MS_PER_DAY)) + 1;
    buckets.push({
      key: `${String(isoYear)}-W${pad2(week)}`,
      label: `Wk ${pad2(week)} ${String(isoYear)}`,
      from: isoDay(first),
      to: isoDay(last),
    });
  }
  return buckets;
}

/** Which stored date puts a claim in a bucket — the anchor, and only these two. */
function anchorDateOf(claim: SeedClaim, anchor: TrendAnchor): string {
  return anchor === "fnol" ? claim.froi_date : claim.doi;
}

/** Which cohort a claim belongs to, or `null` on an unsplit series. */
function trendCohortOf(claim: SeedClaim, dimension: TrendCohortDimension): string | null {
  switch (dimension) {
    case "none":
      return null;
    // The one registered `risk` derivation, which is what AC 2's "match the KPI
    // cards' risk colouring exactly" means: the same band, so the same red.
    case "severity_band":
      return riskBand(claim.severity_score);
    case "disability":
      return claim.disability;
    case "sector":
      return sectorOf(claim);
  }
}

/**
 * The cohort vocabulary in **palette-slot order**: the wire keys, sorted by
 * code unit.
 *
 * `localeCompare` deliberately not used. The slot is assigned server-side by
 * ordering the raw keys, and a locale collation can disagree with a code-unit
 * one about punctuation — `Automation/Sensing` against `Automotive` is a slash
 * away from being the case that proves it. Sorting the *keys* rather than the
 * labels is the 5.3 review's finding restated: two labels can collapse where
 * two keys cannot.
 */
function trendCohortKeys(
  claims: SeedClaim[],
  dimension: TrendCohortDimension,
): (string | null)[] {
  if (dimension === "none") return [null];
  const keys = new Set<string>();
  for (const claim of claims) {
    const key = trendCohortOf(claim, dimension);
    if (key !== null) keys.add(key);
  }
  return [...keys].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
}

/**
 * `chartTheme.ts`'s ten categorical hexes and its two neutrals, restated.
 *
 * Hexes rather than tokens because that array is literal hexes; the two
 * fallbacks are the tokens they are declared as. Restated for the standing
 * reason — a spec that imported the palette would agree with a build that had
 * repainted every line.
 */
const TREND_CATEGORICAL_FILLS = [
  "#1D6A96",
  "#E8560A",
  "#1D7A45",
  "#9A6E06",
  "#C73E2D",
  "#7B5EA7",
  "#2E8B94",
  "#8B6914",
  "#1D4F8A",
  "#6B2D8B",
];
const TREND_PALETTE_OVERFLOW_FILL = "var(--color-muted-text)";
/** The severity band's semantic palette — `RISK_FILL`, which is the gauge's. */
const TREND_RISK_FILL: Record<string, string> = {
  high: "var(--color-error)",
  med: "var(--color-warn)",
  low: "var(--color-ok)",
};
/** What a single unsplit line is drawn in — one line carries no comparison. */
const TREND_SERIES_FILL = "var(--color-brand)";

/**
 * A cohort's colour: identity, never rank — `trendColors.cohortFill` restated.
 *
 * The slot is the position in the sorted **vocabulary**, so it does not move
 * when the numbers do. That is the property the spec asserts across two charts
 * and across a cohort change, and it is why this function takes a slot rather
 * than a row index.
 */
function trendFill(
  dimension: TrendCohortDimension,
  key: string | null,
  slot: number,
): string {
  if (key === null) return TREND_SERIES_FILL;
  if (dimension === "severity_band") return TREND_RISK_FILL[key] ?? TREND_PALETTE_OVERFLOW_FILL;
  return TREND_CATEGORICAL_FILLS[slot] ?? TREND_PALETTE_OVERFLOW_FILL;
}

/** What a cohort value is called on screen — the client's copy, restated. */
const TREND_COHORT_VALUE_LABEL: Partial<Record<TrendCohortDimension, Record<string, string>>> =
  {
    // "Medium" and not the gauge's "Med": a chip and a legend are sentences.
    severity_band: { high: "High", med: "Medium", low: "Low" },
    disability: { temporary: "Temporary", permanent: "Permanent" },
    // `sector` is absent: free text where the stored value *is* the label.
  };

/** What the whole book is called when nothing is split. */
const TREND_WHOLE_BOOK_LABEL = "All claims";

/** What the cohort selector calls each dimension — the legend's `aria-label`. */
const TREND_COHORT_LABEL: Record<TrendCohortDimension, string> = {
  none: "No split",
  severity_band: "Severity band",
  disability: "Disability type",
  sector: "Employer sector",
};

/** The word a card's title ends with, per anchor. */
const TREND_ANCHOR_WORD: Record<TrendAnchor, string> = {
  fnol: "FNOL",
  doi: "injury date",
};

/**
 * Whole dollars, `lib/money.ts`'s rule restated: the formatter drops cents
 * because a claim reserve in cents is noise. The options are spelled out rather
 * than the function imported, so a build that started printing cents fails here.
 */
const TREND_DOLLARS = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

/** `8000` → `"80.00"` — `formatBasisPoints`' integer arithmetic, restated. */
function trendBasisPoints(basisPoints: number): string {
  const whole = Math.trunc(basisPoints / 100);
  const hundredths = Math.abs(basisPoints % 100);
  return `${String(whole)}.${String(hundredths).padStart(2, "0")}`;
}

/** What a point with no value reads as — the em dash, never a zero. */
const TREND_NO_VALUE = "—";

/** The five metrics, in the order the section publishes and draws them. */
type TrendMetricKey =
  | "volume"
  | "avg_days_open"
  | "avg_settlement_days"
  | "rtw_rate_bp"
  | "paid_cents";

interface TrendMetricSpec {
  metric: TrendMetricKey;
  /** The card's `data-testid` stem. */
  testId: string;
  title: (anchor: TrendAnchor) => string;
  /**
   * One bucket's value, or `null` where there was nothing to average or rate.
   *
   * **The null-versus-zero split is by metric kind and not by bucket**, which
   * is AC 3: a count and a sum over an empty period are genuinely `0`, while a
   * mean and a rate over an empty denominator are absent. Each metric answers
   * for itself here rather than a shared "is this bucket empty" branch, because
   * a shared branch is what would make all five agree about a period one of
   * them can describe and another cannot.
   */
  valueOf: (claims: SeedClaim[], asOf: string) => number | null;
  /**
   * How many of the bucket's claims fed that value — the metric's own evidence.
   *
   * Beside `valueOf` rather than inside it, and answered by every metric for
   * itself: the two figures diverge on exactly the two metrics whose
   * denominator is not the bucket, and a spec that reused `claims.length` here
   * would restate the conflation the implementation had and agree with it —
   * `topRate` again, one story on. It decides the low-confidence mark, so a
   * settlement mean over two of a busy month's claims is marked thin here and
   * would not be by an oracle counting the month.
   */
  populationOf: (claims: SeedClaim[]) => number;
  format: (value: number) => string;
  /** The dashed reference line's caption, for the two graded metrics. */
  targetCaption?: string;
  /** A sentence this metric owes its reader, appended to the footnote. */
  note?: (asOf: string) => string;
}

/** The settled population two of the five metrics are folded over (AD-2). */
function trendSettled(claims: SeedClaim[]): SeedClaim[] {
  return claims.filter((claim) => claim.stage === SETTLED_STAGE);
}

const TREND_METRICS: TrendMetricSpec[] = [
  {
    metric: "volume",
    testId: "trend-volume",
    title: (anchor) => `Claim volume by ${TREND_ANCHOR_WORD[anchor]}`,
    // A count. Zero is an answer.
    valueOf: (claims) => claims.length,
    // A count *is* its own population.
    populationOf: (claims) => claims.length,
    format: (value) => String(value),
  },
  {
    metric: "avg_days_open",
    testId: "trend-days-open",
    // The anchor, in a title whose *measure* is not the anchor's: `days_open`
    // counts from the FNOL date whichever date the buckets were cut on, so the
    // card names both — the axis here and the measure in the note below.
    title: (anchor) => `Average days open by ${TREND_ANCHOR_WORD[anchor]}`,
    // `days_open` counts **`froi_date` → `as_of`** on both anchors and floors at
    // zero, so the ten seeded claims filed in the future read as 0 rather than
    // as a negative age. A mean, so `null` on an empty bucket.
    valueOf: (claims, asOf) => {
      if (claims.length === 0) return null;
      const today = utcDay(asOf);
      return mean(
        claims.map((claim) =>
          Math.max(0, Math.round((today - utcDay(claim.froi_date)) / MS_PER_DAY)),
        ),
        0,
      );
    },
    // Over every claim in the bucket — an age needs a claim and nothing else.
    populationOf: (claims) => claims.length,
    format: (value) => `${String(value)}d`,
    note: (asOf) => `Ages counted from FNOL to ${asOf}`,
  },
  {
    metric: "avg_settlement_days",
    testId: "trend-settlement",
    // Design note 1 in the title: there is no closure date in the schema, so a
    // settlement cycle time is a property of the **filing** cohort — "claims
    // filed in March took N days" — and the card has to say so.
    title: (anchor) => `Settlement cycle time — by ${TREND_ANCHOR_WORD[anchor]} cohort`,
    // The SLA strip's settle mean: over settled claims **that carry a duration**,
    // which is a narrower denominator than the rate below it and deliberately so.
    valueOf: (claims) => {
      const durations = trendSettled(claims)
        .map((claim) => claim.settlement_days)
        .filter((days): days is number => days !== null);
      return durations.length === 0 ? null : mean(durations, 0);
    },
    // …and the count is that same narrower denominator, spelled out again
    // rather than reached through `valueOf`: a settled claim with no recorded
    // duration is in the rate's population below and not in this one.
    populationOf: (claims) =>
      trendSettled(claims).filter((claim) => claim.settlement_days !== null).length,
    format: (value) => `${String(value)}d`,
    targetCaption: `Target ${String(TREND_SETTLE_TARGET_DAYS)}d`,
  },
  {
    metric: "rtw_rate_bp",
    testId: "trend-rtw-rate",
    title: () => "Return-to-work rate",
    // The SLA strip's RTW rate: 100 or 0 per settled claim, averaged over **all**
    // of them including those with no recorded duration — the other denominator.
    // Rounded to a whole percent and then scaled to basis points, which is the
    // scale the wire and the reference line share.
    valueOf: (claims) => {
      const settled = trendSettled(claims);
      if (settled.length === 0) return null;
      return (
        mean(
          settled.map((claim) => (claim.return_status === FULLY_RECOVERED ? 100 : 0)),
          0,
        ) * 100
      );
    },
    // Every settled claim, recorded duration or not — the other denominator.
    populationOf: (claims) => trendSettled(claims).length,
    format: (value) => `${trendBasisPoints(value)}%`,
    targetCaption: `Target ${trendBasisPoints(TREND_RTW_TARGET_BP)}%`,
  },
  {
    metric: "paid_cents",
    testId: "trend-paid",
    title: () => "Total paid",
    // A sum of the three paid columns, in cents. Zero is an answer.
    valueOf: (claims) =>
      claims.reduce(
        (total, claim) =>
          total + claim.paid_indemnity + claim.paid_medical + claim.paid_expense,
        0,
      ),
    // A sum over the whole bucket, so the whole bucket is behind it.
    populationOf: (claims) => claims.length,
    format: (value) => TREND_DOLLARS.format(value / 100),
  },
];

export interface ExpectedTrendLine {
  /** `data-cohort-key` — the empty string on an unsplit line. */
  cohortKey: string;
  /** The name the legend and the accessible list print. */
  label: string;
  /** `data-fill` — the hue this cohort holds on **every** chart. */
  fill: string;
  /** One rendered `sr-only` list item per bucket, in the window's order. */
  points: string[];
  /** The bucket keys this line marks thin — on *this metric's* evidence. */
  lowConfidenceBuckets: string[];
  /** The bucket keys whose period had not finished when the window was cut. */
  partialBuckets: string[];
  /** The bucket keys this line has no value for — a gap, never a zero point. */
  gapBuckets: string[];
}

export interface ExpectedTrendCard {
  testId: string;
  /** The card's heading, as rendered. */
  title: string;
  /** Its lines, in the server's order: metric-major, cohort by wire key. */
  lines: ExpectedTrendLine[];
  /** The footnote, as rendered — gaps, then any target, then any note. */
  footnote: string;
}

export interface ExpectedTrends {
  /** The day the window was cut against and the ages counted to. */
  asOf: string;
  /** Every bucket in the window, in the window's order. */
  bucketKeys: string[];
  bucketLabels: string[];
  /** One bucket's inclusive day bounds — what a drill's date facets carry. */
  bucketBounds: Record<string, { from: string; to: string }>;
  /** The cohort vocabulary, in palette-slot order. `[]` when unsplit. */
  cohortKeys: string[];
  /** The legend's rows, as rendered text, and the hue each carries. */
  legendRows: string[];
  legendFills: string[];
  /** The legend's accessible name. */
  legendLabel: string;
  /** The window caption above the cards, as rendered. */
  windowCaption: string;
  /** The five cards, in the section's layout order. */
  cards: ExpectedTrendCard[];
  /** The same five, by `data-testid` stem. */
  card: Record<string, ExpectedTrendCard>;
  /** The filter set one bucket of one cohort drills with. */
  drillFilters: (bucketKey: string, cohortKey?: string) => DrillFilters;
}

/**
 * What a persona's Trends section must render, for one grain/anchor/cohort.
 *
 * **Rendered strings rather than raw numbers**, this file's standing
 * discipline and the sharper half of it here: three of the five metrics carry a
 * unit the wire does not (days, basis points, cents), so a page that printed
 * `8000` where `80.00%` belongs — or dollars where cents were sent — would
 * satisfy any comparison made against a number and fail this one.
 *
 * The three set-valued fields (`bucketKeys`, `cohortKeys`, and each line's
 * `gapBuckets`) exist because the ordering assertions cannot see a wrong
 * *population*: a window that lost its first bucket and gained one at the end
 * is still in ascending order, and a cohort split that dropped a value still
 * draws its remaining lines in slot order.
 */
export function expectedTrendsFor(
  persona: { name: string; role: string },
  query: TrendQuery = {},
): ExpectedTrends {
  const grain = query.grain ?? "month";
  const anchor = query.anchor ?? "fnol";
  const dimension = query.cohort ?? "none";

  const asOf = trendAsOf();
  const window = trendWindow(grain, asOf);
  const visible = claimsFor(persona.name, persona.role);

  // The window's own population, counted once: a claim is in the window when its
  // anchor date falls between the outer edges, and the caption says so beside
  // the whole book's size. Both dates are zero-padded ISO days, so the string
  // comparison is the calendar comparison.
  const windowFrom = window[0].from;
  const windowTo = window[window.length - 1].to;
  const inWindow = visible.filter((claim) => {
    const date = anchorDateOf(claim, anchor);
    return date >= windowFrom && date <= windowTo;
  });

  // **The vocabulary is the window's, not the book's**, and the order of these
  // two statements is the whole of it: a cohort value nobody filed a claim
  // under inside the window is a line nobody drew, so it takes no slot and no
  // legend row. Derived from the persona's whole book this agreed with the
  // server only while every value happened to fall inside twelve periods —
  // green on today's date, red on a twelve-*week* window that starts after the
  // last Aerospace claim, and wrong in a way that moves every `paletteSlot`
  // after the missing value rather than only dropping a row.
  const cohortKeys = trendCohortKeys(inWindow, dimension);

  const bucketBounds: Record<string, { from: string; to: string }> = {};
  for (const bucket of window) {
    bucketBounds[bucket.key] = { from: bucket.from, to: bucket.to };
  }

  /** The claims of one bucket of one cohort — the only population there is. */
  function claimsIn(bucket: TrendBucket, cohortKey: string | null): SeedClaim[] {
    return visible.filter((claim) => {
      if (cohortKey !== null && trendCohortOf(claim, dimension) !== cohortKey) return false;
      const date = anchorDateOf(claim, anchor);
      return date >= bucket.from && date <= bucket.to;
    });
  }

  function labelOf(cohortKey: string | null): string {
    if (cohortKey === null) return TREND_WHOLE_BOOK_LABEL;
    return TREND_COHORT_VALUE_LABEL[dimension]?.[cohortKey] ?? cohortKey;
  }

  const cards = TREND_METRICS.map((spec): ExpectedTrendCard => {
    const clauses: string[] = [];
    const lines = cohortKeys.map((cohortKey, slot): ExpectedTrendLine => {
      const points: string[] = [];
      const lowConfidenceBuckets: string[] = [];
      const partialBuckets: string[] = [];
      const gapBuckets: string[] = [];

      for (const bucket of window) {
        const claims = claimsIn(bucket, cohortKey);
        const value = spec.valueOf(claims, asOf);
        // The server's verdict, restated: strictly above zero and at or below
        // the ceiling, over **this metric's** evidence rather than the bucket's
        // claims. A bucket with no claims *behind this figure* is not marked —
        // its value is already absent and a "low confidence" beside an em dash
        // would be a second way of saying nothing.
        const behind = spec.populationOf(claims);
        const low = behind > 0 && behind <= TREND_LOW_CONFIDENCE_CLAIM_MAX;
        // A period the clock has not reached the end of, decided from the
        // bucket's own last day rather than from its position: the newest
        // bucket of a window that ends today, and none of the others.
        const partial = bucket.to > asOf;
        if (low) lowConfidenceBuckets.push(bucket.key);
        if (partial) partialBuckets.push(bucket.key);
        if (value === null) gapBuckets.push(bucket.key);
        points.push(
          `${bucket.label}: ${value === null ? TREND_NO_VALUE : spec.format(value)}${
            low ? " (low confidence)" : ""
          }${partial ? " (partial period)" : ""}`,
        );
      }

      clauses.push(
        cohortKey === null
          ? `${String(gapBuckets.length)} of ${String(window.length)} periods have no data`
          : `${labelOf(cohortKey)} ${String(gapBuckets.length)}/${String(
              window.length,
            )} without data`,
      );

      return {
        cohortKey: cohortKey ?? "",
        label: labelOf(cohortKey),
        fill: trendFill(dimension, cohortKey, slot),
        points,
        lowConfidenceBuckets,
        partialBuckets,
        gapBuckets,
      };
    });

    // One clause for the card rather than one per line, because every line in a
    // card spans the same window — and it names the bucket the *clock* left
    // unfinished, which is the last one here only because a default window ends
    // today.
    const unfinished = window.find((bucket) => bucket.to > asOf);
    if (unfinished !== undefined) clauses.push(`${unfinished.label} is a part period`);
    if (spec.targetCaption !== undefined) clauses.push(spec.targetCaption);
    if (spec.note !== undefined) clauses.push(spec.note(asOf));

    return {
      testId: spec.testId,
      title: spec.title(anchor),
      lines,
      footnote: clauses.join(" · "),
    };
  });

  const card: Record<string, ExpectedTrendCard> = {};
  for (const entry of cards) card[entry.testId] = entry;

  // Which facet a cohort value narrows on — the same column the split was made
  // on, which is what makes the drilled list reconcile with the line clicked.
  const COHORT_FACET: Record<TrendCohortDimension, DrillFacet | null> = {
    none: null,
    severity_band: "severityBand",
    disability: "disability",
    sector: "sector",
  };

  return {
    asOf,
    bucketKeys: window.map((bucket) => bucket.key),
    bucketLabels: window.map((bucket) => bucket.label),
    bucketBounds,
    cohortKeys: cohortKeys.filter((key): key is string => key !== null),
    legendRows:
      dimension === "none" ? [] : cohortKeys.map((key) => labelOf(key)),
    legendFills:
      dimension === "none"
        ? []
        : cohortKeys.map((key, slot) => trendFill(dimension, key, slot)),
    legendLabel: `${TREND_COHORT_LABEL[dimension]} — show claims`,
    windowCaption: `${windowFrom} to ${windowTo} · ${String(
      window.length,
    )} periods · ${String(inWindow.length)} of ${String(
      visible.length,
    )} claims · as of ${asOf}`,
    cards,
    card,
    drillFilters: (bucketKey, cohortKey) => {
      const bounds = bucketBounds[bucketKey];
      if (bounds === undefined) throw new Error(`no bucket ${bucketKey} in this window`);
      // The bounds go on the pair belonging to the **anchor the response was
      // bucketed by**: a point on the injury-date series opened with
      // `filter[fnolFrom]` returns a plausible list of the wrong claims.
      const filters: DrillFilters =
        anchor === "fnol"
          ? { fnolFrom: bounds.from, fnolTo: bounds.to }
          : { doiFrom: bounds.from, doiTo: bounds.to };
      const facet = COHORT_FACET[dimension];
      if (cohortKey === undefined || facet === null) return filters;
      return { ...filters, [facet]: cohortKey };
    },
  };
}
