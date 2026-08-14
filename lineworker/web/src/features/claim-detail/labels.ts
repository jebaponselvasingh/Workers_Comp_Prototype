/**
 * Display labels for the case file's snake_case enums — UI-owned, by convention.
 *
 * The wire carries tokens (`approaching_mmi`, `need_for_additional_information`)
 * and the browser owns what a human reads. That split is what lets a label be
 * reworded without a migration, and it is why the server sends a *note* for
 * the phase and coordination cards but not a *label*: the note is prose about
 * the rule and belongs with the rule; the label is a name for a value.
 *
 * Every map is `Record<Enum, string>` rather than a lookup with a fallback.
 * A new enum member then fails the build here — beside the other labels,
 * where somebody can write one — instead of rendering `undefined` next to a
 * claimant's name.
 */
import type {
  CommStatus,
  CoordinationStatus,
  Disability,
  DocType,
  IndemnityType,
  RecoveryWindow,
  ReserveVerdict,
  ReturnStatus,
  RiskBand,
  Stage,
  TreatmentPhase,
} from "@/api/claims";

/** The four lifecycle steps, as the stepper and the stage pill spell them. */
export const STAGE_LABEL: Record<Stage, string> = {
  intake: "Intake",
  investigation: "Investigation",
  treatment: "Treatment",
  settled: "Settled",
};

/**
 * The gauge's centred label — abbreviated because it is drawn inside a 68px
 * SVG arc. **Not for prose:** see `SEVERITY_WORD` below.
 */
export const RISK_LABEL: Record<RiskBand, string> = {
  high: "High",
  med: "Med",
  low: "Low",
};

/**
 * The same band, spelled out, for sentences.
 *
 * The header's injury summary reads "… · Medium severity", and the
 * prototype interpolates the dataset's own `severity` string there, which is
 * `High` / **`Medium`** / `Low` — 51 of the 100 claims are "Medium". Reusing
 * the gauge's abbreviation printed "Med severity" while the gauge's own
 * accessible name said "Medium risk", so one claim read two ways to a
 * sighted user and a screen reader (code review, 2026-08-12).
 */
export const SEVERITY_WORD: Record<RiskBand, string> = {
  high: "High",
  med: "Medium",
  low: "Low",
};

export const RISK_DESCRIPTION: Record<RiskBand, string> = {
  high: "High risk",
  med: "Medium risk",
  low: "Low risk",
};

/**
 * The severity card's trend line, verbatim from the prototype (Story 2.4).
 *
 * The arrows are decoration on a band the server decided — they do not
 * describe a *direction of travel*, which the console has no history to
 * compute, and the wording is the prototype's for that reason: "⬆ High risk"
 * rather than "rising".
 */
export const RISK_TREND: Record<RiskBand, string> = {
  high: "⬆ High risk",
  med: "→ Moderate",
  low: "↓ Low",
};

/** The prototype's phase-banner headings, verbatim. */
export const PHASE_LABEL: Record<TreatmentPhase, string> = {
  early: "Early Treatment & Diagnosis",
  active: "Active Treatment & Therapy",
  approaching_mmi: "Approaching MMI / RTW Planning",
};

/** The prototype's coordination headings, verbatim — emoji included. */
export const COORDINATION_LABEL: Record<CoordinationStatus, string> = {
  coordination_gap: "⚠ Coordination Gap",
  awaiting_information: "⚠ Awaiting Information",
  legal_coordination: "⚖ Legal Coordination Active",
  on_track: "✓ Coordination On Track",
};

export const COMM_STATUS_LABEL: Record<CommStatus, string> = {
  fnol_received: "FNOL Received",
  incomplete_information: "Incomplete Information",
  need_for_additional_information: "Need for Additional Information",
  documents_received_and_approved: "Documents Received and Approved",
  initial_approval_provided_treatment_underway:
    "Initial Approval Provided, Treatment Underway",
};

export const RETURN_STATUS_LABEL: Record<ReturnStatus, string> = {
  under_treatment: "Under Treatment",
  returned_and_under_therapy: "Returned and Under Therapy",
  returned_and_fully_recovered: "Returned and Fully Recovered",
};

export const DISABILITY_LABEL: Record<Disability, string> = {
  temporary: "Temporary",
  permanent: "Permanent",
};

/**
 * The prototype's five recovery windows, verbatim.
 *
 * These *were* the stored value: `claim.recovery` held `"6-8 Weeks"` until
 * the Story 2.3 code review, which made the column a `RecoveryWindow` enum
 * and moved the wording here. The point of the move is that this file can be
 * reworded — or translated — without a migration, and without changing what
 * `treatment_phase` computes from the same claim.
 */
export const RECOVERY_LABEL: Record<RecoveryWindow, string> = {
  weeks_0_2: "0-2 Weeks",
  weeks_2_4: "2-4 Weeks",
  weeks_4_6: "4-6 Weeks",
  weeks_6_8: "6-8 Weeks",
  over_1_year: "Greater than 1 Year",
};

/**
 * The four indemnity types, spelled the way the prototype spells them.
 *
 * The prototype's value *is* this string — `"TTD — Temporary Total
 * Disability"` — and it recovers the abbreviation with `.split(" — ")[0]`
 * wherever it needs the short form. Here the wire carries the token and this
 * map is the label, so nothing has to be parsed back apart; the one place the
 * abbreviation is still spelled server-side is the reserve-rationale
 * paragraph, which is prose the server writes.
 */
export const INDEMNITY_TYPE_LABEL: Record<IndemnityType, string> = {
  ttd: "TTD — Temporary Total Disability",
  tpd: "TPD — Temporary Partial Disability",
  ppd: "PPD — Permanent Partial Disability",
  ptd: "PTD — Permanent Total Disability",
};

/**
 * The four reserve adequacy verdicts, spelled the way the prototype spells them.
 *
 * The prototype's value *is* this string (`label: "Reserve Light"`), which is
 * what makes a re-wording a breaking change for every consumer that compared
 * against one. Here the wire carries `light` and this map is the label — and
 * the label is deliberately the *state* rather than the advice: what to do
 * about a light reserve is the rationale's sentence, which the server writes.
 */
export const RESERVE_VERDICT_LABEL: Record<ReserveVerdict, string> = {
  light: "Reserve Light",
  adequate: "Reserve Adequate",
  heavy: "Reserve Heavy",
  closed_final: "Closed — Final",
  // Not one of the prototype's four, because the prototype has no data that can
  // be absent. The wording says what is missing rather than that something went
  // wrong: a claim whose bills are not on file has not failed a check, it has
  // not had one — and the server's sentence beside it names the missing term.
  indeterminate: "Awaiting Bill Data",
};

/**
 * Document types, spelled out for the intake checklist.
 *
 * Story 2.5 adds the short chips the Documents tab uses (`FROI` → `C-1`,
 * `INCIDENT` → `INC`, …). A checklist row is a sentence a handler reads
 * once — "Incident Investigation Report" says what is missing; "INC" makes
 * them look it up.
 */
export const DOC_TYPE_LABEL: Record<DocType, string> = {
  froi: "First Report of Injury (FROI)",
  incident: "Incident Investigation Report",
  medauth: "Medical Authorization",
  wage: "Wage Statement (AWW)",
  rtw: "Return-to-Work Letter",
  legal: "Legal / Statutory Filing",
};
