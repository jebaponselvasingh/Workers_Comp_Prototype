# Story 6.2: AI Insight Cache & Insights Tab

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a claims handler,
I want per-claim AI insights with visible freshness,
so that AI context is available without pretending to be claim data.

## Acceptance Criteria

1. **Given** the `ai_insight` table `(claim_id, kind, content, model, generated_at)` created here and owned by `services/rag` (AD-10, AD-12), **when** insights generate (scheduled, or on-demand through the single refresh command), **then** the four kinds populate per claim — similar-case outcomes, reserve adequacy review, next best actions, fraud risk indicators — with any figures sourced from deterministic service output (AD-2).
2. **Given** the AI Insights tab, **when** it renders, **then** the four cards display with their generation timestamps, never user-editable (FR-H-9), with an explicit state for claims whose insights aren't generated yet (NFR-3).
3. **Given** a low-fraud-risk claim, **when** the fraud card renders, **then** it shows the low-risk confirmation rather than an empty red-flag list, and Epic 3's "View Fraud Indicators" deep-link enables and lands here.

## Tasks / Subtasks

- [ ] Task 1: `ai_insight` migration (AC: 1)
  - [ ] New Alembic revision: `ai_insight` (surrogate `id`, `claim_id` FK, `kind` snake_case enum `similar_case_outcomes | reserve_adequacy_review | next_best_actions | fraud_risk_indicators`, `content jsonb`, `model text`, `generated_at timestamptz`, unique `(claim_id, kind)` — the cache holds the latest generation per kind, not history)
  - [ ] No `version` column: `ai_insight` is not a user-mutable entity (AD-10 — never user-editable; refreshed wholesale by its owner); PHI-class store under AD-11
