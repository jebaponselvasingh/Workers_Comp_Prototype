---
baseline_commit: ae93ae5d0f6ca592c02f60095d5bd170b0de4e0e
---

# Story 3.1: Statutory Benefit Calculation

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want the weekly indemnity benefit computed from AWW with statutory clamping and an override,
so that benefit amounts are correct and defensible.

## Acceptance Criteria

1. **Given** a claim, **when** the benefit card renders in Overview, **then** it shows the weekly indemnity computed once in `services/financials`: comp-rate % of AWW (default 66.67%; 100% PTD when disability is permanent and severity ≥ 85), clamped to the state statutory min/max from a maintained `state_rate_schedule` table created and seeded by this story (FR-H-3, NFR-4).
2. **And** all money is integer cents end to end, formatted only in the UI.
3. **Given** the derived indemnity type, **when** displayed, **then** TTD / TPD / PPD / PTD is determined from disability + return status by the single registered derivation, and the card shows type, state min/max, the 7-day waiting-period payment note, and the auto-generated reserve rationale paragraph.
4. **Given** the editable comp-rate override, **when** changed, **then** an audited CAS command persists it, the benefit recomputes server-side, and a ↺ reset control restores the default (FR-H-3, AD-4).
5. **Given** the benefit formulas, **when** tested, **then** they live in typed Python with tunables (default rate, PTD threshold) read from ZEN (AD-8), and Hypothesis property tests assert the clamp holds across statutory ranges for all seeded states (NFR-7).

## Tasks / Subtasks

- [x] Task 1: `state_rate_schedule` migration + seed (AC: 1, 2)
  - [x] Alembic migration creating `state_rate_schedule` (surrogate `id` PK, `state_code` unique, `state_name`, `weekly_min_cents int`, `weekly_max_cents int`, `effective_date date`) — money columns integer cents, snake_case per the Excel canonical naming
  - [x] Seed the prototype's 17 jurisdictions (`STATE_WC_RATES`, prototype line 638–645: OH/MN/IL/WA/TX/MA/IA/MI/SC/KY/AL/NJ/SD/IN/CA/NC/AZ), dollar values × 100 into cents
  - [x] Migration-time assertion: every seeded claim's `state` has a `state_rate_schedule` row — the prototype's silent `{max:1200,min:250}` fallback (line 717) does NOT survive; a missing state is a data error surfaced as problem+json, not a default (NFR-4)
- [x] Task 2: Benefit formula in `services/financials` (AC: 1, 2, 5)
  - [x] Typed function `compute_benefit(claim, rate_row, params) -> Benefit` returning `{weekly_cents, comp_rate_pct, default_pct, indemnity_type, state_min_cents, state_max_cents, waiting_days, is_overridden}`
  - [x] Formula (typed Python): `weekly_cents = round(aww_cents * pct / 100)` then clamp `max(min_cents, min(max_cents, weekly_cents))`; document the rounding convention (round-half-up on cents) in the function docstring
  - [x] PTD branch: `disability == permanent AND severity_score >= ptd_threshold` → comp rate 100%
  - [x] Tunables from a ZEN JDM document (`benefit_params`): `default_comp_rate_pct: 66.67`, `ptd_comp_rate_pct: 100`, `ptd_severity_threshold: 85`, `waiting_period_days: 7` — DB-versioned with effective dates (AD-8); the formula itself never lives in JDM
  - [x] Expose via the claim-detail read model / a `GET .../benefit` projection so the SPA never computes any of this (AD-1)
- [x] Task 3: Indemnity-type derivation (AC: 3)
  - [x] Single registered function in `services/derivations`: permanent → `ptd` (when PTD branch true) else `ppd`; temporary → `tpd` when return status indicates therapy/modified duty else `ttd` (prototype line 723–725 is the behavioral reference)
  - [x] snake_case enum `indemnity_type: ttd|tpd|ppd|ptd` in DB/API; UI owns the display labels ("TTD — Temporary Total Disability", …)
  - [x] Never a user-writable column (AD-10)
- [x] Task 4: Reserve rationale paragraph (AC: 3)
  - [x] Server-side deterministic template in `services/financials` mirroring prototype line 1257: severity + injury + recovery window, surgical-exposure clause, litigation-buffer clause, permanent-vs-TTD clause, statutory min/max reference, and the override clause when `is_overridden`
  - [x] This is deterministic service output, NOT an LLM narrative (AD-2) and NOT an `ai_insight` row — it renders synchronously with the benefit card
