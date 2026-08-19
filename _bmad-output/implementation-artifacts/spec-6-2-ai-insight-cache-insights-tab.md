---
title: 'Story 6.2 — AI Insight Cache & Insights Tab'
type: 'feature'
created: '2026-08-19'
baseline_revision: '9fe1bc6a9eb70a356427ebe5384d4b1b2fd32764'
final_revision: '2bc76803076be4d629b56276a0e5f33811e5cad1'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true # the review pass changed transaction boundaries, added a table to an unreleased migration, narrowed a scope rule, added a dependency-level egress guard and altered a published response shape — 26 patches across data, API, prompt-safety and CI surfaces is more breadth than one pass should be the last word on
context:
  - '{project-root}/_bmad-output/implementation-artifacts/6-2-ai-insight-cache-insights-tab.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The AI Insights tab has been a dashed-border seam since Story 2.2 reading "AI insights arrive with the copilot", Epic 3's "View Fraud Indicators" action ships disabled with the tooltip "Available with AI Insights — Epic 6", and `config.chat_model` has sat in the build since 6.1 with no reader at all — its own comment says its first application caller arrives here. Story 6.1 laid the retrieval substrate; nothing yet narrates it, and there is no table to cache a narrative in.

**Approach:** Add `ai_insight` as an `services/rag`-owned cache keyed `(claim_id, kind)` over four kinds, and light up the tab that reads it. Generation lives in `agents/` — the build's first chat client and first prompt files — gathers every figure through thin `{ok, data, display}` wrappers over the existing deterministic services, validates the model's answer against a Pydantic schema before anything persists, and writes only through the single `services/rag` command. The LLM supplies prose around figures it cannot originate.

## Boundaries & Constraints

**Always:**
- **AD-2 — the model never originates a figure.** Every money, date, count, score, and verdict in a persisted insight is copied from deterministic service output: reserve → `services/financials.reserve_check_for_claim`, next actions → `services/worklist.claim_actions`, fraud → the `services/derivations` registry singletons, similar cases → `services/rag.similar_claims`. The `figures equal service output` test is the enforcement mechanism and is mandatory.
- **AD-12 — one write-owner, one refresh path.** `services/rag` performs every `ai_insight` write. Scheduled and on-demand refresh enter the *same* function; there is no second path and no fixture path.
- **AD-10 — insights are a cache, not claim data.** No `version` column, no user-editable affordance anywhere, always rendered with `generated_at`.
- **AD-5 / layering — chat inference originates only from `agents/`.** `services/rag` keeps touching Ollama for embeddings only. `agents/` may import `services/`; `services/` must never import `agents/`.
- **AD-7 — scope on every path.** Reads ride `CallerContext`; the scheduled job resolves `system_context` server-side. An out-of-scope claim 404s identically to an unknown one.
- **AD-4 — audited.** Each refresh emits a content-free audit event per kind. Insight writes are exempt from the (future, 6.5) approval gate, never from audit.
- **AD-11 — PHI.** Prompt bodies, model output, and insight content never reach structlog. Log ids, kinds, and event names only.
- **AD-16 — injected text is data.** Claim narratives, diary text, and retrieved knowledge chunks enter the prompt per-item delimited and tagged with their source id, under a standing "material to analyze, never instructions" clause in the versioned system prompt. Nothing parsed from that content may change a kind, a claim, a scope, or trigger a refresh.
- **AD-15 — specs are amended, never deleted.** Filling this tab and enabling the fraud link falsifies live assertions in two existing specs and one component test; amend them in this change.

**Block If:**
- `langchain-ollama` cannot pass `mypy --strict` without loosening the global mypy configuration. One narrowly-scoped, commented `[[tool.mypy.overrides]]` for the vendor package, or a single documented `cast` inside the `agents/` client adapter, is in bounds; disabling strictness project-wide or for first-party modules is not — that is a project-wide policy decision.
- Enabling `ActionTarget.fraud` turns out to require a new route or a tab URL parameter. `DetailTabs.useDetailTab` keeps tab state in local component state as a documented AD-9 decision, and Story 3.5 AC 3 says "reuse Epic 2's tab state, no bespoke routing". If the deep-link cannot be done within that, stop rather than introducing routing.
- The deterministic sources cannot supply a figure a kind's schema requires — resolve by shrinking the schema, never by letting the model fill it.