- [ ] Task 2: Deterministic inputs per kind (AC: 1)
  - [ ] For each kind, gather figures **only** from existing deterministic services (AD-2 — the LLM never originates a figure): similar-case → `services/rag.similar_claims` results incl. `embedded_at` (6.1); reserve review → Epic 3's single reserve-check computation (`services/financials`); next best actions → Epic 3's action-worklist generation (`services/worklist`); fraud risk → the claim's fraud score/flags via `services/derivations` (real fraud modeling is Deferred — narrate the existing derived fields, invent nothing)
  - [ ] Structured output: each kind has a Pydantic model (e.g. ranked actions list, red-flag list with narrative, low-risk confirmation variant) validated via `with_structured_output(method="json_schema")` before anything persists — `content` stores the validated structure, so the UI renders typed cards, not prose blobs
  - [ ] Every money/date figure in `content` carries the service-formatted display string alongside the raw value (same spirit as AD-13's `{data, display}`), so cards never re-derive or re-format figures
- [ ] Task 3: Generation pipeline + single refresh command (AC: 1)
  - [ ] **Layering (read before coding):** the spine's dependency diagram allows chat inference only from `agents/` (services touch Ollama for embeddings only). Therefore: the narrative-generation code (langchain-ollama chat client, prompts, structured-output plumbing) lives in `agents/` (e.g. `agents/insights.py` — a plain module, **not** part of the 6.3 copilot StateGraph), and persistence goes through the **one** `services/rag` refresh command (`refresh_insights(claim_id, kinds=all)`) that owns all `ai_insight` writes (AD-12). `agents/` calls `services/` — never the reverse
  - [ ] Deterministic-figure fetches from `agents/insights.py` go through thin wrapper functions placed in `agents/tools/` (one service call each, `{ok, data, display}`-shaped) — 6.3 formalizes this same module into the registered tool registry; do not build the registry itself here, just keep the wrappers thin so 6.3 can absorb them
  - [ ] Prompts as versioned files in `agents/prompts/` (one per kind), loaded by key — never inline string literals (spine Prompts convention)
  - [ ] Scheduled generation: in-process job (mechanism deferred — same style as 6.1's embedding refresh) generating missing/stale insights across the portfolio under a server-resolved system context; on-demand regeneration goes through the same single command — one refresh path, not two (AD-12)
  - [ ] Insight writes are exempt from the (future, 6.5) interrupt gate but **not** from audit: emit a content-free AD-4 audit event per refresh (actor = system or requesting user, action, claim, kind — no content)
  - [ ] Logs: IDs and event names only — never prompt bodies or model output (AD-11)
- [ ] Task 4: Insights API (AC: 2, 3)
  - [ ] `GET` endpoint (claim business-ID route) returning the four kinds with `generatedAt` + `model`, camelCase via the established alias generator; caller context from the auth dependency scopes claim access (AD-7) — a handler cannot fetch insights for an out-of-scope claim
  - [ ] Missing kinds return explicitly (e.g. `status: not_generated`) so the UI renders the NFR-3 empty state, not a 404
  - [ ] Optional `POST …/refresh` triggering the single command (returns 202-style accepted or fresh payload — keep it simple; no streaming here)
- [ ] Task 5: AI Insights tab UI (AC: 2, 3)
  - [ ] Fill the Epic-2 empty-state AI Insights tab (`web/src/features/claim-detail/`, UX-DR5 six-tab pane): four cards — similar-case outcomes, reserve adequacy review, next best actions, fraud risk indicators — via TanStack Query on the shared `queryKeys` module (AD-9)
  - [ ] Each card shows its `generatedAt` timestamp and model label; content is read-only — no edit affordance anywhere (FR-H-9 / AD-10)
  - [ ] Per-claim not-yet-generated state: explicit card-level empty state with the refresh affordance; loading + error states per UX-DR11
  - [ ] Fraud card: when the structured content is the low-risk variant, render the low-risk confirmation styling (ok-token semantics) — never an empty red-flag list
  - [ ] Design tokens: use the Story 1.1 Tailwind tokens (canonical **light** palette; the "dark console aesthetic" wording in UX-DR12 is a documented discrepancy — do not invent a dark theme)
- [ ] Task 6: Enable Epic 3's "View Fraud Indicators" deep-link (AC: 3)
  - [ ] Story 3.5 left Fraud Indicators action links rendered disabled-with-tooltip ("→ Epic 6"); flip them to enabled, deep-linking to the AI Insights tab (fraud card focused/scrolled) for the claim
  - [ ] Amend Story 3.5's Playwright spec in this story's PR (AD-15: specs are amended, never deleted) — the disabled-link assertion becomes an enabled-deep-link assertion
- [ ] Task 7: Tests (AC: all)
  - [ ] Unit: refresh command is the sole writer (attempted direct write path doesn't exist / repository guarded); structured-output validation rejects malformed model output; low-risk variant selection logic; figures in `content` match deterministic service output exactly (assert equality against the service call, catching any LLM-originated figure)
  - [ ] Integration: generate-all for a seed claim against the model stub → four rows, correct kinds, timestamps; on-demand refresh replaces, not duplicates (unique constraint)
  - [ ] Scope test: out-of-scope persona gets no insights for a foreign claim (repository + API level)
  - [ ] E2E: `e2e/stories/6-2-ai-insight-cache-insights-tab.spec.ts` tagged `@story:6-2 @epic:6`, one `@smoke` happy path — handler opens AI Insights tab, four cards with timestamps render from stub-generated content; plus not-yet-generated empty state and the Epic-3 deep-link landing. Assert structure (cards, kinds, timestamp presence), never prose

## Dev Notes

Batch-created story: at dev time, first skim the Dev Agent Records of all previously completed stories for learnings — this file predates them.

### What this story is — and is not

This story delivers the **AI-insight cache and its tab**: the `ai_insight` table, the generation pipeline with the single refresh command, the four read-only cards, and the Epic-3 fraud deep-link enablement. It is **not**: chat, threads, SSE, or the copilot panel (6.3); the QAS quick actions that *answer in chat* using the same deterministic services (6.4 — the fraud *card* here and the fraud *quick action* there are different surfaces over the same derived data); interrupts (6.5); degradation handling (6.6 — if the model is down, insight generation simply doesn't refresh; cards keep showing their last `generated_at`, which is the honest behavior for a cache, and the empty state covers never-generated claims). The prototype's `cp*` canned fields (`cpSimilarCase`, `cpReserveNote`, …) are the *content shape* reference only — pre-authored text presented as AI is banned (AD-14); every row here is genuinely generated.

Epic 7 note (do not build now): Story 7.1 will aggregate the fraud-kind narratives portfolio-wide — keeping `content` structured (Task 2) is what makes that possible.

### Architecture compliance (binding ADs for this story)

- **AD-2:** every figure in an insight comes from deterministic service output; the LLM narrates only.
- **AD-5:** chat inference stays local and originates only from `agents/` code; `services/rag` still touches Ollama for embeddings only.
- **AD-7:** insight reads ride normal claim scoping; generation jobs use a server-resolved context, never caller-supplied scope.
- **AD-10:** AI narratives are cache rows with generation timestamps, never claim columns, never user-editable.
- **AD-12:** `services/rag` owns `ai_insight`; exactly one refresh command serves scheduled and on-demand paths.
- **AD-11:** insight content is PHI-class; prompts/outputs never logged.
- **AD-9:** tab state via TanStack Query + shared `queryKeys`; no client-side derivation of figures.
- **AD-15:** spec amended for 3.5's deep-link in the same PR; new story spec gates done.

### Data notes

Table created here: `ai_insight (claim_id, kind, content, model, generated_at)` — owner `services/rag` (AD-12), 4 kinds, latest-per-kind via unique `(claim_id, kind)`, `content` as validated JSONB structure. `model` records the actual serving model name (config value at generation time) for the card label. Reads existing: claim + derived fields (`services/derivations`), reserve check (`services/financials`), action worklist (`services/worklist`), similar-case retrieval (6.1). No writes to any table not owned by `services/rag` except the AD-4 audit event via its command.

### UX notes

- UX-DR5: AI Insights is the sixth tab of the detail pane — fill the existing empty state, don't restructure the pane.
- UX-DR11: loading / empty / error states on the tab and per card; no native dialogs.
- UX-DR12: prototype visual identity via the Story 1.1 tokens — **light palette is canonical** (epics' "dark console aesthetic" is the documented discrepancy, ruled at story-creation time); fraud low-risk confirmation uses ok/ok-soft tokens, red-flag lists use error tokens.
- FR-H-9 (tab half): claim-aware AI context surfaced in the case file; the copilot half arrives in 6.3.

### Testing requirements

- Unit + integration + scope tests per Task 7; the "figures equal service output" assertion is the AD-2 enforcement test — treat it as mandatory.
- Structured-output validation failure path: malformed stub response ⇒ no row written, error logged content-free (never a half-written card).
- Playwright: `e2e/stories/6-2-ai-insight-cache-insights-tab.spec.ts` tagged `@story:6-2 @epic:6` with exactly one `@smoke` happy path, against the reset e2e stack + model-stub (scripted insight responses). Story cannot move to `review`/`done` until it passes (AD-15 done-gate).

### Project Structure Notes

- Generation code in `agents/` (`insights.py` + `agents/prompts/` files + thin `agents/tools/` wrappers); ownership command + repository in `services/rag` / `server/data/`; UI in `web/src/features/claim-detail/`.
- Depends on 6.1 (similar-case retrieval, model serving, stub); do not start before 6.1 is done.
- The layering resolution in Task 3 (agents-hosted generation persisting through the services/rag command) reconciles the spine's "chat only from agents/" edge with AD-12's "services/rag owns ai_insight" — flagged as a discrepancy note at story creation; if dev finds friction, raise it rather than moving chat calls into services.

### References

- Story + ACs: [Source: _bmad-output/planning-artifacts/epics.md#Story 6.2]
- AD-2 / AD-10 / AD-12 full text; dependency diagram edges: [Source: _bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md#AD-2 / #AD-10 / #AD-12 / #Design Paradigm]
- Structured outputs (`json_schema`), RAG design: [Source: docs/Architecture-LINEWORKER.md#5.3]
- AD-6 insight-refresh audit exemption: [Source: ARCHITECTURE-SPINE.md#AD-6]
- Epic 3 disabled deep-link seam: [Source: _bmad-output/planning-artifacts/epics.md#Story 3.5 (line ~624)]
- UX-DR5/11/12; token discrepancy ruling: [Source: epics.md#UX Design Requirements; _bmad-output/implementation-artifacts/1-1-running-project-skeleton.md#Design Tokens]
- Prototype `cp*` content-shape reference: [Source: docs/Workers_Comp_Prototype.html (cpSimilarCase / cpReserveNote / cpNextActions / fraud indicator fields)]

## Dev Agent Record

### Agent Model Used

<!-- filled by dev-story -->

### Debug Log References

### Completion Notes List

### File List
