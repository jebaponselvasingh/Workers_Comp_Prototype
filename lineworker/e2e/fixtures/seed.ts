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

const caseFile = JSON.parse(readFileSync(CASE_FILE_PATH, "utf8")) as CaseFileSeed;

/** The prototype's `.slice(-6)` on the treatment overview. */
export const RECENT_TIMELINE_COUNT = 6;

/** The story's intake requirement list — restated, not read from the JDM document. */
export const REQUIRED_INTAKE_DOC_TYPES = ["froi", "incident", "medauth", "wage"] as const;

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
export function expectedRecentTimelineFor(claimId: string): ExpectedTimelineEntry[] {
  return expectedTimelineFor(claimId).slice(-RECENT_TIMELINE_COUNT);
}

export interface ExpectedChecklistRow {
  docType: string;
  received: boolean;
}

/** Received/Missing per required type, in the requirement list's order. */
export function expectedChecklistFor(claimId: string): ExpectedChecklistRow[] {
  const onFile = new Set(
    caseFile.documents.filter((doc) => doc.claim_id === claimId).map((doc) => doc.doc_type),
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
export function expectedStepperFor(stage: SeedStage): { stage: string; state: string }[] {
  const position = STAGES.indexOf(stage);
  return STAGES.map((step, index) => ({
    stage: step,
    state: index < position ? "done" : index === position ? "current" : "upcoming",
  }));
}

/** The first claim of a persona's book in a given stage, by business id. */
export function firstClaimInStage(name: string, role: string, stage: SeedStage): string {
  const claims = claimsFor(name, role)
    .filter((claim) => claim.stage === stage)
    .map((claim) => claim.claim_id)
    .sort();
  if (claims.length === 0) throw new Error(`no seeded ${stage} claim for ${name}/${role}`);
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
export function claimIdsInStage(name: string, role: string, stage: SeedStage): string[] {
  const claims = claimsFor(name, role)
    .filter((claim) => claim.stage === stage)
    .map((claim) => claim.claim_id)
    .sort();
  if (claims.length === 0) throw new Error(`no seeded ${stage} claim for ${name}/${role}`);
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
export function lastClaimInStage(name: string, role: string, stage: SeedStage): string {
  const claims = claimsFor(name, role)
    .filter((claim) => claim.stage === stage)
    .map((claim) => claim.claim_id)
    .sort();
  if (claims.length === 0) throw new Error(`no seeded ${stage} claim for ${name}/${role}`);
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
export function claimIdsWithStatus(name: string, role: string, status: string): string[] {
  const claims = claimsFor(name, role)
    .filter((claim) => claim.status === status)
    .map((claim) => claim.claim_id)
    .sort();
  if (claims.length === 0) throw new Error(`no seeded ${status} claim for ${name}/${role}`);
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
export function otherRecoveryWindow(claimId: string): { token: string; label: string } {
  const current = recoveryToken(seededField(claimId, "recovery"));
  const option = RECOVERY_WINDOWS.find((window) => window.token !== current);
  if (!option) throw new Error("the recovery vocabulary has fewer than two members");
  return option;
}

/** A seeded claim's stored value for one of the editable columns. */
export function seededField(
  claimId: string,
  field: "injury_type" | "cause" | "body_key" | "body_part" | "icd" | "recovery" | "disability",
): string {
  const claim = seed.claims.find((c) => c.claim_id === claimId);
  if (!claim) throw new Error(`no seeded claim ${claimId}`);
  return String((claim as unknown as Record<string, unknown>)[field]);
}

/** A body key the claim is *not* already assigned to, so an edit is a change. */
export function otherBodyKey(claimId: string): { key: string; label: string } {
  const current = seededField(claimId, "body_key");
  const option = BODY_PART_OPTIONS.find((o) => o.key !== current);
  if (!option) throw new Error("the body-part vocabulary has fewer than two members");
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
  prognosis: { mmi: string; rtw: string; impairment: string; litigation: string };
}

const deferred = new Map(
  ((seed as unknown as { deferred: SeedDeferred[] }).deferred ?? []).map((row) => [
    row.claim_id,
    row,
  ]),
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
    | { contraindications?: string }
    | undefined;
  if (!claim?.contraindications) throw new Error(`no seeded contraindications for ${claimId}`);
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
export function expectedForm(path: SeedPath, formCode: string): SeedRequiredForm {
  const form = requiredForms.find((f) => f.path === path && f.form_code === formCode);
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
export function firstClaimOnPath(name: string, role: string, path: SeedPath): string {
  const claims = claimsFor(name, role)
    .map((claim) => claim.claim_id)
    .filter((id) => claimPath(id) === path)
    .sort();
  if (claims.length === 0) throw new Error(`no seeded path-${path} claim for ${name}/${role}`);
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
    | { policy_num: string; doi: string; plant: string; state: string; region: string }
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
export function scoreInAnotherBand(claimId: string): { score: number; band: string } {
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
  if (!best || best.n === 0) throw new Error(`no photographed claim for ${name}/${role}`);
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

const stateRates = JSON.parse(readFileSync(STATE_RATES_PATH, "utf8")) as SeedStateRate[];

// --- Story 3.3: bills and expenses ---------------------------------------

const BILLS_PATH = fileURLToPath(new URL("../../server/data/seed/bills.json", import.meta.url));
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
const expenses = JSON.parse(readFileSync(EXPENSES_PATH, "utf8")) as SeedLineItem[];

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

  const staticTotal = claim.paid_indemnity + claim.paid_medical + claim.paid_expense;
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
export function expectedBenefit(claimId: string, overrideBp?: number): ExpectedBenefit {
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

export type SeedVerdict = "light" | "adequate" | "heavy" | "closed_final" | "indeterminate";

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
        Math.min(MAX_SCHEDULE_WEEKS, SCHEDULE_WEEKS[recoveryToken(claim.recovery)]),
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
  const unapproved = claim.stage === "intake" || claim.stage === "investigation";
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
export const DEMO_MEETING_TYPES = ["rtw_conference", "claim_review_supervisor"] as const;

export interface ExpectedMeeting {
  claimId: string;
  meetingType: (typeof DEMO_MEETING_TYPES)[number];
}

export function expectedMeetingsFor(name: string, role: string): ExpectedMeeting[] {
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
