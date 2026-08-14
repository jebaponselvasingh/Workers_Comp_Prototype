---
baseline_commit: 98aa8c2dffcb962407574620a599c4263db7f67d
---

# Story 3.2: Reserve Adequacy Check

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want an automatic reserve adequacy verdict,
so that under- and over-reserved claims surface before they become problems.

## Acceptance Criteria

1. **Given** a claim, **when** the Reserve Check computes, **then** `services/financials` compares projected remaining exposure (unpaid indemnity + unpaid medical) to the current reserve and classifies Light / Adequate / Heavy with a rationale (FR-H-4, FR-DET-3), computed in exactly one place (AD-2).
2. **Given** the classification bands, **when** resolved, **then** they are parameters in a ZEN JDM document, versioned with effective dates (AD-8).
3. **Given** the verdict, **when** displayed, **then** it renders identically in the Bills financial summary and the treatment-stage Overview card from the same server value (AD-10).
4. **And** unit tests cover all three bands and boundary values.

## Tasks / Subtasks

- [x] Task 1: Reserve-check function in `services/financials` (AC: 1)
  - [x] Pure typed core `classify_reserve(reserve_cents, remaining_indemnity_cents, remaining_medical_cents, bands) -> ReserveVerdict` returning `{verdict, ratio, projected_remaining_cents, rationale}` — the single computation site (AD-2)
  - [x] Classification per the prototype reference (`reserveCheck`, lines 1407–1418): `ratio = projected_remaining / reserve`; `ratio > light_ratio` → `light` (under-reserved, error semantics); `ratio < heavy_ratio` → `heavy` (over-reserved, warn semantics); else `adequate` (ok semantics)
  - [x] Edge cases codified from the prototype: `reserve == 0` → ratio treated as 2 when remaining > 0 (light) else 1 (adequate); settled stage → distinct `closed_final` verdict ("Claim settled and closed. No further reserve exposure."), no band math
  - [x] Deterministic rationale strings per verdict (light: exposure exceeds reserve, recommend re-evaluating upward; heavy: reserve comfortably exceeds exposure, consider reallocating surplus; adequate: well aligned) with both figures embedded — service output, never LLM (AD-2)
  - [x] snake_case enum `reserve_verdict: light|adequate|heavy|closed_final`; UI owns labels ("Reserve Light", …) — plus a fifth member, `indeterminate`, for a claim whose exposure cannot be totalled because its bills are not on file. The prototype has no equivalent because it has no data that can be absent; see the Change Log