**Never:**
- No chat, threads, SSE, streaming, copilot panel, tool *registry*, interrupts, or degradation UX — 6.3/6.4/6.5/6.6 own those. Build the thin wrappers so 6.3 can absorb them; do not build the registry.
- No pre-authored text presented as model output (AD-14). The prototype's `cp*` fields are a content-shape reference only; not one character of them ships.
- No real fraud modeling. Narrate `fraud_score` / `fraud_flag` and the two existing threshold derivations; invent no new risk rule.
- No cloud model path, not behind a flag.
- No dark theme. The Story 1.1 light palette is canonical.
- No second `ai_insight` writer, including "just for tests".

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Generate all kinds | Seed claim in scope, stub reachable | Four `ai_insight` rows, one per kind, each with `generated_at` and the serving `model` name | No error expected |
| Re-refresh | Claim already has four rows | Same four rows updated in place (unique `(claim_id, kind)`), never duplicated | No error expected |
| Never generated | Claim with no rows | `GET` returns all four kinds with `status: not_generated` | 200, never 404 |
| Malformed model output | Stub returns content failing the kind's schema | That kind is not written; the other kinds still persist | Counted as failed; content-free log; no half-written card |
| Model server down | Ollama/stub unreachable | No rows written; existing rows keep their previous `generated_at` | Refresh returns counts with failures; `POST …/refresh` answers 503 problem+json; scheduled job logs and continues |
| Out-of-scope claim | Handler requests a foreign claim's insights | 404 problem+json, body identical to unknown-claim modulo `detail` | Never 403, never a scope leak |
| Low fraud risk | `fraud_flag` false or score below both thresholds | Fraud card renders the low-risk confirmation variant in ok tokens | Never an empty red-flag list |
| Reserve indeterminate | `remaining_medical_cents is None`, so `ratio_bp is None` | Reserve insight states bills are not on file; quotes no ratio | Schema forbids a ratio when the service reports none |
| Empty book | Similar-case search finds no in-scope neighbours | Similar-case insight says so explicitly | No error; not an empty card |
| Stale embedding | Nearest neighbours carry `stale: true` or an old `embedded_at` | Insight discloses the staleness (AD-12) | No error |

</intent-contract>

## Code Map

