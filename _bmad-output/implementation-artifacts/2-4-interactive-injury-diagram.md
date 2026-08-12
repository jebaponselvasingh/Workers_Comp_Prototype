---
baseline_commit: b6151e02ea196d6f9b4c551a261d1d827dabfa85
---

# Story 2.4: Interactive Injury Diagram

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want a body-map of the worker's injuries I can edit,
so that clinical severity is captured visually and accurately.

## Acceptance Criteria

1. **Given** the Injury Diagram tab, **when** it renders, **then** the ported body-silhouette SVG shows severity-colored markers at injured regions with the primary injury pulsing (UX-DR6), plus ICD-10 diagnosis, prognosis, numbered treatment plan (from `treatment_plan_step` rows, table created and seeded here), and restrictions cards.
2. **Given** the "+ Add another injury" popover, **when** body part + injury type + severity (0–100) are submitted, **then** an `additional_injury` row is created via an audited command (table created here), a secondary marker renders, and secondaries are removable via ✕ with audit (FR-H-6).
3. **Given** the editable severity score or body-part selector, **when** changed, **then** the audited command updates the claim and severity band + risk recompute server-side, refreshing gauge, queue card, and stat tiles (FR-H-6, FR-DET-2).
4. **Given** invalid input (severity out of range, missing body part), **when** submitted, **then** inline validation blocks the save with an explanation — no native dialogs (NFR-3).

## Tasks / Subtasks

- [x] Task 1: Migration — `additional_injury` + `treatment_plan_step`, seeded (AC: 1, 2)
  - [x] Alembic migration creating `additional_injury` (surrogate int PK, `claim_id` FK, `body_key` enum (the 11 keys), `body_part` label, `injury_type`, `severity_score` int 0–100 CHECK, `version` int — mutable entity per Write-concurrency convention) and `treatment_plan_step` (surrogate int PK, `claim_id` FK, `step_no` int, `description`)
  - [x] Seed `treatment_plan_step` from each prototype claim's `treatmentPlan` string array (ordered → `step_no`) — DATA source only, never code [Source: docs/Workers_Comp_Prototype.html ALL_CLAIMS line 632, rendered at line 1526]
  - [x] `additional_injury` seeds **empty** — the prototype's `additionalInjuries` store starts `{}` with no seeded entries (line 656); creating the table with no rows is correct, not an omission (discrepancy vs. the epic's "creates + seeds" phrasing — flagged in the batch report)
  - [x] If Story 1.2's `claim` schema did not carry the prognosis fields (`mmi`, `rtw`, `impairment`, `litigation` outlook) and `contraindications`, add them in this migration seeded from the prototype's `prognosis` object / `contraindications` string — check 1.2's Dev Agent Record first
- [x] Task 2: Audited commands in `services/claims` (AC: 2, 3)
  - [x] `add_additional_injury(caller_ctx, claim_business_id, expected_claim_version, {body_key, injury_type, severity_score})` — server-side validation (severity 0–100 int, body_key in the 11-key enum, injury_type non-empty) → 422 problem+json on failure; insert + same-transaction audit event + timeline event (AD-4/AD-12, pattern from 2.3)
  - [x] `remove_additional_injury(caller_ctx, claim_business_id, injury_id, expected_version)` — CAS on the injury row; audit event records the removed row as `before` diff
  - [x] `update_claim_severity(caller_ctx, claim_business_id, expected_version, severity_score)` — extends 2.3's PATCH whitelist (or a dedicated command): clamps nothing client-side, validates 0–100 server-side; on success severity band + risk recompute via the registered derivations with band thresholds (≥70 high / ≥40 med) as JDM parameters (AD-8/AD-10) [Source: docs/Workers_Comp_Prototype.html lines 682–692]
  - [x] Body-part change from this tab reuses 2.3's `update_claim_fields` command with `body_key` — one command, two surfaces (AD-12: one write path)