- [x] Task 5: Comp-rate override command (AC: 4)
  - [x] Nullable `comp_rate_override_bp int` (basis points; 6667 = 66.67%) on `claim` — exact integer storage, no floats in DB
  - [x] PATCH-shaped audited CAS command in `services/claims` (the claim entity's write-owner, AD-12) with `expected_version`; validation 0–150% inclusive rejected inline via problem+json (prototype input bounds, line 731/1261); audit event with before/after in the same transaction (AD-4)
  - [x] Reset command (↺) nulls the override through the same audited CAS path; benefit recomputes on read in `services/financials`
  - [x] SPA: editable number input + ↺ reset shown only when overridden; optimistic update allowed (user-entered scalar), 409 rollback renders fresh state inline (AD-9); mutation invalidates the claim + benefit query keys
- [x] Task 6: Benefit card UI (AC: 1, 2, 3, 4)
  - [x] "Benefit calculation & reserve details" card in the Overview tab (in the stage variants that carry it in the prototype — investigation and treatment), fields: Comp rate (editable, ↺ when overridden) · Weekly indemnity benefit · Indemnity type · state statutory min/max · "Weekly, starting 7-day waiting period after DOI" payment-schedule note · state name · reserve-rationale ratbox (prototype 1258–1273 for layout/feel)
  - [x] All money formatted in the UI only, from cents (AC 2); figures typeset in the mono token per Epic 1 design tokens
- [x] Task 7: Tests (AC: 5)
  - [x] Hypothesis property test: ∀ seeded state rows, ∀ `aww_cents ≥ 0`, ∀ valid override pct ∈ [0,150] → `state_min_cents ≤ weekly_cents ≤ state_max_cents` (NFR-7)
  - [x] Unit tests: PTD branch on/off around threshold 85 (84/85/86), default vs override, reset restores default, indemnity-type derivation for all four types, missing-state error path
  - [x] Command tests: CAS 409 on stale version; audit row emitted in-transaction; out-of-range override rejected
  - [x] E2E `e2e/stories/3-1-statutory-benefit-calculation.spec.ts` `@story:3-1 @epic:3`: `@smoke` happy path — login as handler persona, open a claim, benefit card shows weekly/type/min-max; then edit comp rate → recompute; ↺ reset → default restored

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story is the first slice of the Epic 3 financial engine: the benefit **formula**, its statutory rate table, the indemnity-type derivation, the rationale paragraph, and the comp-rate override command + card UI. It builds on Epic 1 (schema/auth/scoping/e2e harness) and Epic 2 (detail pane, stage-adaptive Overview from Story 2.2 — the card slots into that existing Overview; do not rebuild the tab).

It is **not**: the reserve adequacy verdict (3.2), the payment schedule or `bill`/`expense` tables (3.3), payment approval/batch (3.4), or the action checklist (3.5). The 7-day waiting period is displayed here as the payment note; the schedule that *applies* it (start = DOI + 7 days) is generated in 3.3. Do not create `payment_schedule_week` here. The prototype (`docs/Workers_Comp_Prototype.html`) is a behavior/design reference only — never a code source.

### Architecture compliance (binding ADs for this story)

- **AD-1:** the benefit figure appears on screen ⇒ a service computed it; the SPA never multiplies AWW by anything.
- **AD-2:** `compute_benefit` exists exactly once, in `services/financials`; every consumer (Overview card, 3.3 schedule, Epic 6 copilot tools) calls it — no re-derivation anywhere.
- **AD-4:** the override is a PATCH-shaped, `expected_version`-CAS'd, audited command; 409 problem+json with fresh entity on mismatch.
- **AD-8:** tunables (66.67 default, PTD 100/85, waiting 7) live in a DB-versioned, effective-dated ZEN JDM document; the clamp/rounding formula is typed Python. Each rule element in exactly one tier.
- **AD-10:** `indemnity_type` has one registered derivation in `services/derivations`; not user-writable.
- **AD-12:** `claim` writes (the override field) go through `services/claims` commands; `services/financials` computes but does not write the claim row.
- **Conventions:** money integer cents in DB, API, and across the ZEN boundary (the pct tunables are rates, not money); camelCase JSON via Pydantic alias; enums snake_case with UI labels.

### Data notes

- **Creates + seeds:** `state_rate_schedule` (reference data; refresh ownership/update cadence is an architecture Deferred item — seed the 17 prototype states, structure for effective-dating so a later refresh process slots in).
- **Alters:** `claim` — adds nullable `comp_rate_override_bp` (write-owner `services/claims`).
- **Uses:** `claim` (aww, disability, severity_score, return_status, state), seeded by Story 1.2.
- The prototype stores AWW in whole dollars (`aww:1432`); the 1.2 seed already carries cents — verify the seed's `aww_cents` before wiring the formula (a ×100 mismatch is the classic bug here).

### UX notes

- UX-DR5 (6-tab detail pane; the card lives inside Overview's stage variants). Card layout, kv rows, ratbox rationale, ↺ affordance: prototype lines 1255–1274.
- Inline validation on the override input, no native dialogs (NFR-3, UX-DR11); 409 conflict renders fresh state inline at the field (AD-9).
- Design-token ruling (from Story 1.1): the prototype's LIGHT palette is canonical; epics.md's "dark console aesthetic" wording is a documented discrepancy. Use Epic 1's tokens — no new colors.
- Drop the prototype's "Illustrative figures" disclaimer line only when NFR-4 validation is done; until statutory data is production-validated (Deferred), keep an equivalent provenance note sourced from the `state_rate_schedule` effective date.

### Testing requirements (this story's definition of done)

- Hypothesis property tests on the clamp across all seeded states (AC 5, NFR-7) — this is the named NFR-7 exemplar; do not substitute example-based tests.
- Unit: PTD threshold boundaries, all four indemnity types, override/reset, missing-state error.
- Command: CAS 409, in-transaction audit emission, validation rejection.
- E2E: `e2e/stories/3-1-statutory-benefit-calculation.spec.ts` tagged `@story:3-1 @epic:3`, exactly one `@smoke` happy path, run against the freshly reset e2e compose stack. Story cannot move to `review`/`done` until it passes (AD-15).

### Project Structure Notes

- New code lands in `lineworker/server/services/financials/` (formula, rationale), `services/derivations/` (indemnity type), `services/claims/` (override command), `server/rules/` (JDM `benefit_params` document + ZEN wrapper first use — the ZEN engine dependency (`zen-engine`, pinned) is installed by this story if Epic 2 has not already), `server/data/` (migration), `web/src/features/claim-detail/` (card).
- First JDM document of the build: establish the loading path (DB-versioned document, effective-date selection) cleanly — 3.2 and 3.5 reuse it.
- Rounding convention and basis-point storage are decisions made here; record them in code docstrings so later financial stories inherit them.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 3.1]
- AD-2 deterministic core, AD-8 two-tier rules, AD-4 audited CAS, AD-10 derivations, AD-12 ownership: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- Money-in-cents (incl. ZEN boundary), enums, write-concurrency conventions: [Source: ARCHITECTURE-SPINE.md#Consistency Conventions]
- Capability map row (benefit calc → `services/financials`): [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- Deferred: state statutory coverage & refresh cadence: [Source: ARCHITECTURE-SPINE.md#Deferred]
- Behavioral reference: `computeBenefit`/override (lines 715–738), `STATE_WC_RATES` (638–645), benefit card + rationale (1255–1274): [Source: docs/Workers_Comp_Prototype.html]
- Narrative on the three logic layers: [Source: docs/Architecture-LINEWORKER.md#3.2 The three logic layers]
- FR-H-3 / NFR-4 definitions: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-story` workflow.

### Debug Log References

- **The PTD threshold had two plausible homes and only one correct one, and the choice changed the design.** The story lists `ptd_severity_threshold: 85` among `benefit_params`' tunables, beside the two comp rates. But the same cut-off decides the *indemnity type*, which AC 3 makes a registered derivation — and `DerivationThresholds` is the single argument every registered derivation is built from. Putting it in `benefit_params` would have left `services/financials` comparing a severity score itself, which is a second reader of one rule element (AD-8) and a second computer of one condition (AD-10). It went into `derivation_thresholds` v4, and `compute_benefit` now asks the derivation whether the claim is `ptd` and pays accordingly. The prototype's `computeBenefit` computes `isPTD` inline and uses it twice, which is the failure this avoids; `tests/test_benefit_block.py::test_the_financial_engine_never_compares_a_severity_score` greps the sources so it cannot come back.
- **The story's `default_comp_rate_pct: 66.67` is a float, and the story's own Task 5 requires integer basis points for the override.** Same rule element, two units — and 66.67 is not representable in binary floating point, so "is this claim overridden?" would have been a comparison two values can fail by 1e-14. The document declares `defaultCompRateBp: 6667` instead, which makes the whole calculation integer arithmetic from the column through the rules tier to the PATCH body. The *values* are the story's; only the unit differs, and the reasoning is written into the document's own description field.
- **`round()` is banker's rounding and would have been wrong here silently.** Python rounds halves to even, so `round(2.5)` is `2`: an exactly-half-cent benefit would go to whichever neighbour happened to be even — a rule no benefit schedule states and no handler could reproduce by hand. `_round_half_up` uses `divmod` and an integer comparison, which is exact and needs no `Decimal`. Two of the four rounding tests are deliberate ties, so a `round()` reintroduced into the formula fails on them rather than on nothing.
- **The first Hypothesis property tripped the `filter_too_much` health check, and the obvious fix would have weakened it.** Asserting "an unclamped benefit is the rate applied to the wage" needs inputs that do *not* clamp, and a wide wage domain against Iowa's $420–$2,101 range clamps nineteen times in twenty. Suppressing the health check would have left the property running on a handful of survivors and saying very little. The wage is **constructed** instead: a target weekly figure is drawn from the state's own interval and the wage that produces it is derived, so `assume` fires only on the rare cent that integer division pushes back out.
- **A `Protocol` with eleven members is a smell, and splitting it made the design legible.** `compute_benefit` reads four columns; the rationale paragraph reads seven more, because it is a sentence about the whole claim. `BenefitClaim(RationaleClaim, Protocol)` keeps the dependency one-directional (`benefit.py` imports `rationale.py`, never the reverse) and states the split: those seven describe the claim, these four compute its benefit.
- **The derivation module was named after the value it exports and the structural test caught it immediately.** `services/derivations/indemnity_type.py` exporting `indemnity_type` silently rebinds the package attribute — the trap `__init__.py` documents and `test_no_derivation_module_is_named_after_the_value_it_exports` enforces. Renamed to `indemnity_classification.py`, beside `path_classification.py`.
- **Sarah Williams has no intake claims**, which three of the command tests assumed. The seeded books are 45 claims for Kaya (3 intake, 1 investigation, 15 treatment, 26 settled) and 8 for Sarah (0 / 1 / 1 / 6) — so `claims_of(SARAH, "intake")[0]` raised on the first run. The claims are now assigned per test from the stages that exist, and the audit-counting tests take a claim each so nothing shares a row. Story 2.6's Dev Agent Record records the same class of failure against `WC-20017`.
- **`Jennifer Park` cannot see Kaya's claims**, which the "a supervisor reads the same benefit" test assumed. Her book is Toyota/GM/3M; the full-portfolio supervisor is `David Bline`. Recorded because "use a supervisor" is not the same as "use a supervisor who can see this claim", and the test would have been asserting a 404 against a 404.
- **`int | None` cannot carry Pydantic's `ge`/`le` directly** — the constraint has to sit on the `int` half of the union, not on the nullable whole. `CompRateBasisPoints = Annotated[int, Field(ge=…, le=…)]` then `CompRateBasisPoints | None`, which is what puts the bound in the OpenAPI document and therefore into the generated client.

### Completion Notes List

- **AC 1 — the prototype's silent fallback is gone, and its absence is enforced in three places.** `computeBenefit` reads ``STATE_WC_RATES[c.state] || {max:1200,min:250}``: a claim in an unlisted state is clamped to two numbers belonging to no jurisdiction while the card prints that state's name beside them. Here migration 0023 **refuses to complete** while any seeded claim's state is uncovered, `services/financials` raises `MissingStateRate` rather than defaulting, and the router maps it to a problem document. The refusal is deliberately *not* graceful: a missing schedule takes the whole case file down with a 500, because blanking one card while serving the rest is the prototype's silent answer in a different shape.
- **The rate table is seeded from the prototype and asserted against the prototype**, not against its own extractor output — Story 2.5's rule. `tests/test_state_rate_migration.py` reads `STATE_WC_RATES` with a *field-keyed* regex where the extractor's is strictly positional, so the pair covers "the shape changed" and "the values are wrong" rather than failing together. The ×100 into cents is asserted explicitly, because a schedule left in dollars would clamp every claim in the portfolio to about eleven dollars a week and nothing would look obviously broken.
- **AC 2 — every figure is integer cents and every rate is integer basis points, end to end.** The column, the JDM document, the wire, the PATCH body and the input's committed value are all integers; `web/src/lib/rate.ts` is the rate's `money.ts`, the one place a basis point becomes a percentage, and it is exact by construction rather than by `toFixed` rounding a float back to where it started. `tests/test_benefit_block.py` asserts that the only `$` anywhere on the block is inside the rationale paragraph.
- **The one place the server formats money, and why it is not a contradiction.** The reserve rationale is a *sentence*, in the same category as a timeline event's description, which `services/claims/timeline.py` has written server-side since Story 2.3. A paragraph with holes in it for the browser to fill is a template, and templates in the browser are what AD-1 removes. The obligation that creates is real and written down: `_dollars` must agree with `lib/money.ts`'s `formatCents`, because the reserve appears as prose on the card and as a formatted figure two rows above — both drop the cents, both round half up, and the pairs that differ under the alternatives are pinned by test.
- **AC 3 — the indemnity type is a registered derivation whose answer the financial engine consumes.** This is the first registry entry another *service* reads rather than a card: `compute_benefit` asks it whether the claim is `ptd` and pays `ptdCompRateBp` if so. The consequence is that the PTD condition has exactly one reader, so the rate a claim is paid at and the type it is called can never disagree — which the prototype cannot say, because it computes the condition inline and uses it twice.
- **AC 3 — the rationale is the prototype's paragraph, clause for clause, and it is deterministic.** Not an LLM narrative and not an `ai_insight` row (AD-2): the same claim always produces the same sentence, from its own columns, rendering synchronously with the card. The clause order is the prototype's and is asserted, because the paragraph reads as a narrowing argument — from what the injury is, to what was done to the number.
- **Two server-side prose vocabularies, and the reason they duplicate the browser's.** `SEVERITY_WORDS` and `RECOVERY_PHRASES` restate wordings `labels.ts` also owns. That is `services/claims/reference.py`'s `FIELD_LABELS` arrangement: the UI owns labels it *renders*, the server owns words it *writes into prose* — and they are not the same strings, since a select shows "6-8 Weeks" where a sentence needs "6-8 weeks". Sharing one map would mean lower-casing at one call site and not the other.
- **AC 4 — set and reset are one command, because they are one column.** `compRateBp: null` *is* the ↺. A second endpoint would duplicate the five-refusal ladder to express "write NULL" and would give one column's history two action names in the audit log, so a reader reconstructing how a claim's rate moved would have to know both. It is also why the API's field is **required but nullable**, the opposite of `ClaimFieldPatch`'s optional members: there `null` is a caller error, here it is the whole point.
- **`isOverridden` is published rather than inferred**, and the fixture that proves it matters is `BENEFIT_OVERRIDDEN_AT_DEFAULT`. A handler who types 66.67 back in has made a decision the case file records — the column is non-null — and the ↺ has to appear for them. A client comparing `compRateBp` with `defaultCompRateBp` would answer "not overridden" and hide the only way back to the statutory rate.
- **The override is refused, never clamped.** The prototype's `updateCompRate` discards an out-of-range entry by re-rendering, so a handler who typed `700` watches the field snap back with no explanation. Here it is a 422 the field renders inline, with what they typed kept so it can be corrected — `normalise_severity`'s argument about `Math.max(0, Math.min(100, n))`, applied to money.
- **AC 4 — "recomputes server-side" is asserted in a way only a server can satisfy.** At 100% of wage the answer is the AWW clamped into the state's range, which `test_the_weekly_figure_is_the_servers_answer_not_the_clients` computes from the payload's *own* figures — no rounding, no rate arithmetic, nothing restating the formula. A route that echoed the submitted rate without recalculating passes every assertion about the rate and fails on the weekly figure.
- **The comp rate is the one optimistic write on the case file, and the severity score is deliberately not.** AD-9 permits an optimistic update for a user-entered scalar; the difference is whether the scalar has a *derived appearance*. A severity score is coloured by its band, so writing the digits in while the colour waited would render a 78 in green — a state that exists nowhere. A comp rate is not banded, coloured or thresholded, so the digits move immediately while the weekly figure, the indemnity type and the rationale all wait for the server. `applyOptimisticCompRate` is exported so a test can assert exactly what it leaves alone.
- **The queue is deliberately not invalidated** by this mutation, unlike Story 2.4's severity write. No queue card shows a comp rate and nothing in the priority score reads one, so a refetch of every card in the caseload would be work with no visible effect.
- **A new timeline tag rather than `edit`.** A comp-rate override is a financial decision on the case file, and a reader scanning a timeline for what moved a claim's money should filter on a token rather than parse descriptions — which is the one thing `settlement` already proves a tag is good for (`_settlement_date` matches on it). `TimelineTag` is a `Text` column precisely so a story can add one without a migration. The sentence itself names the field and never the rate, which is Story 2.4's rule for the severity score and, if anything, stronger here: a log line quoting one percentage out of a sequence reads as *the* comp rate on the claim.
- **`state_rate_schedule` is the fourth sanctioned unscoped repository** (`glossary`, `statutory_forms`, `identity` are the others), and the carve-out is argued rather than assumed: a jurisdiction's weekly maximum is a statement about the *state*, not about anybody's claim. The claim-scoped half is asked first and elsewhere — the caller resolves the claim through `select_claim_detail`, which is scoped, and what arrives here is a two-letter code.
- **The benefit block is on the case file at every stage; the card renders on two.** The figure is a fact about the claim throughout — a settled claim was paid a weekly indemnity, an intake claim already has a wage and a state — and Story 3.3's payment schedule is a Bills-tab surface that needs it at whatever stage the claim is in. Putting the block on the stage variants would have meant two copies of one field list and a payload whose shape changed under a claim as it progressed. The *card* is where the prototype puts it: investigation and treatment.
- **The prototype's "Illustrative figures" disclaimer is replaced, not dropped.** Until per-jurisdiction statutory data is validated (NFR-4, Deferred) the honest equivalent is the date the schedule on file took effect — a fact the table holds rather than a sentence hardcoded in a component. What that date *is* remains inferred rather than sourced, and that is recorded in `deferred-work.md`.
- **One divergence from the prototype's arithmetic, and it is the money convention working.** The prototype computes in dollars (`Math.round(c.aww * (pct/100))`) because its whole data model is dollars, so its figures round to the dollar before being clamped to dollar bounds. This console computes in cents, which means it can differ from the prototype by up to fifty cents on an unclamped benefit. Ported deliberately and written into `benefit.py`'s docstring.
- **Tests:** **909 server** (121 new — the seeded rate rows against a second, independently written parse of the prototype's HTML, the ×100, the coverage of every claim's jurisdiction from both the seed file and the database, the `CHECK` and the unique constraint, the `SELECT`-only grant; two Hypothesis properties over all seventeen jurisdictions, the rounding convention including two exact ties, the PTD boundary at 84/85/86, all four indemnity types, the override, the reset and the override-at-default case; the whole rationale paragraph, every conditional clause with the one it must not drag in, the clause order, the two prose vocabularies' totality and the money formatting agreement; the command's five refusals, the audit diff with integer and null values, the three timeline sentences, the no-op, the CAS, a forced read/write race, the scoped UPDATE, the agent-tool path and the API's declared bounds; the block at all four stages, the supervisor's read, the missing-state refusal and the source-level guard that keeps the PTD comparison out of `services/financials`). **253 vitest** (26 new — the six facts on the card, the waiting-period note, the paragraph verbatim, the provenance line, the PTD variant, the two stages the card renders on and the two it does not, the ↺'s presence rule including the overridden-at-default payload, what the PATCH body carries, the three inline refusals with no request behind them, the absence of a dialog, and the optimistic update's exact blast radius; plus `lib/rate.ts`'s round trip and its seven refusals). **92 Playwright** (7 new, `@story:3-1 @epic:3`, one `@smoke`).
- **Verified live.** e2e stack (`compose.e2e.yaml`, :8081) rebuilt with the new API and SPA: full suite **92/92**, the 14-test `@smoke` set and the 7-test `@epic:3` set all green against a freshly reset stack. `alembic check` reports no drift and a `downgrade 0021` → `upgrade head` round trip completes clean. `npm run generate:api` leaves `src/api/schema.d.ts` byte-identical on a second run. ruff + format + mypy clean; eslint clean (8 pre-existing warnings, none new); tsc clean for both `web/` and `e2e/`. No new runtime or dev dependency.

### File List

**New — server**

- `lineworker/server/data/versions/20260814_0022_benefit_schema.py`
- `lineworker/server/data/versions/20260814_0023_seed_state_rates.py`
- `lineworker/server/data/versions/20260814_0024_benefit_rules.py`
- `lineworker/server/data/seed/extract_prototype_state_rates.py`
- `lineworker/server/data/seed/state_rates.json`
- `lineworker/server/data/repositories/state_rates.py`
- `lineworker/server/rules/documents/benefit_params.jdm.json`
- `lineworker/server/rules/documents/derivation_thresholds.v4.jdm.json`
- `lineworker/server/services/derivations/indemnity_classification.py`
- `lineworker/server/services/financials/benefit.py`
- `lineworker/server/services/financials/rationale.py`
- `lineworker/server/services/claims/comp_rate.py`
- `lineworker/server/tests/test_state_rate_migration.py`
- `lineworker/server/tests/test_benefit_calculation.py`
- `lineworker/server/tests/test_benefit_rationale.py`
- `lineworker/server/tests/test_benefit_block.py`
- `lineworker/server/tests/test_comp_rate_command.py`

**Modified — server**

- `lineworker/server/data/models/core.py` (`StateRateSchedule`, `claim.comp_rate_override_bp`)
- `lineworker/server/data/models/__init__.py` (export)
- `lineworker/server/data/models/enums.py` (`TimelineTag.benefit`, `RUNTIME_ONLY_TIMELINE_TAGS`)
- `lineworker/server/data/repositories/__init__.py` (the fourth unscoped carve-out)
- `lineworker/server/rules/parameters.py` (`BenefitParams`, `benefit_params_for`, `DerivationThresholds.ptd_severity_threshold`)
- `lineworker/server/services/financials/__init__.py` (the package was an empty placeholder)
- `lineworker/server/services/derivations/__init__.py` (registration + the naming note)
- `lineworker/server/services/claims/detail.py` (the `benefit` block on the case file)
- `lineworker/server/api/routers/claims.py` (`BenefitResponse`, `CompRatePatch`, `PATCH /claims/{id}/comp-rate`, `_missing_state_rate`, `RATE_SCHEDULE_RESPONSE`)
- `lineworker/server/tests/test_rules_engine.py` (v4 and `benefit_params` in the seeded-document tables; the superseded-version map now covers v3)
- `lineworker/server/tests/test_rule_parameters.py` (the new block's refusals, and the comp-rate domain agreement)
- `lineworker/server/tests/test_derivations.py`, `tests/test_case_file_derivations.py` (the thresholds fixture)
- `lineworker/server/tests/test_claim_detail.py`, `tests/test_claims_queue.py` (the thresholds version)

**New — web / e2e**

- `lineworker/web/src/lib/rate.ts`
- `lineworker/web/src/lib/rate.test.ts`
- `lineworker/web/src/features/claim-detail/BenefitCard.tsx`
- `lineworker/web/src/features/claim-detail/BenefitCard.test.tsx`
- `lineworker/e2e/stories/3-1-statutory-benefit-calculation.spec.ts`

**Modified — web / e2e**

- `lineworker/web/src/api/claims.ts` (`Benefit`, `useEditCompRate`, `applyOptimisticCompRate`)
- `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/features/claim-detail/labels.ts` (`INDEMNITY_TYPE_LABEL`)
- `lineworker/web/src/features/claim-detail/InlineEditField.tsx` (`compRate`, and an optional numeric `step` — the granularity, not an increment; see the Change Log)
- `lineworker/web/src/features/claim-detail/overview/InvestigationOverview.tsx`, `overview/TreatmentOverview.tsx` (the card, and their docstrings' Epic 3 notes)
- `lineworker/web/src/features/queue/noDerivation.test.ts` (Story 3.1's derived fields; the card named in the scan assertion)
- `lineworker/web/src/test/api-mock.ts` (`BENEFIT` and its three variants; `benefit` on the four case-file fixtures)
- `lineworker/e2e/fixtures/seed.ts` (the 3.1 oracles: `expectedBenefit`, `expectedStateRate`, `formatCents`, `formatBasisPoints`, `SCHEDULE_EFFECTIVE_FROM`)

**Modified — repo**

- `_bmad-output/implementation-artifacts/deferred-work.md` (seven items)
- `_bmad-output/implementation-artifacts/3-1-statutory-benefit-calculation.md` (this file), `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-08-14: Addressed code review findings — **2 findings, 2 fixed, 0 deferred, 0 dismissed** (both rated low; neither had user-visible fallout today, and the second is the one worth reading).

  **The migration comment overstated what its own check covers.** 0023's coverage assertion is justified in a comment claiming "a later migration that inserted a claim would be covered by this check re-running from scratch on a fresh database" — which is false, and in the direction that matters: 0023 runs *before* every higher-numbered revision on a fresh database as much as on an existing one, so a future seed migration inserting a claim in an uncovered jurisdiction passes `alembic upgrade head` cleanly and surfaces only as a 500 on that claim's case file. The property at head *is* asserted, by `test_every_claim_in_the_database_has_a_schedule`, which runs after the whole chain — so the gap was in the prose rather than in the coverage. The comment now says what the check does (stops the seed from shipping uncovered) and names the test that holds the rest.

  **`step="0.5"` did the opposite of what its comment claimed.** The `step` prop was added to `InlineEditField` so the comp-rate spinner would not round a handler's decimals away, and it was set to the prototype's 0.5 — which does not achieve that. A `step` is not a convenient increment: the browser marks any value that is not a multiple of it a `stepMismatch`, and `stepUp()` snaps to the next multiple *above the step base*. So with 0.5 the statutory default itself (66.67%) was invalid, and one click of the up arrow turned it into 67.00 — the exact rounding the comment said the step existed to prevent, moved one decimal down. It is now 0.01, one basis point: the unit the column stores and the unit `parseBasisPoints` accepts, so every value the field can commit is a multiple of it by construction. A deliberate divergence from the prototype, recorded as such. The finding had no visible symptom (no form, no `:invalid` styling, `.value` reads back correctly) and therefore no test — so one was added, over five two-decimal rates rather than the one the fixture carries, and verified to fail on revert.

  Gate re-run green: ruff + format + mypy clean, **pytest 909**, **vitest 254**, **Playwright 92/92** plus the 14-test `@smoke` set against a rebuilt e2e stack.

- 2026-08-14: Story 3.1 implemented end to end — the first slice of the Epic 3 financial engine. `state_rate_schedule` created and seeded with the prototype's seventeen jurisdictions in integer cents, with a migration-time assertion that every claim in the portfolio is covered, so the prototype's silent `{max:1200,min:250}` fallback cannot be reproduced. `services/financials.compute_benefit` computes the weekly indemnity once (AD-2) from a comp rate in basis points, rounded half up on cents and clamped into the jurisdiction's bounds, with its tunables in a new `benefit_params` JDM document; `indemnity_type` joined the AD-10 registry with its cut-off in `derivation_thresholds` v4, and the financial engine consumes its answer rather than banding a severity score itself. The reserve rationale is a deterministic server-written paragraph. `PATCH /claims/{id}/comp-rate` is the build's fifth AD-4 command — one route for the override and its ↺ reset, compare-and-swapped, audited and timelined in one transaction, refusing rather than clamping — and the case file's `benefit` block is recomputed server-side on every answer. The Overview's investigation and treatment variants gained the prototype's benefit card, with the comp rate as the one optimistic write on the case file. Full gate green: ruff + format + mypy clean, pytest 909, vitest 253, Playwright 92/92 plus the 14-test `@smoke` set and the 7-test `@epic:3` set against a rebuilt e2e stack. Status → review.