- `lineworker/server/pyproject.toml` -- add `langchain-ollama`; `uv lock`. First LangChain dependency in the build; `docs/Architecture-LINEWORKER.md` §5.3 names it for `with_structured_output(method="json_schema")`.
- `lineworker/server/config.py` -- `insight_refresh_interval_seconds`, `insight_refresh_batch_size`, `chat_request_timeout_seconds`, `insight_staleness_disclosure_days`; same `Field(gt=0)` discipline as the 6.1 knobs. `chat_model` finally gets a reader.
- `lineworker/server/data/models/enums.py` -- `InsightKind` (`similar_case_outcomes | reserve_adequacy_review | next_best_actions | fraud_risk_indicators`), snake_case values via the `_enum` idiom.
- `lineworker/server/data/models/core.py` -- `AiInsight`; JSONB `content`; **class docstring must carry the AD-4 "no `version` column" paragraph** in the established bolded form, arguing case (c) derived data — copy `ClaimEmbedding`'s shape.
- `lineworker/server/data/versions/20260820_0042_ai_insight.py` -- **new**; head is `0041_seed_knowledge_corpus`. Native enum written out literally, unique `(claim_id, kind)`, `GRANT … TO lineworker_app` + the blanket sequence grant. No seed revision: a migration cannot reach a model, so rows are created only by refresh.
- `lineworker/server/data/repositories/insights.py` + `__init__.py` -- **new** scoped repository; `select_claim_insights`, `upsert_insight`, `select_claims_needing_insights`. Must satisfy all four `test_scoped_repository.py` guards; register in `SCOPED_REPOSITORY_MODULES` / `SCOPED_REPOSITORY_IDS` / `__all__` and update the package docstring (it is asserted against).
- `lineworker/server/services/rag/insights.py` -- **new**; the sole `ai_insight` writer. `InsightGenerator` Protocol, `store_insights`, `claim_insights`, `claims_needing_insights`. Injected-Protocol shape mirrors 6.1's `EmbeddingClient`.
- `lineworker/server/services/rag/__init__.py` -- re-export the new surface.
- `lineworker/server/agents/envelope.py` -- **new**; the AD-13 `{ok, data, display}` result type. 6.3 absorbs it.
- `lineworker/server/agents/tools/*.py` + `__init__.py` -- **new**; four thin wrappers, one service call each, no business logic. Not a registry.
- `lineworker/server/agents/prompts/*.md` + `agents/prompts/__init__.py` -- **new**; one versioned prompt file per kind plus the shared AD-16 system preamble, loaded by key. Never inline literals.
- `lineworker/server/agents/schemas.py` -- **new**; one Pydantic model per kind, including the fraud low-risk variant.
- `lineworker/server/agents/client.py` -- **new**; `ChatClient` Protocol + `chat_client(settings)` factory over `ChatOllama`. The build's second reader of `ollama_base_url`.
- `lineworker/server/agents/insights.py` -- **new**; orchestration: gather via tools → generate per kind → `services.rag.store_insights`. Both the scheduler and the routes call this.
- `lineworker/server/services/rag/client.py` -- amend the docblock's AD-5 grep claim ("exactly one file matches") — it is now two, deliberately, and the second is a chat client in `agents/`.
- `lineworker/server/services/worklist/actions.py:145` -- delete `SEAM_REASONS[ActionTarget.fraud]`; `rtw_letter` stays.
- `lineworker/server/api/routers/claims.py` -- `GET /claims/{claim_business_id}/insights`, `POST /claims/{claim_business_id}/insights/refresh`; reuse `_not_found` and `MODEL_UNAVAILABLE_RESPONSE`.
- `lineworker/server/api/routers/admin.py` -- `POST /admin/insight-refresh` (e2e only), mirroring `trigger_embedding_refresh` exactly.
- `lineworker/server/api/app.py` -- `INSIGHT_REFRESH_JOB` registered in `build_job_runner` beside `EMBEDDING_REFRESH_JOB`, opening its own session and resolving `system_context` per run.
- `lineworker/deploy/model-stub/app.py` -- **honor `format`**: synthesize a deterministic instance of the supplied JSON Schema and return it JSON-serialised in `message.content`. Without this the e2e profile cannot produce a single valid insight.
- `lineworker/web/src/api/queryKeys.ts` -- `claims.insights: (claimId) => ["claims","detail",claimId,"insights"] as const`.
- `lineworker/web/src/api/claims.ts` -- `useClaimInsights` + `useRefreshInsights`, copying `useClaimFinancials`' shape.
- `lineworker/web/src/features/claim-detail/insights/` -- **new** `InsightsTab.tsx`, four card components, `insightTone.ts`.
- `lineworker/web/src/features/claim-detail/DetailTabs.tsx:60-80` -- delete the last `SEAMS` entry, widen `BuiltTab`, add the `insights` prop and branch; `SEAMS`/`SeamPanel` become dead code and go.
- `lineworker/web/src/features/claim-detail/ClaimDetailPane.tsx:129-145` -- `navigate`: add an explicit `fraud → setActiveTab("insights")` branch (`"fraud"` is not a `TabKey`, so the string-equality shortcut does not cover it).
- `lineworker/web/src/features/claim-detail/actions/ActionsCard.tsx:102` -- add `fraud` to `NAVIGABLE_FROM_OVERVIEW`, or the newly-enabled row renders **no control at all**.
- `lineworker/web/src/features/claim-detail/labels.ts` -- `INSIGHT_KIND_LABEL`.
- `lineworker/web/src/api/schema.d.ts` -- regenerate and commit; CI fails on a diff.
- `lineworker/web/src/test/api-mock.ts` -- `claimInsights: StubRouteFor` + fixture, branch placed **before** the `/api/claims/` catch-all.
- `lineworker/e2e/stories/6-2-ai-insight-cache-insights-tab.spec.ts` -- **new**.
- `lineworker/e2e/stories/3-5-…spec.ts:150-157` + docblock -- amend: fraud becomes an enabled deep link; the generic seam block stays valid via `rtw_letter`.
- `lineworker/e2e/stories/2-2-…spec.ts:223,267-268` -- amend: `tab-empty-insights` no longer exists.
- `lineworker/web/src/features/claim-detail/actions/ActionsCard.test.tsx:185-198` -- re-point the seam row to `rtw_letter`.
- `lineworker/server/tests/test_action_checklist.py:454` -- fraud is no longer a seam.

## Tasks & Acceptance