- [x] Task 3: API surface (AC: 1, 2, 3)
  - [x] Extend the claim-detail payload (2.2's endpoint) with the injury-diagram block: primary marker (claim `body_key` + `severity_score` + injury type/part), additional injuries list (with row `id` + `version`), ICD-10 + description, prognosis, ordered treatment-plan steps, contraindications, and server-derived marker colors band (`high | med | low`) so the SPA never re-derives severity bands (AD-10)
  - [x] `POST /api/claims/{claimBusinessId}/injuries` and `DELETE /api/claims/{claimBusinessId}/injuries/{id}` (+ severity via the PATCH route) — all behind AD-7 scope + handler role-gate; 409 problem+json with fresh entity on CAS mismatch (2.3 contract); regenerate the OpenAPI client
- [x] Task 4: `BodyMap` typed React component — SVG port (AC: 1)
  - [x] Port the prototype's silhouette as a typed component in `web/src/features/claim-detail/`: 130×310 viewBox figure (ellipses/rects per lines 1476–1494) and the **hotspot coordinate dictionary** for the 11 body keys (`head {65,28,18}`, `shoulder_right {98,72,12}`, `shoulder_left {32,72,12}`, `torso {65,100,16}`, `hand_right {116,148,9}`, `hand_left {14,148,9}`, `tibia_left {47,240,11}`, `tibia_right {83,240,11}`, `ears→head`, `forearm_right {110,115,10}`, `lumbar {65,158,13}`; unknown key falls back to torso) [Source: docs/Workers_Comp_Prototype.html line 1463]
  - [x] Markers: three concentric circles (r+10 @ .12, r+5 @ .22, r @ .88 with white stroke) + crosshair lines, colored by the server-provided severity band (er/wn/ok tokens); primary marker adds the pulsing SMIL/CSS animation ring (expanding r, fading opacity, 1.8s loop) [Source: docs/Workers_Comp_Prototype.html lines 1466–1475]
  - [x] Props are data-only (markers array from the API payload); no business logic in the component
- [x] Task 5: Injury tab layout + cards (AC: 1, 3)
  - [x] Replace 2.2's Injury Diagram empty state: left column — BodyMap + body-part selector (wired to the PATCH command) + ICD code caption; right column — cards: Severity score (editable 0–100 number input + severity bar + band labels Mild/Moderate/Severe + risk arrow), ICD-10 diagnosis (code + description), Prognosis (MMI estimate / RTW outlook / Impairment / Litigation risk), Treatment plan (numbered steps from `treatment_plan_step`), ⚠ Restrictions (contraindications, warning-token styling) [Source: docs/Workers_Comp_Prototype.html lines 1497–1529]
  - [x] "+" popover: injuries-on-this-claim summary list (primary tagged PRIMARY, secondaries with ✕ remove), divider, add-form (body-part select, injury-type text, severity number defaulting 40) [Source: docs/Workers_Comp_Prototype.html lines 1501–1515]
  - [x] Mutations per AD-9: optimistic update only for the user-entered scalar (severity number, injury-type text); marker colors/bands/risk wait for the server; on success invalidate `claimDetail`, queue keys, **and the top-bar stat-tile keys** (High Risk count can change — AC 3's "stat tiles" clause); 409 → rollback + inline fresh state (2.3 pattern)
  - [x] Inline validation before submit and on 422: severity outside 0–100 or missing/empty injury type blocks save with an explanation at the field; no native dialogs (NFR-3)
- [x] Task 6: Tests + E2E spec (AC: all)
  - [x] Unit (server): add/remove/severity commands — validation bounds (0, 100, −1, 101, non-int), CAS mismatch 409, audit + timeline same-transaction, severity band/risk derivation registry usage; property test (Hypothesis): for any severity 0–100 the derived band/risk matches the JDM thresholds and never diverges between queue and detail payload assembly
  - [x] Vitest: BodyMap renders a marker per injury with correct hotspot and band color, primary pulses, fallback hotspot for unknown key; popover validation messages; severity-bar width
  - [x] `e2e/stories/2-4-interactive-injury-diagram.spec.ts` tagged `@story:2-4 @epic:2`; `@smoke` happy path: open Injury Diagram on a seeded claim → silhouette + primary marker + treatment-plan steps render → add a secondary injury via popover → marker appears → remove it via ✕ → gone after reload; additional tests: severity edit updates gauge + queue card risk dot; out-of-range severity shows inline error and saves nothing

## Dev Notes

### What this story is — and is not

This story delivers the Injury Diagram tab: the ported body-silhouette SVG as a typed React component, multi-injury capture with audited add/remove, and severity/body-part editing that recomputes band + risk server-side. It **reuses** 2.3's command/CAS/audit pattern and `InlineEditField` styling — do not invent a second write idiom. It does **not** recompute benefit or reserve (Epic 3 — the AC's recomputation clause here is severity band + risk only), does not touch documents/photos (2.5/2.6), and adds no copilot behavior. The prototype's `injHTML`/`addInjury` JS is behavior/coordinate reference only — never code. Cause editing lives on the Overview card (2.3); this tab's title shows cause read-only or via the shared component — keep one write path.

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### Architecture compliance (binding ADs for this story)

- **AD-1:** severity band, risk, marker colors — all server-computed; the component renders payload data.
- **AD-4:** every add/remove/edit is a CAS-guarded audited command with same-transaction audit events; removals audit the removed row in `before`.
- **AD-7:** all three endpoints scope-enforced in the repository; handler role-gated for writes.
- **AD-8:** severity-band thresholds (70/40) and the default new-injury severity (40) are JDM parameters; the banding logic is the registered Python derivation.
- **AD-9:** optimistic scalars only; derived values wait; 409 rollback + inline fresh state; invalidation spans claimDetail + queue + stat-tile keys.
- **AD-10:** severity band and risk have exactly one computing function each — gauge (2.2), queue dot (2.1), and stat tiles (1.4) all refresh from the same derivations after this story's edits.
- **AD-12:** `additional_injury`, `treatment_plan_step`, and the claim's severity fields are owned by `services/claims`; timeline events ride its commands.
- **AD-15:** story spec is the done-gate (see Testing requirements).

### Data notes

- Creates: `additional_injury` (mutable, versioned; **seeded empty** — see Task 1 note), `treatment_plan_step` (seeded from prototype `treatmentPlan` arrays; effectively static reference rows this epic — no runtime writer until a later epic needs one).
- Uses: `claim` (severity_score, body_key, prognosis/contraindication fields), `audit_event`, `timeline_event`.
- The 11-entry body-key enum + label mapping is shared with 2.3's body-part select — single source (reference constant or table) consumed by both the server enum and the SVG hotspot dictionary.
- Write-owner (AD-12): everything here → `services/claims`.

### UX notes

- UX-DR6 governs: animated severity-colored markers, pulsing primary, add-injury popover, removable secondary markers, ported SVG assets.
- UX-DR12 + Story 1.1 ruling: prototype palette is **LIGHT and canonical** (epics.md "dark console aesthetic" is a documented discrepancy); marker/band colors ride the er/wn/ok tokens; silhouette fills keep the prototype's neutral greys (#D8DFE6/#D0D8E0/#C8D1D9 with #B0BAC4 strokes) or token equivalents.
- UX-DR11/NFR-3: inline validation only — the prototype's silent-focus rejection on empty injury type becomes an explicit inline message; no `alert()`.
- Popover is non-modal (shadcn/ui Popover), anchored to the "+" button top-right of the silhouette panel, as in the prototype.

### Testing requirements

- Unit/property tests the ACs demand: validation bounds, CAS + audit atomicity, derivation-registry recompute, Hypothesis property over the 0–100 severity domain.
- Vitest: marker rendering/pulse/fallback, validation UX.
- E2E (AD-15): `e2e/stories/2-4-interactive-injury-diagram.spec.ts` tagged `@story:2-4 @epic:2`, exactly one `@smoke` happy path, per-spec DB reset; covers add/remove round-trip, severity-edit propagation to gauge and queue, and the inline-validation path. **The story cannot move to `review`/`done` until this spec passes.**

### Project Structure Notes

- Server: migration in `server/data/` versions; commands in `server/services/claims/`; band thresholds JDM document in `server/rules/` (or extend the existing derivation-parameters document from 2.1 — one rule element, one tier).
- Web: `web/src/features/claim-detail/injury/` — `BodyMap.tsx` (SVG + hotspots), `InjuryTab.tsx`, `SeverityCard.tsx`, `AddInjuryPopover.tsx`; reuse `InlineEditField` from 2.3; keys in `web/src/api/queryKeys`.
- SMIL `<animate>` works in React via lowercase attribute pass-through; if it fights the toolchain, an equivalent CSS keyframe pulse is acceptable — the *visual* contract (expanding, fading ring on the primary) is what's binding.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 2.4]
- AD-4 / AD-8 / AD-9 / AD-10 / AD-12 / AD-15: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- "The SVG body map ports as-is — coordinate dictionary and severity-colored markers become a typed React component": [Source: docs/Architecture-LINEWORKER.md#6. Frontend architecture]
- Capability map row "Body map & injuries": [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- ERD (`additional_injury`, `treatment_plan_step`): [Source: ARCHITECTURE-SPINE.md#Core entity ERD]
- Readiness fix assigning `treatment_plan_step` creation here: [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-09.md#Defects Found & Remediated item 2]
- Prototype design/coordinate contract: [Source: docs/Workers_Comp_Prototype.html — hotspots 1463, markers 1466–1475, silhouette 1476–1494, popover 1501–1515, cards 1517–1529, updateSevScore 682, addInjury/removeInjury 693–713, BODY_PART_OPTIONS 648]
- Design-token light-palette ruling: [Source: _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design tokens]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-story` workflow.

### Debug Log References

- **`alembic_version.version_num` is `varchar(32)`, and `0016_injury_capture_rule_document` is 33 characters.** The whole chain aborted with `StringDataRightTruncation` — a message that names neither the revision nor the column, and that arrives *after* fifteen migrations have already run. Renamed to `0016_injury_capture_rules`. Worth recording because nothing in the project checks revision-id length and the failure reads as a data error rather than a naming one.
- **An explicitly named `UniqueConstraint` is taken verbatim; the naming convention only fills in unnamed ones.** `name="uq_claim_step"` on the model and `op.f("uq_treatment_plan_step_uq_claim_step")` in the migration are two different names for one constraint, and `alembic check` reported it as dropped-and-re-added on every run. The model's constraint is unnamed now, so `NAMING_CONVENTION` decides both ends. `CheckConstraint` is the opposite case — its convention *does* interpolate `%(constraint_name)s`, so naming it is correct there.
- **`sa.Enum(..., create_type=False)` is a PostgreSQL-dialect keyword, not a generic one.** The first draft pre-created the type and then asked `create_table` not to; 0010's pattern (let the column create it, drop it by hand in `downgrade`) is the one that works with `sa.Enum`.
- **`INSERT … SELECT` needs typed literals.** The add command's guard rides inside the statement, so the three values are `sa.literal(...)` in the SELECT list — and an untyped one reaches asyncpg with nothing to encode it as, `body_key` being a native enum. Each literal takes its type from the column it lands in, which also means a column that changes type needs no second edit.
- **`test_no_module_outside_the_registry_hardcodes_the_band` fired on a docstring.** The sentence explaining why the prototype's marker cut-offs are *not* ported named the deployed thresholds, and the guard greps for them in every file outside the registry. Reworded rather than allowlisted: the guard is deliberately blunt, and a comment naming a threshold is one edit away from being code that uses it.
- **`noDerivation.test.ts` fired on `SEVERITY_MIN = 0` / `SEVERITY_MAX = 100` in the severity card**, and it was right to. The fix was not to rename the constants — that is the guard training the code — but to stop the browser having them: the bounds are now on the case file (`injury.severityMin` / `severityMax`), served like the eleven body keys, so the number input, the pre-flight refusal and its message all quote what the server said.
- **`openapi-fetch` passes a `Request` object as fetch's only argument**, so `vi.fn().mock.calls[i][1]` is `undefined` and the body is a stream. `InjuryWrites.test.tsx` records requests in front of the stub and clones them, which is what makes "which version actually travelled" assertable — the thing every compare-and-swap in this story depends on.
- **A Radix popover is `role="dialog"` whether or not it is modal.** The first draft of the NFR-3 assertion checked for the absence of that role and failed against a correct component. What NFR-3 is about is what *blocks* the handler, so the assertion is now that the popover is not modal and that no `alertdialog` exists anywhere.
- **The treatment overview renders the last six timeline events**, so the @smoke path counts events on the **investigation** claim instead — whose variant renders the whole log. Counting the slice would have made "the command emitted a timeline event" pass or fail depending on how many events the claim already had.

### Completion Notes List

- **AC 1 — the diagram is a payload, and the SVG decides nothing.** `injury.markers[]` arrives with each marker's `band` already computed by the one registered `risk` derivation, so `BodyMap` picks a token from a key and holds no threshold. That is the story's central deviation from the prototype: `injHTML` re-derives a colour per marker from a cut-off pair written into the drawing function, which is *not* the pair the rest of the console bands with — so on the prototype some scores draw one colour on the diagram and another on the card beside it. The registered derivation wins, and the prototype's second pair appears in no rule document. The primary marker's band is the **same value** the header's gauge got (passed in, not recomputed), which is what makes "the gauge and the diagram cannot disagree" a property rather than a coincidence.
- **The hotspot dictionary and the silhouette are geometry, ported verbatim.** Eleven entries on a 130×310 figure, `ears` sharing the head's circle exactly as the prototype does (the figure has no ear geometry, and inventing one would be this port designing rather than porting). The `torso` fallback for an unknown key is unreachable through the UI — the select is built from the server's vocabulary and the command refuses anything outside it — and is kept because the alternative when a key *does* go missing is `undefined.cx`, which renders no marker at all and reads as "this claim has no injury". It has a fixture of its own for the reason Story 2.2's code review gave about the settled banner's date: a branch nothing can reach is a branch nothing tests.
- **AC 1 — `treatment_plan_step` and the four prognosis columns came from data already in the repository.** `seed_data.json`'s `deferred` block has held both since Story 1.2; no new extractor was written, because re-running one to move committed content would put the prototype HTML in the migration chain's dependencies for no gain. 500 steps, five per claim, all 100 claims, numbered from the array's order — and `step_no` is a real column with a uniqueness constraint, so unlike `timeline_event` the ordering does not depend on insertion order.
- **The prognosis columns arrive nullable in 0014 and become NOT NULL in 0015, after the data lands.** The alternative — NOT NULL with a `''` server default — would leave a hundred claims briefly holding an empty string that reads as a real, blank prognosis rather than an absent one, and would put the honesty of the column in a default nobody reads. This also fills a gap Story 2.2 recorded: it omitted the MMI row from the treatment card because nothing persisted it, and "a row that always reads —' is furniture, not honesty".
- **AC 2 — two different things are compare-and-swapped, and that is the design rather than an inconsistency.** `add` guards on the **claim's** version, because the case file the handler was reading is what they were reasoning about; `remove` guards on the **injury row's**, because that is what is being destroyed. Guarding the delete with the claim's version would refuse a handler's ✕ because somebody had corrected an unrelated ICD-10 code, while still permitting the one deletion that actually matters — removing a row another handler had just re-classified. The case file publishes each secondary's own `id` and `version` for exactly this.
- **Adding does not bump `claim.version`, and the guard is still a real compare-and-swap.** No column of `claim` changed, so the client's cached version stays a valid basis for its next edit — and two handlers can record two findings on one claim without either being refused, which is the correct outcome for an append. The guard is sound because the version predicate rides *inside* the `INSERT … SELECT` rather than in a Python `if` before it; a forced-interleaving test bumps the version from a second connection between the command's read and its insert, and every sequential test would pass against a read-then-write implementation.
- **AC 2 — the removal audits the row it destroyed, through `RETURNING`.** `before` is the whole row and `after` is null. A whole-row diff is right here for the reason it is *wrong* in `update_claim_fields`: there, a one-field edit would drag every PHI column of the claim into a table with a seven-year retention floor; here the row **is** the change. The columns come back from the statement rather than from a prior read, because a value read before the delete is a value another writer could have changed in between — the log would then describe a row that never existed in that state.
- **The timeline sentence never carries text a caller typed.** `injury_type` is free text from a handler (and, under AD-13, from an agent tool whose arguments are untrusted content). The event names the **region**, whose label comes from the closed server-owned vocabulary, so the log cannot be made to hold arbitrary strings by whoever calls the command. The audit row carries the whole row, where it belongs. Asserted both ways: the event says "Additional injury recorded (Left Shoulder)" and does not contain "Rotator Cuff Tear".
- **AC 3 — `update_claim_severity` is a command of its own rather than an eighth entry on 2.3's patch whitelist**, which the story permitted either way. That whitelist is machinery for *text*: `require_text` trims and length-caps, `_Change` carries `str` before/after, the timeline sentence is built from `FIELD_LABELS`, and a Hypothesis property ranges over every member of it. Threading one integer through all of that would have made five things conditional to save twenty lines — and the audit diff would have recorded `"78"` for a numeric column. What *is* shared is everything that matters: the refusal ladder in the same order, the same repository CAS function, the same `StaleClaim` carrying fresh state, and the same four-status mapping.
- **The four routes now share one refusal mapping.** `_answer` in the claims router runs a command and maps `EditNotPermitted`/`ClaimNotVisible`/`InvalidPatch`/`StaleClaim` onto 403/404/422/409 — written once because the *sameness* is the security property: a caller comparing one route's refusal with another's must not be able to learn from the difference. Story 2.3's PATCH was re-pointed through it rather than left as a fifth copy. Asserted directly: a supervisor gets one status and one `type` from all three new routes for their own claim, somebody else's, and one that does not exist.
- **"No such injury" is the claim's own 404, deliberately.** A distinct refusal would confirm that the *claim* exists, which is precisely the enumeration oracle `select_claim_detail`'s single-answer rule closes. The same wording covers "removed by somebody else a moment ago", so a caller cannot use the difference to learn what another handler did.
- **AC 3 — nothing in this story tells the gauge, the queue or the tiles to move.** The severity score is a column; `risk` is a band of it; the header gauge, the primary marker, the queue card's dot and the top bar's High Risk count all read the same derivation (AD-10). Editing the score to 100 and then to 0 moves all of them, asserted across two endpoints in one test so a payload that cached a band would fail. The client's part is invalidation, and the **stat-tile key is the one no earlier mutation touched** — `highRisk` counts claims in the high band across the caller's whole book, so a score crossing the boundary changes a number two panes away that nothing in the SPA could compute.
- **AC 3 — the body-part select on this tab is Story 2.3's command, not a second one** (AD-12: one write path). It is an `InlineEditField` over `PATCH /claims/{id}`, with the same optimistic scalar, the same 409 handling and the same inline refusal as the investigation overview's select. This is also the tab where the two body-part vocabularies are visible together, which is the thing 2.3 flagged and named this story as the place it would become decidable; it has not been decided, and it is in `deferred-work.md` as a product question rather than closed by a dev-time guess.
- **AC 4 — nothing is clamped.** The prototype's `updateSevScore` does `Math.max(0, Math.min(100, n))`, which silently turns a typo of `780` into a maximum-severity claim — a value nobody entered, in the column that decides the claim's risk band and its position in the queue. The command refuses instead, and the browser refuses first for the two mistakes it can see (a non-integer, and a value outside the served bounds) so the common cases cost no round trip. The prototype's other refusal, a silent `typeEl.focus()` on an empty injury type, is a sentence now.
- **One named deviation from Task 5's optimistic-update clause.** The severity commit is **not** optimistic. AD-9 permits an optimistic update *for* the user-entered scalar; it does not require one, and here every visible consequence of the number is derived — the colour of the digits, the bar's fill, the gauge, the primary marker. Writing the digits in while their colour waited for the server would render a state that exists nowhere (a 78 in green), which is worse than the ~100 ms of the input holding its previous value; the field is disabled while the commit is in flight, so there is no ambiguity about what is being sent. The add and remove mutations are not optimistic either, for a stronger reason: a new marker needs an `id`, a `version` and a band, none of which the browser can invent (the prototype fabricates an id from `Date.now()`).
- **The severity bounds are served, not restated in the SPA.** `injury.severityMin` / `severityMax` ride on the case file beside `defaultSeverityScore`, which is a JDM parameter (`injury_capture` v1 — a document of its own for the reason `intake_required_documents` is one: `services/claims` owns the add command, and `derivation_thresholds` is the single argument every *derivation* is built from). The bounds are not a JDM parameter — moving them would take a migration, a CHECK change and a re-seed — but they are still the server's to state, and `noDerivation.test.ts` is what made that concrete.
- **`BodyRegion` moved the eleven keys into `data/models/enums.py`, and the labels stayed in `services/claims/reference.py`.** A native enum column needs its members in the data layer (`data/` must not import from `services/`), so the vocabulary lives there and `reference.py` hangs the display labels off it — which keeps the single source the 2.3 Dev Notes asked for and points the dependency the right way. Migration 0014 writes the eleven keys out *literally* rather than importing the enum, for the reason 0013 wrote its recovery map out: a migration is a historical record, and one that read a live constant would silently change what it did when somebody reordered it. Four statements of one list, and a test that compares all four — enum, options, labels, migration — including their order, because the migration's tuple is what decides the enum's label order in PostgreSQL.
- **`claim.body_key` is still `Text` while `additional_injury.body_key` is the native enum.** Scoped deliberately: the task list puts the enum on the table it creates, and converting a column two earlier stories own is a migration with a ripple through the edit command, the case-header contract and the generated client. The vocabulary is single-sourced either way — `edit.py` validates the claim's key against the same eleven members — so what differs is only where the refusal comes from. Recorded in `deferred-work.md`.
- **The DELETE's version travels as `?expectedVersion=`, aliased to camelCase.** A DELETE body is permitted but widely dropped by proxies and generated clients, and a compare-and-swap whose guard can be silently discarded is not a guard. The alias is the first place the camelCase boundary meets a query parameter — the queue's four are single words — and a parameter is no less on the wire than a body field.
- **`POST` answers 201 with the case file and no `Location` header.** A row is created, so 201 is the honest status; there is deliberately no `GET /claims/{id}/injuries/{id}` to point at, because the injuries are part of the case file and a second way to read one would be a second place the diagram's data comes from. `DELETE` answers 200 with the case file rather than 204, for the same reason every other command here does: the caller needs the diagram without the marker.
- **The Injury Diagram tab is on the case file, not on a stage variant.** A settled claim's diagram is as readable as an intake claim's, and putting the block on a variant would have meant four copies or a tab that disappeared when a claim settled. `DetailTabs` lost its first seam entry — which is what the seam list's shape was for — and the panel is rendered only while its tab is selected, so the popover's three mutations and its form state do not stay alive behind a tab nobody is looking at.
- **The layout is the prototype's `.blayout` / `.binfo`**: a `180px 1fr` grid with the cards stacked in a single column, verified by screenshot at 1440×900 against `injHTML`. The first draft used the two-column `CardGrid` and looked fine, which is exactly why it was worth checking — the prognosis card already has two columns *inside* it, and a second level around it is how a dense console stops being readable. One responsive addition the prototype does not specify: below the breakpoint the two panels stack, because the prototype is a desktop console with no rule for it at all.
- **Story 2.2's Injury-Diagram seam assertions were re-pointed, not deleted** (1.5/1.6/2.1/2.3's precedent), in both the vitest suite and the Playwright spec. Each now asserts the stronger version of what it was really guaranteeing: the arrow-key test checks that the *panel* followed the selection (which would otherwise pass while Overview's content stayed on screen), and the seam table checks that a tab is either built or honest about not being — with the built one asserted explicitly beside the four that are not.
- **2.3's "the financials card stays read-only" assertion needed no change and is stronger than it looks.** The severity score became editable on the Injury Diagram tab and stays read-only on the investigation financials card, which is where 2.3 put the assertion after its own code review. One editable surface per column.
- **Tests:** 687 server (76 new — the severity domain including `True` as an `int`, the region vocabulary in its four homes and against the prototype's HTML, the injury-type text rules, four Hypothesis properties over the 0–100 domain, the audit and timeline rows for all three commands, both compare-and-swaps including a forced read/write race, the delete's 404-vs-409 split, the role and scope refusals across three routes, the endpoint contract, the enum and grants and CHECK constraints in the database, and the 500 seeded steps tied back to the seed file). 187 vitest (18 new — a marker per injury at its own hotspot and in its own band, the pulse on the primary only, the torso fallback, the bar's width and colour, the popover's list and its ✕, the served default, three inline refusals, what each mutation actually sends, the stat-tile invalidation, and the 422 and 409 paths). 77 Playwright (7 new, `@story:2-4 @epic:2`, one `@smoke`; the count includes the DB-reset setup project).
- **Verified live.** e2e stack (`compose.e2e.yaml`, :8081) rebuilt from clean: full suite **77/77**, the 11-test `@smoke` set and the 38-test `@epic:2` close-out all green against a freshly reset stack. `alembic check` reports no drift and a `downgrade 0013` → `upgrade head` round trip completes clean. `npm run generate:api` leaves `src/api/schema.d.ts` byte-identical on a second run. ruff + format + mypy clean. No new runtime or dev dependency — the popover is `radix-ui`, which the vendored shadcn components already depend on.

### File List

**New — server**

- `lineworker/server/data/versions/20260812_0014_injury_diagram_tables.py`
- `lineworker/server/data/versions/20260812_0015_seed_injury_diagram.py`
- `lineworker/server/data/versions/20260812_0016_injury_capture_rules.py`
- `lineworker/server/rules/documents/injury_capture.jdm.json`
- `lineworker/server/services/claims/injuries.py`
- `lineworker/server/tests/test_injury_validation.py`
- `lineworker/server/tests/test_injury_commands.py`
- `lineworker/server/tests/test_injury_diagram_migration.py`

**Modified — server**

- `lineworker/server/data/models/enums.py` (`BodyRegion`)
- `lineworker/server/data/models/core.py` (the four prognosis columns; `AdditionalInjury`, `TreatmentPlanStep`)
- `lineworker/server/data/models/__init__.py` (exports)
- `lineworker/server/data/repositories/claims.py` (two child reads, the guarded insert, the compare-and-swapped delete, and the read that tells 404 from 409)
- `lineworker/server/rules/parameters.py` (`InjuryCapture`, `injury_capture_for`, `INJURY_CAPTURE_KEY`)
- `lineworker/server/services/claims/reference.py` (options keyed by `BodyRegion`; `SEVERITY_MIN`/`SEVERITY_MAX`)
- `lineworker/server/services/claims/edit.py` (`update_claim_severity`, `normalise_severity`; `_conflict`→`conflict` and `_require_text`→`require_text`, now that a second module shares them)
- `lineworker/server/services/claims/detail.py` (`InjuryMarker`, `Prognosis`, `TreatmentPlanEntry`, `InjuryDiagram`, `_injury`)
- `lineworker/server/api/routers/claims.py` (the injury response models, the shared `_answer` refusal mapping, and the three write routes)
- `lineworker/server/tests/seed_fixture.py` (`treatment_plan_for`, `prognosis_for`)
- `lineworker/server/tests/test_claim_edit.py` (one docstring reference after the rename)

**New — web / e2e**

- `lineworker/web/src/components/ui/popover.tsx` (vendored shadcn, `dark:` utilities stripped per 1.1)
- `lineworker/web/src/features/claim-detail/injury/BodyMap.tsx`
- `lineworker/web/src/features/claim-detail/injury/InjuryTab.tsx`
- `lineworker/web/src/features/claim-detail/injury/SeverityCard.tsx`
- `lineworker/web/src/features/claim-detail/injury/AddInjuryPopover.tsx`
- `lineworker/web/src/features/claim-detail/injury/InjuryTab.test.tsx`
- `lineworker/web/src/features/claim-detail/injury/InjuryWrites.test.tsx`
- `lineworker/e2e/stories/2-4-interactive-injury-diagram.spec.ts`

**Modified — web / e2e**

- `lineworker/web/src/api/claims.ts` (`useEditSeverity`, `useAddInjury`, `useRemoveInjury`, the shared post-write invalidation, and the diagram types)
- `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/features/claim-detail/InlineEditField.tsx` (`numeric` bounds and `EditableFieldName` — the extension its own docstring anticipated)
- `lineworker/web/src/features/claim-detail/useInlineEdits.ts` (`feedbackFromError`, extracted for the three new mutations)
- `lineworker/web/src/features/claim-detail/labels.ts` (`RISK_TREND`)
- `lineworker/web/src/features/claim-detail/DetailTabs.tsx` (the injury panel; the seam entry removed)
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.tsx` (renders the tab)
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.test.tsx` (2.2's seam assertions re-pointed)
- `lineworker/web/src/features/queue/noDerivation.test.ts` (`band` added to the derived fields; the injury folder named in the scan assertion; two more smell tests)
- `lineworker/web/src/test/api-mock.ts` (`INJURY_DIAGRAM`, `INJURY_UNKNOWN_KEY`, `injury` on the four case-file fixtures)
- `lineworker/e2e/fixtures/seed.ts` (the 2.4 oracles: treatment plan, prognosis, restrictions, the primary marker's band, a score in another band)
- `lineworker/e2e/stories/2-2-case-header-stage-adaptive-overview.spec.ts` (the two Injury-Diagram seam assertions re-pointed)

**Modified — repo**

- `_bmad-output/implementation-artifacts/deferred-work.md` (six items)
- `_bmad-output/implementation-artifacts/2-4-interactive-injury-diagram.md` (this file), `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-08-12: **Second review pass, run from Story 2.6 before Epic 2 sign-off — 4 findings, 4 fixed.** The pass was scoped to regression first (2.5 and 2.6 modified 12 of this story's 39 files) and found **none**: all four fixes from the first pass are intact, and each was re-verified by reverting it and watching its pinning test go red. The four new findings are this story's own. (1) **`injury_capture.jdm.json` was covered by no test** — six rule documents on disk, five in `test_rules_engine.py`'s tuples — so retuning `newInjuryDefaultSeverity` on a database where 0016 had already run would have left the file and the row disagreeing with the whole gate green. Fixed by adding the document *and* by enumerating `rules/documents/` so the next one cannot be missed, which is the failure the hand-maintained tuple actually had. (2) **Migration 0016 seeded `injury_capture` with `effective_from = 2026-08-12`** while 0009, 0012 and 0019 all use `2026-08-11`; because v1 has no predecessor to fall back to, a case-file read before that date raises `RuleDocumentMissing` and **500s the entire case file**, 409 bodies included. Corrected to match, with a new test asserting no seeded document is future-dated (verified to fail against a 2027 date). (3) **`applyOptimisticEdit` echoed `bodyKey` into the header but not into `injury.markers[0]`** — the same `claim.body_key` column, both on screen on this story's own tab — so changing the body part moved the select instantly while the pulsing marker sat on the old hotspot and then jumped: the same defect this story's first pass fixed for the treatment recovery select. (4) **The first pass's "role first, before anything else is examined" overstated the guarantee**: FastAPI validates the request model before the endpoint runs, so an out-of-range `severityScore` is a 422 whatever the caller's role. The Pydantic bound was deliberately **not** removed — it is in the published OpenAPI contract, reaches the generated client, and restates only limits every caller has already downloaded — so the fix is an accurate statement of the real rule ("once a request is a well-formed instance of the published contract, the role is checked before anything about the claim or the patch is examined") plus tests pinning it on all four write routes, where only one was covered. Gate re-run green: ruff + format + mypy clean, **pytest 788**, **vitest 228**, **Playwright 86/86** plus the 13-test `@smoke` set against a rebuilt e2e stack.

- 2026-08-12: Story 2.4 implemented end to end — `additional_injury` (a native `body_region` enum, a 0–100 CHECK, versioned) and `treatment_plan_step` created, the four prognosis columns added to `claim` and 500 plan steps seeded from data Story 1.2 had already extracted, and `injury_capture` v1 added to the rules tier. Three AD-4 commands joined `services/claims` — `add_additional_injury` (guarded inside an `INSERT … SELECT` on the claim's version), `remove_additional_injury` (compare-and-swapped on the injury row, auditing it through `RETURNING`) and `update_claim_severity` — behind `POST`/`DELETE /claims/{id}/injuries` and `PATCH /claims/{id}/severity`, all four write routes now sharing one refusal mapping. The case file gained an `injury` block whose markers arrive already banded by the one registered `risk` derivation, and the Injury Diagram tab replaced Story 2.2's seam with the ported silhouette, its eleven hotspots, a pulsing primary marker, five cards and a non-modal add/remove popover. Full gate green: ruff + format + mypy clean, pytest 687, vitest 187, Playwright 77/77 plus the 11-test `@smoke` set and the 38-test `@epic:2` close-out against a rebuilt e2e stack. Status → review.

- 2026-08-12: Addressed code review findings — **4 findings, 4 fixed, 0 deferred, 0 dismissed**. Two were this story's: four independent `isPending` flags on one tab let two commits overlap and 409 the handler about their own edit (now one `useClaimWriteInFlight` count across all four mutations), and the shared `_answer` refusal mapping inherited an ordering inversion in which a malformed patch got a non-handler a 422 before the role check ran (the explicit-null refusal moved into `normalise`, which also closes it for AD-13 agent tools). Two were Story 2.3's, in code this story touched: a single `attempted` reused across the ICD pair rendered the code inside the description input on a 422, and `applyOptimisticEdit` never echoed the treatment card's recovery window, so that select appeared to reject the handler's choice. Each fix is pinned by a test verified to fail when the fix is reverted. Gate re-run green: ruff + format + mypy clean, **pytest 694**, **vitest 191**, **Playwright 77/77** plus the 11-test `@smoke` set and the 38-test `@epic:2` close-out against a rebuilt e2e stack.

### Code Review Pass — 2026-08-12

Four findings, **all four real and all four fixed**. Each is now pinned by a
test that fails when the fix is reverted (verified by reverting each one and
watching the new tests go red).

**The two that were mine.** (1) The Injury Diagram tab mounts four mutation
hooks — the field patch, the severity score, and the add and remove of a
secondary injury — each with its own `isPending`. Story 2.3 made
"every input is disabled while a commit is in flight" true *within* one hook
and wrote it down twice; 2.4 put three more hooks on the same screen without
unifying them, so committing a severity score and then changing the body part
before it settled sent the same `expectedVersion` twice and told the handler
somebody else had changed the claim — about their own edit. The four
mutations now share a `mutationKey`, and `useClaimWriteInFlight` counts them,
so the guarantee holds across the surface rather than inside each hook.
(2) `_answer` inherited a refusal-order inversion and would have handed it to
every Epic 3 command: `ClaimFieldPatch.edited_fields()` refused an explicit
`null` while the router was still *building* the call, so it fired before the
command's role check and a supervisor sending `{"cause": null}` was told the
patch was malformed rather than that their role cannot edit. The refusal
moved into `normalise`, after the whitelist check (which is what makes naming
the offending keys safe) — and it now also covers the AD-13 agent tools,
which never construct a Pydantic model at all.

**The two inherited from 2.3, in code this story touched.** (3) `attempted`
was read from `fields[0]` and the resulting feedback applied to *every* field
of the commit. The only two-field commit is the ICD pair, whose key order is
always `icd, icdDesc` — so clearing the description produced a 422 that
rendered the ICD **code** in the description input, ready to be committed as
the description if the handler typed on top of it; the mirror case blanked
the description instead. Feedback is computed per field now. The existing
tests only ever edited `icd`, which is why it survived 2.3's own review pass.
(4) `applyOptimisticEdit` echoed only the investigation variant, but 2.3's
code review had made the *treatment* card's recovery window editable too — so
that select re-rendered its old option the moment a handler picked a new one,
appearing to reject the choice and then jumping. The derived figures beside
it (`expectedDays`, the phase) still wait for the server, which is the half
AD-9 actually forbids guessing.

**Gate after the pass:** ruff + format + mypy clean, **pytest 694**,
**vitest 191**, **Playwright 77/77** plus the 11-test `@smoke` set and the
38-test `@epic:2` close-out, against an e2e stack rebuilt from clean.
`alembic check` reports no drift. `npm run generate:api` is byte-stable.

**Files changed by this pass** — `services/claims/edit.py` (the null refusal,
in `normalise`), `api/routers/claims.py` (`edited_fields` passes nulls
through; `_answer`'s docstring corrected), `tests/test_claim_edit.py` (+2),
`tests/test_claim_edit_validation.py` (+5); `web/src/api/queryKeys.ts`
(`claims.writes`), `web/src/api/claims.ts` (the shared mutation key,
`useClaimWriteInFlight`, `OPTIMISTIC_TREATMENT_FIELDS`),
`web/src/features/claim-detail/useInlineEdits.ts` (per-field feedback, shared
busy flag), `web/src/features/claim-detail/injury/SeverityCard.tsx` and
`AddInjuryPopover.tsx` (shared busy flag),
`web/src/features/claim-detail/InlineEdit.test.tsx` (+3),
`web/src/features/claim-detail/injury/InjuryWrites.test.tsx` (+1).