- [x] Task 2: Exposure inputs (AC: 1)
  - [x] Remaining indemnity = `total_scheduled_cents − paid_so_far_cents` (floored at 0) from the payment-schedule projection in `services/financials` — implement the pure projection math here if 3.3 has not landed it yet (weeks from recovery window, weekly from Story 3.1's `compute_benefit`, statuses by stage/reference date); 3.3 persists rows through this same function — one generator, never two (AD-2)
  - [x] Remaining medical = sum of `bill` rows with status ≠ `paid`, read via the scope-enforced repository. Sequencing note: the `bill` table is created/seeded by Story 3.3 — wire the query now; if 3.2 is executed before 3.3 it legally returns 0 and verdicts complete when 3.3's seed lands. Unit tests inject fixtures directly, so no test depends on the seed — **partial, and stated as such**: the table does not exist, so the query cannot be written; `unpaid_medical_cents` is the one named function 3.3 fills in, with the query it will contain written into its docstring. It returns `None` ("not on file") rather than the story's `0` ("no unpaid bills"), and the verdicts an unknown non-negative term could flip are withheld rather than guessed. See the Completion Notes and the Change Log.
  - [x] Assemble in a `reserve_check(claim_id, caller_ctx)` service query; expose on the claim financial read model so both consuming surfaces read one value (AC 3) — assembled as `reserve_check_for_claim(db, claim, benefit, as_of)`; no `caller_ctx` parameter, for the reason `benefit_for_claim` has none (see the Debug Log)
- [x] Task 3: JDM bands document (AC: 2)
  - [x] ZEN JDM document `reserve_bands` with parameters `light_ratio: 1.15`, `heavy_ratio: 0.60` (prototype-seeded values), DB-versioned with effective dates, loaded via the rules path established in Story 3.1 (AD-8) — declared as `lightRatioBp: 11500` / `heavyRatioBp: 6000`; the story's values, in the unit that makes the boundary exact (see the Debug Log)
  - [x] Formula (ratio arithmetic, clamping, edge cases) stays in typed Python; only the band thresholds live in JDM — one tier per rule element
- [x] Task 4: Treatment-stage Overview rendering (AC: 3)
  - [x] Wire the verdict into Story 2.2's treatment-stage paid-vs-reserve card: verdict chip + rationale ratbox, colored by ok/warn/error tokens (adequate/heavy/light respectively — note the prototype maps light→error, heavy→warn)
  - [x] The SPA renders the server verdict verbatim — no client-side ratio math (AD-1/AD-9); query key shared via the `queryKeys` module so 3.3's Bills summary consumes the identical cached value
- [x] Task 5: Tests (AC: 4)
  - [x] Unit: all three bands; boundary values — ratio exactly `1.15` → adequate (strict `>`), exactly `0.60` → adequate (strict `<`), just above/below each; `reserve == 0` with and without remaining exposure; settled → `closed_final`; floor-at-0 remaining indemnity
  - [x] Property test (Hypothesis, per the NFR-7 pattern from 3.1): ∀ non-negative inputs, exactly one verdict is returned and `projected_remaining_cents == remaining_indemnity + remaining_medical`
  - [x] JDM: band values resolve from the effective-dated document, not from constants in code (assert by loading a variant document in a test)
  - [x] E2E `e2e/stories/3-2-reserve-adequacy-check.spec.ts` `@story:3-2 @epic:3`: `@smoke` happy path — login as handler, open a treatment-stage claim, verdict chip + rationale visible in Overview with one of the three labels

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story is the reserve-adequacy verdict: the classification function, its JDM bands, the exposure assembly, and the treatment-Overview rendering. It builds on 3.1 (`compute_benefit` supplies the weekly figure inside the indemnity projection) and Epic 2 (Story 2.2's treatment Overview card is the render target — extend it, don't rebuild it).

It is **not**: the Bills & Payments tab (3.3 renders this same verdict in its financial summary — AC 3's "identical rendering" is completed by 3.3 consuming this story's server value and shared query key); not schedule/bill persistence (3.3); not payment transitions (3.4). No new tables are created here — `payment_schedule_week`, `bill`, `expense` are all 3.3's migrations. The prototype is a behavior reference only.

**Sequencing seam (deliberate):** projected remaining exposure needs the schedule projection and bill data whose persistence/seed land in 3.3. The projection math is pure and belongs to `services/financials` either way — implement it here if absent, and 3.3 must persist through the same function (AD-2 forbids a second generator). The bill query legitimately returns 0 until 3.3 seeds; verdicts are fully populated once 3.3 lands. This is the honest reading of the epic ordering, not a defect.

### Architecture compliance (binding ADs for this story)

- **AD-1:** the verdict and ratio are computed server-side only; the SPA renders strings and enum values.
- **AD-2:** reserve check exists exactly once in `services/financials`; the rationale is deterministic service text, and Epic 6's "reserve adequacy review" insight may quote it but never re-derive it.
- **AD-7:** the `reserve_check` query goes through scope-enforced repositories with the caller context — a handler cannot compute a verdict for a claim outside their book.
- **AD-8:** bands (1.15 / 0.60) are JDM parameters, effective-dated; ratio math is typed Python. One tier per element.
- **AD-10:** one computing function; the Bills summary (3.3) and treatment Overview must render from the same server value — never two computations, never client math.
- **Conventions:** money integer cents everywhere including across the ZEN boundary (ratios are dimensionless — fine); enums snake_case, UI labels.

### Data notes

- **Creates:** no tables. **Uses:** `claim` (reserve_cents, stage, recovery, disability, severity — via 1.2 seed), Story 3.1's `state_rate_schedule`/benefit, and (once 3.3 lands) `bill` rows read via repository. JDM `reserve_bands` document stored in the DB-versioned rules store.
- Write-owner impact: none — this story is read/compute only; no audit events are emitted by a pure read (AD-4 binds mutations).

### UX notes

- UX-DR5 (Overview tab, treatment variant). Verdict presentation: chip in the card + `ratbox` rationale (prototype lines 1442–1451 show the Bills-side twin for visual consistency; 3.3 implements that side).
- Color semantics ride Epic 1's tokens: light→error, adequate→ok, heavy→warn, closed_final→muted. Design-token ruling: the prototype's LIGHT palette is canonical (epics' "dark console aesthetic" is a documented discrepancy).
- Loading/error states on the financial read model query (NFR-3).

### Testing requirements (this story's definition of done)

- Unit tests on all three bands + boundaries + edge cases (AC 4) and the JDM-resolution test.
- Hypothesis property test on classification totality.
- E2E: `e2e/stories/3-2-reserve-adequacy-check.spec.ts` tagged `@story:3-2 @epic:3`, one `@smoke` happy path, against the freshly reset e2e stack. Story cannot move to `review`/`done` until it passes (AD-15).

### Project Structure Notes

- Code lands in `lineworker/server/services/financials/` (classification + projection), `server/rules/` (JDM `reserve_bands`), `web/src/features/claim-detail/` (Overview card wiring), shared query key in `web/src/api/queryKeys`.
- Register the verdict read model under one query key now; 3.3 reuses it — that is how AC 3's "identical" is enforced structurally rather than by convention.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 3.2]
- AD-2 (reserve check exists once), AD-8, AD-10: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#Invariants & Rules]
- Capability map row (reserve check → `services/financials`): [Source: ARCHITECTURE-SPINE.md#Capability → Architecture Map]
- 115%/60% adequacy bands named in the narrative: [Source: docs/Architecture-LINEWORKER.md#3.2 The three logic layers]
- Behavioral reference: `reserveCheck` (lines 1407–1418), treatment-overview usage (line 1353), Bills-summary twin (1437–1451): [Source: docs/Workers_Comp_Prototype.html]
- FR-H-4 / FR-DET-3: [Source: _bmad-output/planning-artifacts/epics.md#Requirements Inventory]

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Claude Opus 5, 1M context), via the `bmad-dev-story` workflow.

### Debug Log References

- **The story's bands are decimals and its own AC 4 asks for an exact boundary, and those two things cannot both be true in floats.** `light_ratio: 1.15` compared against `projected / reserve` makes "exactly 1.15" a fact about which pair of cent figures produced the quotient, not about the claim — the same failure Story 3.1 recorded for `default_comp_rate_pct: 66.67`, one operator over. The document declares `lightRatioBp: 11500` and `heavyRatioBp: 6000`, and `classify_reserve` cross-multiplies: `projected × 10 000 > light_ratio_bp × reserve`. No division, no rounding, and `test_the_boundary_holds_on_a_reserve_that_is_not_a_round_number` picks a reserve ($7,777.77) whose 115% is not a whole cent so the two neighbouring cents have to fall either side of a boundary no exposure can sit on. The *values* are the story's; only the unit differs, and the reasoning is in the document's own description field.
- **The ratio is published and does not decide anything, and keeping those two apart needed a property.** `ratio_bp` rounds half up for display while the verdict cross-multiplies exactly, so the two are computed differently on purpose — which means a claim can round *onto* a boundary the verdict did not cross. `test_the_verdict_agrees_with_the_ratio_it_publishes` allows exactly that and forbids the far side, so the rounding can never surface as a payload contradicting itself. The card renders no ratio at all, which is the prototype's choice too; a test pins that a `null` ratio on a settled claim cannot become "NaN%".
- **`reserve_check_for_claim` takes no `CallerContext`, and the story's Task 2 asks for one.** Everything this service reads is either handed to it or unscoped rule data, so a `ctx` parameter would be accepted and never read — which is exactly what `services/claims/detail.py::_overview` argues against in the code review that removed its own ("a context nobody reads is decoration, and the next author would take the scoping as already done"). AD-7 is satisfied a step earlier and in the usual place: the claim reaches this service only from `select_claim_detail`, which is scoped. `benefit_for_claim` takes its claim on identical terms. The parameter goes in when Story 3.3 gives the bill query something to scope.
- **The prototype's reference date is `DOI + daysOpen`, and porting that arithmetic would have been wrong.** It reads as an oddity until you notice `daysOpen` is a static dataset column — adding a claim's age to its date of injury is how a file with no clock says "now". This console has a clock, and its `days_open` counts from `froi_date` rather than `doi` (Story 2.1's argument), so the prototype's expression would land a few days *behind* today by exactly the reporting lag. `project_payments` takes `as_of`, which is the intent rather than the transliteration, and every test names its date rather than letting the calendar decide.
- **`test_a_missing_key_raises_rather_than_returning_an_empty_block` was asserting nothing and only said so today.** It asked `load(db, "reserve_bands")` for a document Epic 3 was going to add, so the day 3.2 added it the test failed — having quietly stopped testing missing keys at some earlier point that nobody would have noticed. Re-pointed at `no_such_rule`, a key no story will ever seed, with the trap written into the docstring.
- **The AD-10 phase guard fired twice, once as a false positive and once on a real question.** `PHASE_RULE_READ` matches "weeks" case-insensitively near a paren, and `class ScheduleWeekStatus` contains the letters `WeekS` — a class name, deciding nothing. The second hit is the one worth the paragraph: `SCHEDULE_WEEKS` really is a second table keyed by `RecoveryWindow`, beside `treatment_progress.EXPECTED_WEEKS`. They answer different questions and their numbers differ (a 0-2 week window is 14 expected days and *four* scheduled weeks, after the clamp), so folding either into the other would be wrong rather than tidy. Both files were added to `PHASE_HOME` — the escape hatch that test's own docstring sanctions — and `test_the_schedule_is_not_the_treatment_phases_window` pins the two apart so the entry cannot quietly become permission to grow a third.
- **A negative reserve reverses the cross-multiplication, which is worse than the case it was meant to handle.** Multiplying an inequality by a negative flips it, so a claim with $50,000 of exposure against a below-zero reserve would come back **heavy** — the console telling a handler to reallocate surplus on a claim in the worst possible state. No column stops the reserve going negative, so the sentinel branch is entered on `reserve_cents <= 0` rather than `== 0`, and `test_a_reserve_that_has_gone_negative_takes_the_zero_branch` is the one that would have caught it.
- **`_dollars` and `_round_half_up` were private and are now not.** Two deterministic sentences about the same reserve is two roundings of one figure unless they share a function, and the whole point of `rationale.py`'s money paragraph is that the reserve reads identically in prose and in the formatted row above it. `format_dollars` and `round_half_up` are the public names; `benefit.py`'s docstring already said "the only rounding in the financial engine, and Stories 3.2–3.5 inherit it", so this is that sentence becoming true rather than a new claim.

### Completion Notes List

- **AC 1 — the verdict exists once, and the guard that keeps it there is a source-level test rather than a convention.** `tests/test_reserve_block.py::test_no_module_outside_the_classifier_reads_a_reserve_band` greps every server module for the two band names and allows three: the classifier, the typed block that validates the document, and the migration that seeds it. The failure it prevents is specific and quiet — Story 3.3's Bills summary or Epic 6's "reserve adequacy review" computing its own ratio from the same two figures, agreeing on almost every claim and disagreeing exactly at a boundary, which is the one place a handler is being asked to act.
- **AC 1 — the payment-schedule projection landed here, and that is the story's seam rather than scope creep.** The indemnity half of projected exposure is `total_scheduled − paid_so_far`; there is no honest way to compute it without projecting the schedule, and AD-2 forbids the tempting alternative (a "just the totals" shortcut here and the real generator in 3.3) because that is two functions deciding how long a claim's schedule is. `services/financials/schedule.py` is complete — weeks, dates, statuses, totals and the next due date — and 3.3 persists `payment_schedule_week` rows *through* it. It is also where Story 3.1's `waitingPeriodDays` is first **applied** rather than displayed, which is what that document's description said 3.3 would do and what 3.2 needed first.
- **AC 1 — the bill query could not be written, and the seam is one named function rather than a scatter.** The story asks to "wire the query now"; the `bill` table is 3.3's migration, so there is no table to query and a runtime existence probe on the case-file path would be a worse answer than an honest zero. `unpaid_medical_cents(db, claim)` is the single function that returns it, carrying the exact query 3.3 replaces its body with. This is recorded as a partial rather than claimed as done: `test_the_medical_term_is_zero_until_story_3_3_seeds_the_bills` asserts today's state so that 3.3's seed *fails* it, which is the reminder rather than a surprise. The consequence is in `deferred-work.md`, because it is demo-visible: with one of two exposure terms missing the open portfolio bands 26 heavy to 7 light, and a genuinely under-reserved claim with large unpaid bills currently reads adequate.
- **The zero-reserve sentinel is a verdict, not a division guard, and it is written as one.** The prototype's `ratio = reserve > 0 ? projected / reserve : (projected > 0 ? 2 : 1)` looks like defensive code and is not: a claim carrying exposure against no reserve *is* under-reserved, and one carrying none has nothing to be wrong about. The branch is kept explicit rather than left to fall out of the cross-multiplication, so a reader checking this function against the prototype finds the prototype's two sentinels where they expect them.
- **A settled claim is judged, not skipped.** `closed_final` is a member of the enum rather than a `null` verdict, because "there is no further exposure" is an answer and the absence of one is not. `ratio_bp` *is* null there, and the two are different facts: no comparison was made, so there is no number — which is the payload a component assuming an integer would render as "NaN%". The exposure figures are still the computed ones rather than hardcoded zeroes, so a settled claim with an unpaid bill would be visible in the payload instead of asserted away.
- **AC 2 — the bands are the document's, and the test that proves it hands the classifier a document it has never seen.** Loading the real row proves the *loader* works, which is `test_rules_engine.py`'s subject; what was unproven anywhere else is that the classifier consumes what the loader returns. `test_the_bands_come_from_the_document_not_from_the_code` runs one exposure through the seeded bands and through a variant block and requires the verdict to change — if either threshold were a literal in `reserve.py`, both calls read identically and the test fails.
- **AC 3 — "identical in both surfaces" is structural, not a promise.** `reserveCheck` sits on the case file beside `benefit`, outside the stage-variant union, so Story 3.3's Bills summary reads the same field out of the same `queryKeys.claims.detail` cache entry. A copy on the treatment block would have let 3.3 read the other one with nothing anywhere to say they had drifted. Three tests hold the shape: the field is absent from `overview`, its `reserveCents` equals the one the card renders two rows above, and the SPA reads `claim.reserveCheck` rather than the overview block.
- **AC 3 — the card renders the server's answer even when the server is wrong, which is the only way to test that it is not computing.** `ReserveCheckCard.test.tsx` feeds it a deliberately impossible payload — an exposure at 140% of the reserve carrying the verdict `heavy` — and requires "Reserve Heavy" on screen. A *possible* payload cannot distinguish "rendered what it was sent" from "happened to agree"; this one can. The five reserve fields also joined `noDerivation.test.ts`'s scan, with the prototype's own `reserveCheck` transliterated into the smell list so the guard is shown to catch it.
- **light→error and heavy→warn reads backwards for about a second, and it is right.** A light reserve is money the carrier has not put aside; a heavy one is capital tied up. The prototype makes the same call (`var(--er)` for light) and it is the only sensible one — but it is the kind of pairing a later refactor "corrects", so each has its own test naming the reason. `closed_final` is muted rather than green, because green would read as a pass mark on a comparison nobody made.
- **`ScheduleWeekStatus` went into `data/models/enums.py` although this story creates no table.** A native enum column needs its members in the data layer (`data/` must not import from `services/`), and the projection that produces the value arrived a story before the table that stores it. Putting the vocabulary where 3.3 will need it means 3.3 adds a column over an existing type rather than renaming one out of `services/` on the way past — `BodyRegion`'s argument, one story later. `payment_scheduled` is named now and produced by nothing: it is 3.4's approval state, declared with its siblings so the states a week can hold are visible together.
- **AC 4 — the boundaries get four tests each, and the reason is that one would prove nothing.** A `>` and a `>=` agree on every input except the exact boundary, so sampling 1.20 and 1.10 passes against either. The pairs straddle each threshold by a single cent, on a round reserve so the arithmetic reads as the percentages it is testing, and again on an unround one where no exposure can sit exactly on the boundary at all.
- **Two Hypothesis properties rather than the one the story asks for.** Totality over the whole non-negative space is AC 4's, and it asserts the sum alongside it because those are the two ways this function fails without raising — falling off the end of its branches, or publishing a total that is not the sum of the parts beside it. The second property is the ratio/verdict agreement described in the Debug Log; it exists because publishing a differently-computed number is a real risk that only a property can cover.
- **The e2e spec checks the whole treatment book, not one claim.** One claim can agree with an oracle by luck; fifteen cannot, and the seeded portfolio's injury dates span January to September — so the same rule has to produce a fully elapsed schedule, a partly paid one and one that has not started. `fixtures/seed.ts` gained a second implementation of the projection *and* the cross-multiplied banding, because a float oracle would disagree with the server exactly at the boundary, which is the one place the spec is worth having.
- **Verdict distribution across the seeded portfolio: 7 light, 5 adequate, 26 heavy, 62 closed_final.** Checked rather than assumed, because a rule that answered one thing for everybody would pass every test above and be visibly useless in a demo. The heavy skew is the missing medical term and is recorded as such.
- **A verdict is withheld rather than guessed when an exposure term is not on file.** The `bill` table is Story 3.3's, so the medical term is `None` — not `0` — and `classify_reserve` publishes `indeterminate` for the two bands an unknown non-negative amount could flip, while keeping `light`, which is sound on a lower bound. The full argument is in the Change Log; the short version is that "this claim has no unpaid bills" was a statement the console was not entitled to make, and making it was what produced portfolio-wide advice to release reserves.
- **The card states one notion of "paid", and that took a code review to notice.** The verdict is computed from the schedule projection; the row above it was rendering `claim.paid_indemnity`, a column that is 0 on every open seeded claim. Both figures now come from the projection, which is what the prototype does and what its own comment says to do. The full account is in the Change Log — it is the one finding in this story that a handler would have seen.
- **Tests:** **1015 server** (108 new — the three bands, both boundaries to the cent on round and unround reserves, the two exposure terms counting separately, the zero and negative reserve sentinels, the settled short-circuit and its figure-free sentence, the three rationales word for word and the money-formatting agreement, the document-versus-code test, the band block's refusals and its required parameters, two Hypothesis properties; the week counts and the clamp at both ends, the waiting period, back-to-back weeks, every status branch including both stage short-circuits and the day either side of "due this week", the floor at zero, the next-due exclusion, and the schedule-is-not-the-phase-window pin; the block at all four stages, its absence from the stage variant, the supervisor's read, integer cents, the rationale as the only formatted money, the exposure sum, the 3.3 seam, and the source-level guard that keeps the bands out of every other module). **267 vitest** (14 new — the four verdicts with their tones, the rationale verbatim, the settled sentence naming no figures, the contradictory-payload test, the null ratio, and the two stages the chip renders on and the three it does not; plus the two reserve smells added to the derivation guard). **102 Playwright** (10 new, `@story:3-2 @epic:3`, one `@smoke`).
- **Verified live.** e2e stack (`compose.e2e.yaml`, :8081) rebuilt with the new API and SPA: full suite **102/102**, the 15-test `@smoke` set and the 15-test `@epic:3` set all green against a freshly reset stack. `alembic check` reports no drift and a `downgrade 0024` → `upgrade head` round trip completes clean. `npm run generate:api` leaves `src/api/schema.d.ts` byte-identical on a second run. ruff + format + mypy clean; eslint clean (8 pre-existing warnings, none new); tsc clean for both `web/` and `e2e/`. No new runtime or dev dependency.

### File List

**New — server**

- `lineworker/server/data/versions/20260814_0025_reserve_bands.py`
- `lineworker/server/rules/documents/reserve_bands.jdm.json`
- `lineworker/server/services/financials/reserve.py`
- `lineworker/server/services/financials/schedule.py`
- `lineworker/server/tests/test_reserve_check.py`
- `lineworker/server/tests/test_reserve_block.py`
- `lineworker/server/tests/test_payment_projection.py`

**Modified — server**

- `lineworker/server/data/models/enums.py` (`ScheduleWeekStatus`)
- `lineworker/server/rules/parameters.py` (`RESERVE_BANDS_KEY`, `ReserveBands`, `reserve_bands_for`)
- `lineworker/server/services/financials/__init__.py` (the package's second and third modules, and their exports)
- `lineworker/server/services/financials/benefit.py` (`_round_half_up` → `round_half_up`)
- `lineworker/server/services/financials/rationale.py` (`_dollars` → `format_dollars`, and why it is public)
- `lineworker/server/services/claims/detail.py` (`reserve_check` on the case file; the benefit computed once and shared)
- `lineworker/server/api/routers/claims.py` (`ReserveCheckResponse`, the field on `ClaimDetailResponse`, and the treatment variant's corrected note)
- `lineworker/server/tests/test_rules_engine.py` (the new document in both tables and its expected values; the missing-key test re-pointed)
- `lineworker/server/tests/test_case_file_derivations.py` (`PHASE_HOME`'s two entries, and the argument for each)

**New — web / e2e**

- `lineworker/web/src/features/claim-detail/ReserveCheckCard.test.tsx`
- `lineworker/e2e/stories/3-2-reserve-adequacy-check.spec.ts`

**Modified — web / e2e**

- `lineworker/web/src/api/claims.ts` (`ReserveCheck`, `ReserveVerdict`, and why neither has a key of its own)
- `lineworker/web/src/api/schema.d.ts` (regenerated)
- `lineworker/web/src/features/claim-detail/labels.ts` (`RESERVE_VERDICT_LABEL`)
- `lineworker/web/src/features/claim-detail/overview/TreatmentOverview.tsx` (the chip and the rationale box, replacing the placeholder)
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.test.tsx` (the placeholder test, replaced)
- `lineworker/web/src/features/queue/noDerivation.test.ts` (Story 3.2's five derived fields, and two smells)
- `lineworker/web/src/test/api-mock.ts` (`RESERVE_CHECK` and its three variants; `reserveCheck` on the four case-file fixtures)
- `lineworker/e2e/fixtures/seed.ts` (the 3.2 oracle: `expectedReserveCheck`, `claimIdsInStage`, `RESERVE_VERDICT_LABEL`, and three columns on `SeedClaim`)

**Modified — repo**

- `_bmad-output/implementation-artifacts/deferred-work.md` (five items)
- `_bmad-output/implementation-artifacts/3-2-reserve-adequacy-check.md` (this file), `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-08-14: **Honest degradation — the verdicts a missing exposure term could flip are now withheld** (requested after the code review's fourth finding).

  The `bill` table is Story 3.3's, so `unpaid_medical_cents` had no honest number to return and was returning `0`. That is a statement about the claim — "this claim has no unpaid bills" — which nothing here was entitled to make, and it was the root of the review's observation that the console advised reallocating the reserve on 26 of 38 open claims, with the share growing every week as schedules elapsed. It now returns `None`, and `None` and `0` are different answers all the way to the wire.

  **Which verdicts survive is arithmetic rather than preference, and the withholding is deliberately not blanket.** The unknown term is non-negative, so the exposure computed without it is a *lower bound*. That makes exactly one band still sound: `light` — if scheduled indemnity alone already exceeds 115% of the reserve, adding bills can only push it further past the same threshold. `adequate` and `heavy` are both claims about an *upper* bound and an unknown addition can move either up a band, so both are withheld as a fifth verdict, `indeterminate`. Suppressing all four would have cost the console exactly the signal the story's "so that" clause names — under-reserved claims surfacing early — while protecting against nothing, since a `light` verdict on partial data is not wrong.

  `projected_remaining_cents` and `ratio_bp` are `None` alongside the medical term, because a total missing a term is not a total and a ratio computed from one is not a ratio; publishing the lower bound under either name would be the same mislabel the previous fix removed one field over. The card shows "Awaiting Bill Data" in a muted tone (any status colour would imply a conclusion) above a sentence naming what is missing, and the `light`-on-partial rationale says "before medical bills are counted" so a handler acting on it knows the figure is a floor. Verified against the running stack: Kaya's book reads **4 light, 15 indeterminate, 26 closed_final**, and "reallocating surplus" appears nowhere.

  Fifteen new tests hold it — the `None`/`0` distinction at the classifier, each band's withholding, the light boundary under a missing term, the two nullable figures, both new rationales, a blanket "no withheld verdict ever advises reallocating", the property extended over a nullable medical term, and three end-to-end passes over the whole open book asserting that the withholding is real, that it is *not* a blackout, and that the card says what is missing.

  Gate: ruff + format + mypy clean, **pytest 1015**, **vitest 267**, **Playwright 102/102** against a rebuilt e2e stack.

- 2026-08-14: Addressed code review findings — **5 findings, 4 fixed, 1 recorded, 0 dismissed**. One was a real user-visible defect and is the one worth reading.

  **The treatment card showed two different answers to "how much indemnity has been paid", and the verdict was computed from the one it did not display.** The row read `overview.paidIndemnityCents` — `claim.paid_indemnity`, which is 0 on every open seeded claim — while the reserve verdict was computed from the schedule projection, in which weeks that have elapsed count as disbursed. On 19 of Kaya's 28 treatment claims a handler therefore saw "Indemnity paid **$0.00**" directly above "Reserve check: **Reserve Heavy** — projected remaining exposure (**$0**). Consider reallocating surplus": two contradictory statements on one card, and a recommendation to release the whole reserve on a claim showing no disbursements. The prototype has one answer rather than two — its treatment card renders `sch.paidSoFar of sch.totalScheduled` (line 1364) and `billsHTML` states outright that the static column "is often 0 even though the schedule already show[s] real disbursements" (line 1429) — so the projection is the live figure and the column is a stale snapshot. The projection had been ported correctly; the card had not. `ReserveCheck` now publishes `scheduled_indemnity_cents` and `disbursed_indemnity_cents` (named `disbursed_` precisely because `overview.paidIndemnityCents` still exists and means something else), the card renders that pair, and three tests hold it: the payload identity `remaining = max(0, scheduled − disbursed)` at every stage, a component test with a fixture where the two sources disagree, and an e2e pass over the whole treatment book asserting no card reads "$0 of …" beside "exposure ($0)". Verified against the running stack — the four claims cited in the review now read "$21,793 of $22,940", "$0 of $5,531", "$6,584 of $6,584", "$6,070 of $6,070", each consistent with its verdict.

  **The e2e oracle claimed more independence than it has.** `expectedReserveCheck` re-derives which weeks are disbursed from the same calendar rule the server uses, so it can catch an arithmetic slip or a flipped boundary but cannot be evidence for the modelling assumption it shares. The docstring said it made "a spec that loaded the rule it is testing" impossible, which overstated it. Corrected to say what it does and does not prove, and the assumption is now checked where it actually is checkable — against what the card renders, in the contradiction test above.

  **Two low findings, both mine, both trivial:** `ScheduleClaim`'s docstring said "four claim columns" over a three-member protocol, and `test_a_settled_claim_is_fully_paid_however_recent` carried a stray `_unused: None = None` parameter that would have become a fixture-resolution error the moment anybody removed the default.

  **One finding recorded rather than fixed.** The reviewer notes that the `heavy` skew from the missing medical term is not stable but *grows*: every schedule is at most twenty weeks from the date of injury, so as the calendar advances the whole open book walks to `heavy` and the console ends up advising release of every reserve. That is correct and is now in `deferred-work.md` with its two options (seed `bill` early, or withhold the verdict while an exposure term is known-missing). Not acted on here because the story states verdicts complete when 3.3 lands, and inventing a suppression rule the spec does not describe would be a worse failure than the one it avoids — but it is flagged as a call for 3.3 rather than left implicit.

  Gate re-run green: ruff + format + mypy clean, **pytest 1002**, **vitest 265**, **Playwright 100/100** plus the 15-test `@smoke` set against a rebuilt e2e stack.

- 2026-08-14: Story 3.2 implemented end to end — the reserve adequacy verdict, and the payment-schedule projection it is computed from. `services/financials.classify_reserve` bands projected remaining exposure against the reserve in exact integer arithmetic (the bands are basis points in a new `reserve_bands` JDM document, cross-multiplied rather than divided, so the story's 1.15 and 0.60 boundaries are exact), with the prototype's zero-reserve sentinel kept as an explicit verdict and a settled claim short-circuited to `closed_final` before any band math. `services/financials.project_payments` is the single schedule generator Story 3.3 must persist through, and the first place Story 3.1's waiting period is applied rather than displayed. The verdict is published as `reserveCheck` on the case file beside `benefit` — one field, one query key — which is what makes AC 3's "identical in both surfaces" structural rather than a convention; the treatment Overview renders the chip and the server's rationale and computes nothing. The medical half of the exposure is nil until Story 3.3 seeds `bill`, which is the story's own deliberate seam, recorded with its consequences in `deferred-work.md`. Full gate green: ruff + format + mypy clean, pytest 1015, vitest 267, Playwright 102/102 plus the 15-test `@smoke` set and the 15-test `@epic:3` set against a rebuilt e2e stack. Status → review.