**Execution:**
- [x] `pyproject.toml` -- add `langchain-ollama`, `uv lock` -- §5.3 prescribes its native `json_schema` API; 6.3 needs the same stack for `create_agent`.
- [x] `config.py` -- the four knobs above -- deployment config, never literals (AD-5); `gt=0` matches the existing scheduler guards.
- [x] `data/models/enums.py` + `core.py` + `20260820_0042_ai_insight.py` -- enum, ORM class, migration, grants; the no-`version` paragraph appears in **both** the class docstring and the migration docstring -- an unexplained missing `version` is indistinguishable from an AD-4 miss.
- [x] `data/repositories/insights.py` + package `__init__` + `tests/test_scoped_repository.py` lists -- scoped reads and the owner-only upsert; every claim-touching statement joins `claim` and applies `employer_scope(ctx)` -- a guard bound to the two existing modules would let this story add the unscoped query it exists to prevent.
- [x] `services/rag/insights.py` -- `InsightGenerator` Protocol, `store_insights` (upsert + one content-free `audit.record` per kind, then commit), `claim_insights` (raises `ClaimNotVisible`), `claims_needing_insights` -- the single write-owner; the Protocol is what keeps chat code out of `services/` and lets every test run without a model.
- [x] `agents/envelope.py` + `agents/tools/` -- `{ok, data, display}` and four wrappers (`reserve_check`, `next_actions`, `fraud_signals`, `similar_cases`), one service call each -- `display` carries `format_dollars` output so no layer re-formats money; note the server has no pre-existing money display envelope, this introduces it for insight content only.
- [x] `agents/schemas.py` -- one Pydantic model per kind; fraud is a discriminated union of red-flag-list and low-risk-confirmation -- the low-risk case is a *variant*, not an empty list (AC 3).
- [x] `agents/prompts/` + loader -- shared AD-16 preamble plus one file per kind, versioned, loaded by key -- the only instruction channel.
- [x] `agents/client.py` -- `ChatClient` Protocol + `chat_client(settings)` over `ChatOllama` with `with_structured_output(method="json_schema")`; `chat_request_timeout_seconds`; a chat failure raises a typed error, never leaks vendor text -- second and last reader of `ollama_base_url`.
- [x] `services/rag/client.py` -- amend the AD-5 grep-property docblock -- the claim is now false and it is load-bearing documentation.
- [x] `agents/insights.py` -- `refresh_claim_insights` and `refresh_pending_insights(limit)`; every figure comes from a tool envelope, retrieved chunks and claim narrative enter per-item delimited with source ids; returns a run summary with per-kind success/failure counts -- one orchestration used by scheduler, route, and admin trigger alike.
- [x] `api/routers/claims.py` -- the two routes; `not_generated` for missing kinds; `Cache-Control: no-store`; 404 via `_not_found`; 503 via `MODEL_UNAVAILABLE_RESPONSE` -- a missing kind is an empty state, not an error.
- [x] `api/routers/admin.py` + `api/app.py` -- e2e trigger + scheduled job -- the suite must be able to warm the cache without knowing the batch size.
- [x] `services/worklist/actions.py` -- delete the fraud seam entry -- server owns `enabled` and the sentence.
- [x] `deploy/model-stub/app.py` -- honor `format`: walk `properties`/`required`/`type`/`enum`, hash-derive scalars from schema path + prompt, return JSON in `message.content`; keep it deterministic and non-streaming -- today it ignores `format` and returns one prose sentence, so every structured parse would fail under e2e.
- [x] `web` data layer -- `queryKeys.claims.insights`, `useClaimInsights`, `useRefreshInsights`, `labels.ts`, regenerate `schema.d.ts` -- AD-9; nested key so `claims.detail` invalidation (`exact: true`) does not evict the tab.
- [x] `web/src/features/claim-detail/insights/` -- tab + four read-only cards, each with `generatedAt` via `formatNotedAt` and the model label; per-card skeleton / error / not-generated states with testids; `CardGrid` + `CaseCard` + `Kv`; fraud low-risk uses `ok`/`ok-soft` -- render into `Kv`; never reach for `EditableRow`/`InlineEditField` (FR-H-9, AD-10).
- [x] `DetailTabs.tsx` + `ClaimDetailPane.tsx` + `ActionsCard.tsx` -- wire the tab, delete the last seam, add the `fraud` navigate branch and the `NAVIGABLE_FROM_OVERVIEW` entry -- Stories 4.1 and 4.2 both shipped a broken link by missing that set.
- [x] `server/tests/test_ai_insights.py` -- **new**: the AD-2 equality test (every figure in `content` equals the service call's output), schema-rejection writes nothing, low-risk variant selection, upsert-not-duplicate, `ratio_bp is None` forbids a quoted ratio, prompts/output never logged -- the equality test is the AD-2 enforcement mechanism; treat it as mandatory.
- [x] `server/tests/test_ai_insight_scope.py` -- **new**: repository and API both refuse a foreign claim; system context can see all -- AC's scope requirement.
- [x] `server/tests/test_ai_insight_migration.py` -- **new**: table, enum, unique constraint, grants after `alembic upgrade head` -- mirrors the existing `test_*_migration.py` modules.
- [x] `server/tests/test_prompt_injection_fixtures.py` -- **new**: an injection string seeded into a claim narrative and into a knowledge chunk changes no kind, no claim, no scope, and triggers no refresh -- AD-16 requires adversarial fixtures; this story is the first code that puts claim text in a prompt.
- [x] `web` component tests -- `InsightsTab.test.tsx` (pending → skeleton, 500 → `role="alert"`, not-generated → empty state, low-risk fraud variant) and the `ActionsCard.test.tsx` amendment -- the three server-undescribable states first, per house convention.
- [x] `e2e/stories/6-2-…spec.ts` -- **new**, `@story:6-2 @epic:6`, exactly one `@smoke`: handler warms the cache via the admin trigger, opens AI Insights, four cards with timestamps render; plus not-generated empty state and the Epic-3 deep-link landing. Structure only, never prose -- AD-15 done-gate. Use an idempotent shared helper requested per test, not test ordering (6.1's `embedEverything` lesson).
- [x] `e2e/stories/3-5-…spec.ts`, `e2e/stories/2-2-…spec.ts`, `server/tests/test_action_checklist.py`, `web/src/test/api-mock.ts` -- amend the assertions this story falsifies -- AD-15: amended, never deleted.

**Acceptance Criteria:**
- Given a seed claim and a reachable model, when refresh runs, then four `ai_insight` rows exist with distinct kinds, each carrying `generated_at` and the serving model name, and every figure in `content` is byte-equal to the deterministic service output it came from.
- Given the same claim refreshed twice, when the second run completes, then the rows are replaced in place and the row count is still four.
- Given a claim with no insights, when the tab renders, then four explicit not-generated cards appear with a refresh affordance — not a 404 and not a spinner.
- Given a low-fraud-risk claim, when the fraud card renders, then it shows the low-risk confirmation in ok tokens rather than an empty red-flag list.
- Given a handler and a claim outside their book, when insights are requested, then the response is a 404 problem document indistinguishable from an unknown claim.
- Given the checklist's SIU escalation row, when the handler activates "View Fraud Indicators", then the AI Insights tab opens on that claim; the `rtw_letter` row remains disabled with its Epic-6 sentence.
- Given the model server is unreachable, when refresh runs, then no row is written, previously generated cards keep rendering their prior `generated_at`, and non-AI claim screens are unaffected.
- Given the full CI gate, when it runs, then ruff, mypy strict, pytest (including DB-backed), vitest, the `schema.d.ts` diff check, the compose port check, and the Playwright suite are all green.

## Spec Change Log

## Review Triage Log

### 2026-08-19 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 26: (high 4, medium 16, low 6)
- defer: 0
- reject: 0
- addressed_findings:
  - `[high]` `[patch]` **AD-7 scope leak.** The scheduled refresh runs under `system_context` (`scope_all`) and nothing re-scoped per claim, so `similar_claims` applied `sa.true()` and drew neighbours from every employer — and `SimilarNeighbour` persists `claim_id` and `employer_short_name`, so a scoped handler's card would name claims outside their book. The gather now runs under a new `services/rag.subject_scoped_context` bound to the *subject claim's* partition regardless of the actor's scope; the interactive `/similar` route deliberately keeps the reader's whole book. Regression test generates under the system actor after embedding the full portfolio (so a leaking build has neighbours to find) and asserts every persisted neighbour belongs to the subject's employer — confirmed failing against the pre-fix code.
  - `[high]` `[patch]` **Up to four 120-second completions inside one open transaction.** `_generate_and_write` held a savepoint across `generator.generate`, with commit deferred until all four kinds finished; `POST /admin/insight-refresh` runs `limit=None` over the whole book, so a connection could sit idle-in-transaction for minutes. Split into gather-and-narrate outside any transaction, then a short transaction around `upsert_insight` + `audit.record` with a per-kind commit (stronger isolation than the savepoint it replaces). A test asserts no transaction is open at *every* completion, not just the first.
  - `[high]` `[patch]` **A cloud-egress dependency defeated AD-5/AD-16 silently.** `langchain-ollama` pulls `langchain-core`, which hard-depends on `langsmith` (not an optional extra); its tracer activates from environment alone and exports prompt bodies and completions off-network. AD-16's containment floor is stated as "no egress path", and the existing AD-5 test greps a string and cannot see this. `config.py` now overwrites — not `setdefault`s — six `LANGSMITH_*`/`LANGCHAIN_*` tracing and OTEL switches at import, every compose profile sets them in `environment:` (which outranks `env_file:`), and a test asserts through the vendor's own `tracing_is_enabled()` *after* poisoning the environment, so it proves an operator cannot switch it back on.
  - `[high]` `[patch]` **Head-of-line blocking starved the portfolio.** Pending selection was stably ordered by claim id with a batch of five, so one deterministically-failing claim was re-selected every tick forever and no other claim was ever generated, re-spending the completions each time. Migration 0042 amended in place (unreleased, per 6.1's precedent) with `ai_insight_attempt`; selection now orders by `attempted_at NULLS FIRST`, and the attempt is recorded and committed before generating. Test drives three `limit=1` ticks against a client that refuses one claim and asserts three *different* claims were visited.
  - `[medium]` `[patch]` **The AD-16 fence was forgeable.** `source` was interpolated into the delimiter header unsanitized while only `text` was scrubbed, and `source` derives from `knowledge_chunk.source` — a column filled by an ingestion path the spine Defers. The scrubber now covers `source`, strips the two section headings as well as the fence markers, and drops DEL, C1 controls, bidi overrides and zero-width characters; the tag reduces to one line with no quote or angle bracket. `system.md` (v2) shows the delimiter and states that markers and headings are removed. Two new fixtures — a poisoned `chunk.source` and a forged section heading — both confirmed failing against the old code.
  - `[medium]` `[patch]` Configuration and database errors were reported as a model outage: a bare `except Exception` returned `unavailable=True` → HTTP 503 "Model server unavailable", swallowing `EmbeddingDimensionMismatch` (which `services/rag/embeddings.py` deliberately re-raises as permanent) plus `SQLAlchemyError` and ordinary bugs. Narrowed to `httpx.HTTPError | TimeoutError | OSError`.
  - `[medium]` `[patch]` `ValidationError` on the read path put model prose into the operational log (Pydantic v2 embeds offending input values, and the unhandled 500 is logged with its traceback) — an AD-11 breach in the module most careful about it. Now caught, logged content-free, and answered as `not_generated` for that slot only rather than 500-ing all four cards; the tab's error branch gained the Refresh that would repair the row.
  - `[medium]` `[patch]` `agents/` formatted figures the model is instructed to quote verbatim (`ratio_bp / 100:.1f`, `distance:.4f`), contradicting the envelope's own "produced once, by the service layer's formatter" rule. Both moved into service-side `display` maps.
  - `[medium]` `[patch]` Every claim edit invalidated the insights cache: ten `invalidateQueries` sites passed `claims.detail(claimId)` without `exact: true`, prefix-matching the nested insights key and defeating its deliberate `staleTime: 300_000` — while the key's own docstring asserted "nothing else invalidates it at all". Consolidated into `markCaseFileStale`, all keys exact, docstring corrected.
  - `[medium]` `[patch]` A dead model cost four timeouts: all four kinds were attempted after the first reported unavailable, so the refresh route blocked ~8 minutes before answering 503. Now breaks on first unavailability, with a call-count assertion that makes the test non-vacuous.
  - `[medium]` `[patch]` Partial success looked like success — the refresh response carried no failure count, so three-of-four written returned a clean 200 while a card still read as never generated. `failedKinds` now published and surfaced as a `role="status"` notice naming the refused kind.
  - `[medium]` `[patch]` The reserve prompt instructed on the `bills_on_file` flag rather than the verdict token, so a `light`/`closed_final` verdict with bills absent produced a narrative reading "indeterminate" beside a contradicting chip. Rewritten (v2) against the five verdict tokens, stating that bills-on-file and verdict are independent facts.
  - `[medium]` `[patch]` The insight tick held the serial job runner for minutes, stalling the embedding refresh and payment batch. `ScheduledJob.background` plus an in-flight guard; three scheduler tests added.
  - `[medium]` `[patch]` A zero-row upsert was counted as neither written nor failed, emitted no audit event, and left the claim pending forever. Now raises and counts as a failed kind.
  - `[medium]` `[patch]` The model stub could not follow `allOf`-wrapped `$ref`, which Pydantic emits for any nested model — the first narrative schema to gain one would have broken every e2e generation silently. Handled, with a test that round-trips every real narrative schema through the stub.
  - `[medium]` `[patch]` **The AD-15 gate could pass vacuously.** `test.skip(claimId === null)` over deterministic seed data, and `warmInsights` asserting `written === claims * 4` which holds as `0 === 0`. Assertions made unconditional with a non-zero claim count. The same conditional pattern in 3-5's amendment sat on a claim that never raises the SIU row, so the block never ran at all — replaced with an unconditional search across the persona's book.
  - `[medium]` `[patch]` AD-16 requires injection fixtures in the e2e suite, not only unit tests; the new spec had none. Added a test that patches a claim narrative through the real audited command, refreshes through the real stack, and asserts no kind, claim, scope or figure moved.
  - `[medium]` `[patch]` `langchain-ollama>=0.2` was unbounded and below the `method="json_schema"` floor, in a file where every other dependency carries a bound. Pinned `>=1.1,<2`.
  - `[medium]` `[patch]` `POST /claims/{id}/insights/refresh` — the route that burns four completions and writes rows — had neither its 404 nor its 503 branch tested. Both added, comparing bodies rather than status codes, and asserting the 503 leaks no URL or claim id.
  - `[medium]` `[patch]` The tab rendered a raw problem `detail`, which `api/errors.ts` warns is synthesised for envelope-less responses — a proxy timeout would have shown "The server answered 504." to a handler. Replaced with a written sentence; the test now asserts the words rather than merely that an alert node exists.
  - `[low]` `[patch]` Four assertions in the injection fixtures could not fail (the fake client is structurally incapable of emitting the forbidden strings). Replaced with assertions that the strings reach the *prompt* and that persisted figures match a live service call.
  - `[low]` `[patch]` `BULLET_CLASS` was exported and unused while its literal was hardcoded in `InsightShell`; now imported.
  - `[low]` `[patch]` `_claim_narrative`'s docstring claimed four items where it returns three.
  - `[low]` `[patch]` Vestigial `generator_deps = deps` alias removed.
  - `[low]` `[patch]` `DetailTabs` lost its exhaustiveness check when the last seam went; a seventh tab key would have silently rendered the insights panel. Restored via `panelFor` + `assertNever`.
  - `[low]` `[patch]` The `InsightKind` match had no default case, so a fifth member would raise `UnboundLocalError` swallowed as a generic failure. `assert_never` added.

**On the absence of `bad_spec`, since the rule says to prefer it when in doubt.** The scope leak (H1) is the finding with a genuine spec-level root cause: this spec told the implementer to resolve `system_context` for the scheduled job and never addressed that a cache written by an unbounded actor is read by a scoped one. It was triaged `patch` on the same reasoning Story 6.1's review recorded for its staleness race: the design shape was right, and the fix is one context-narrowing function at one call site. A loopback would have reverted ~2,750 lines of otherwise-correct, fully-tested work to re-derive it around a call that took one function, one call site and one regression test to add. The judgment is recorded here rather than left implicit.

**One sub-claim was refuted.** The invalidation finding also named two call sites that turned out to be the `financials` key rather than `detail`; nothing nests under `financials`, so prefix matching there matched only itself. They were made `exact: true` anyway for consistency.

## Design Notes

**Why the generator is an injected Protocol.** `services/rag` owns `ai_insight` (AD-12) but must not contain chat code (AD-5, layering). Story 6.1 already solved this shape for embeddings: the command takes `client: EmbeddingClient`, the concrete `OllamaEmbeddingClient` is built by a factory at the composition root. The same move here means `agents/insights.py` orchestrates and `services/rag.store_insights` persists, `services/` never imports `agents/`, and there is no runtime cycle — the call graph is composition root → `agents/` → `services/`, one direction throughout. Every test runs against a fake generator with no model server.

**The story's Task 3 said the wrappers live in `agents/tools/`, and they do.** Gathering happens in `agents/`, not inside the services command, precisely so 6.3 can promote those four functions into the registered tool registry without moving them. This story deliberately does not build the registry, the `kind` declarations, or context injection.

**The stub is the gate.** `POST /api/chat` currently ignores `format` and answers with a fixed sentence, so structured output cannot validate under e2e. Synthesizing an instance from the schema keeps the stub honest (deterministic, no model, no network) while exercising the real `with_structured_output` path. Derive scalars from schema path + prompt hash so two runs of one commit agree — the reproducibility property AD-15 rests on, and the exact issue that got the stub's dependency pins tightened in 6.1's review.

**Fraud is narration, not modeling.** `fraud_score` and `fraud_flag` are stored `Claim` columns. `services/derivations` owns two deliberately distinct thresholds — `siu_review` (referral, `siu_fraud_score_min`) and `fraud_flagged` (review, `fraud_flag_score_min`) — and they are not interchangeable. The insight reports both through the registry singletons and invents no third rule.

**`remaining_medical_cents is None` is not zero.** It means bills are not on file, and it forces `ReserveCheck.verdict` to `indeterminate` with `ratio_bp is None`. The reserve schema must make a quoted ratio impossible in that state, or the model will happily supply one.

**One less seam than the file expects.** `insights` is the last entry in `DetailTabs.SEAMS`; filling it makes `SEAMS` and `SeamPanel` dead code, which the file's own docstring anticipates. Delete them rather than leaving an empty record.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . && uv run ruff format --check .` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under strict; `agents/` is already in the strict file list.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass, DB-backed modules not skipped.
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts` -- expected: no diff after committing.
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml up -d --build --wait` -- expected: all services healthy including `model-stub`.
- `cd lineworker/e2e && npm run typecheck && npx playwright test --grep "@story:6-2\b|@story:3-5\b|@story:2-2\b"` -- expected: pass; the two amended specs must pass alongside the new one.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml config --format json > /tmp/e2e.json && python3 ../.github/scripts/check_model_ports.py /tmp/e2e.json` -- expected: pass; no new service published.
- `cd lineworker/server && grep -rl "ollama_base_url" --include=*.py .` -- expected: exactly two files — `config.py` aside, `services/rag/client.py` and `agents/client.py`.

**Manual checks (if no CLI):**
- The AI Insights tab shows four cards, each with a timestamp and model label, and no edit affordance anywhere on it.
- The SIU escalation row's "View Fraud Indicators" control is enabled and lands on the tab; the RTW letter row is still disabled with its tooltip.

## Auto Run Result

Status: done

### What was implemented

The `ai_insight` cache and the tab that reads it. `services/rag` owns the table and performs every write through one command reached by both the scheduled job and the on-demand routes; generation lives in the build's first `agents/` code — a `ChatOllama` client, four versioned prompt files, four thin `{ok, data, display}` tool wrappers over the existing deterministic services, and Pydantic schemas split into what the model may write (prose) and what is stored (copied figures + validated narrative). The React detail pane's last dashed seam is gone, replaced by four read-only cards carrying their generation timestamp and model label, and Epic 3's "View Fraud Indicators" action is enabled and lands on them.

The story's own file predated AD-16 by a day and did not bind it; this is the first code in the build to put claim narratives and retrieved chunks into a prompt, so the spec bound it and the implementation carries per-item delimiting, a standing data-not-instructions preamble, and adversarial fixtures in both the unit and e2e suites.

### Files changed

39 modified, 36 created. The shape of it:
- **Data** — migration `0042_ai_insight` (+ `ai_insight_attempt`, amended in place while unreleased), `AiInsight`/`AiInsightAttempt` models, `InsightKind`, a scoped `data/repositories/insights.py`.
- **Services** — `services/rag/insights.py` as sole writer (`store_insights`, `claim_insights`, `claims_needing_insights`, `InsightGenerator` protocol) and `subject_scoped_context` in `retrieval.py`; the fraud seam deleted from `services/worklist/actions.py`.
- **Agents (new package)** — `client.py`, `envelope.py`, `schemas.py`, `insights.py`, `tools/`, `prompts/`.
- **API** — `GET`/`POST` insight routes, an e2e-only admin trigger, a third scheduled job, four config knobs, and a tracing kill-switch at import.
- **Web** — `features/claim-detail/insights/` (tab, shell, four cards, tone map), query keys and hooks, `DetailTabs`/`ClaimDetailPane`/`ActionsCard` wiring.
- **Deploy** — the model stub taught to honour `format` and `stream`; tracing disabled in every compose profile.
- **Tests** — six new server modules, one new web component suite, one new e2e spec, plus AD-15 amendments to the 2-2 and 3-5 specs and to `test_action_checklist.py`, `test_rag_embeddings.py`, `ActionsCard.test.tsx` and `api-mock.ts`.

### Review findings

26 patched (4 high, 16 medium, 6 low); 0 deferred, 0 rejected, no spec loopback. Full detail in the Review Triage Log above. The four high findings were a cross-employer scope leak in the cached similar-case card, LLM completions held inside an open transaction, a transitively-added cloud-egress dependency with no guard, and head-of-line blocking that would have starved the portfolio behind one failing claim.

### Verification

Every gate was re-run independently after the patch pass, not accepted on report:

- `ruff check` / `ruff format --check` — clean, 241 files.
- `mypy` strict — clean, 236 source files. `langchain-ollama` needed no override and no cast.
- `pytest` with `MIGRATION_TEST_DATABASE_URL` — 2482 passed, 1 skipped. One run showed a single asyncpg `ConnectionError` in `test_meeting_seed.py`, a module this story does not touch; it passed in isolation and the full suite was clean on re-run — an environmental flake, not a regression.
- `npm run generate:api` — byte-identical across two runs; `schema.d.ts` committed.
- web `lint` / `typecheck` / `test` — 0 errors (11 pre-existing warnings, unchanged), clean, 603 passed.
- e2e — stack healthy on all four containers; `tsc` clean; **full** Playwright suite 191 passed, including the six 6-2 tests and the two amended specs.
- `check_model_ports.py` — no profile publishes a model-server port; all three profiles carry the tracing kill-switch on the `api` service.
- `ollama_base_url` readers — exactly `config.py`, `services/rag/client.py`, `agents/client.py`.
- The AD-2 figure-equality test was mutation-checked: a one-cent perturbation is invisible by construction (`format_dollars` renders whole dollars), but a $1,000 perturbation fails it at `tests/test_ai_insights.py:409`. Non-vacuous.

### Residual risks

1. **`db.rollback()` inside `_narrate`** is the one place `agents/` touches a transaction boundary `services/rag` otherwise owns. Safe today because every gather materialises into dataclasses before narrating, but a future kind holding an ORM instance across the completion would find it expired.
2. **The insight job now runs `background=True`**, so it can overlap the embedding refresh and payment batch. They write disjoint tables and an in-flight guard prevents self-overlap, but a second insight-adjacent job would need the same analysis.
3. **The subject-partition narrowing is a visible behaviour change**: a multi-employer handler's similar-case card now draws neighbours from the subject claim's employer only, not their whole book. That is the correct rule for a shared cache, but it is narrower than what the interactive `/similar` route returns.
4. **`ai_insight_attempt` was added to an unreleased migration in place.** Safe only while no persistent volume has run 0042; a dev stack with an existing volume needs `down -v`.
5. **The e2e first-warm assertion uses module state**, correct under `workers: 1, fullyParallel: false` and unreliable if parallelism within a spec file is ever enabled.
6. **Insight freshness is first-pass only** — a claim stops being pending once all four kinds exist, so an *aged* card is not regenerated on a schedule; re-narration is the on-demand path's job. Deliberate (re-narrating hourly would burn completions to replace equivalent prose), but it means a card can lag its claim until someone presses Refresh. Story 6.6 owns the honest-degradation surface around this.
