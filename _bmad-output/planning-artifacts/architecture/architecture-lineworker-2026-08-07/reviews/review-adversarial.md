# Adversarial Review — ARCHITECTURE-SPINE.md (lineworker)

**Reviewed:** `_bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md`
**Date:** 2026-08-07
**Method:** For each finding, two concrete units one level below the spine are constructed that each obey every AD and convention *to the letter* yet build incompatible artifacts. Every such pair is a hole the spine must close with a new or tightened AD.

**Verdict: The spine is strong on layer discipline and provenance (AD-1..8 kill the prototype's worst habits) but it governs *layers*, not *seams*. Ten letter-compliant collisions found; four would ship defects a demo would catch, one leaks PHI across handler scopes.**

---

## F1 — CRITICAL — RBAC scope does not propagate into the agent/RAG call path

**Pair:** `agents/` builder (LangGraph tools) × `services/rag` builder (pgvector similar-case search).

**The two letter-compliant builds:**
- The `agents/` builder implements the `similar_cases` and `get_claim` tools calling `services/` with `claim_id` from graph state. Fully compliant: AD-6 (one graph, checkpointed per `(claim_id, user_id)`), the dependency rule ("`agents/` may call `services/` … never `data/` directly"), and AD-7's literal text — which binds only "*every list/detail/aggregate endpoint*" and repository queries reached via "*a FastAPI dependency*". Agent tool calls are not endpoints and do not pass through FastAPI dependencies.
- The `services/rag` builder implements similar-case vector search over **all** `claim_embedding` rows, because the only scope-injection mechanism the spine names (the FastAPI dependency of AD-7) is absent on this call path, and "No endpoint accepts a caller-supplied scope" reads as *forbidding* a scope parameter on the service function.

**Incompatibility / failure:** Handler A asks the copilot "show me similar cases"; the answer narrates claims from employers Handler A is not assigned to. AD-2 is satisfied (figures came from tool output), AD-5 is satisfied (all local), AD-7 is satisfied on its letter — and PHI crosses the `handler_employer_assignment` boundary anyway. The same hole applies to the copilot's `get_claim` tool: `claim_id` in graph state came from an earlier session; nothing rechecks that `user_id` still holds scope over it at tool-execution time.

**Failing AD:** AD-7 scopes its rule to endpoints + a FastAPI dependency; the agent runtime is explicitly *not* behind FastAPI dependencies (it's a peer consumer of `services/`).

**Fix (tighten AD-7):** Scope is a required, non-defaultable parameter of every repository *constructor/query*, not a property of the HTTP layer: "Every repository method requires an `AccessScope` value object; there is no unscoped read path. HTTP resolves scope via the auth dependency; the LangGraph graph resolves it from `user_id` in checkpointed state at the top of every tool node, using the same `resolve_scope(user)` function. `services/rag` vector queries filter by the scope's employer set in SQL, not post-hoc."

---

## F2 — HIGH — Two command owners of `PAYMENT_SCHEDULE_WEEK` (financials generates, worklist approves)

**Pair:** `services/financials` builder × `services/worklist` builder.

**The two letter-compliant builds:**
- Per AD-2, `services/financials` owns "payment-schedule generation" — it implements `regenerate_schedule(claim)` as an audited command (AD-4) that deletes and re-creates future `payment_schedule_week` rows whenever `weekly_wage`, comp rate, or duration changes via inline edit.
- Per the capability map, "Action worklist + approvals (FR-H-5, Excel rows 29/64–66)" live in "`services/worklist` commands" governed by AD-4/AD-8 — so worklist implements `approve_payment(week_id)` as an audited command flipping a week's status to `Payment Scheduled`/`Paid`.

**Incompatibility / failure:** Both services legitimately mutate the same table through their own AD-4 command functions. A handler approves week 12; minutes later an inline wage edit triggers financials' regeneration, which drops and re-creates week 12 — the approval (and its audit trail's referent) is silently clobbered. Reverse ordering deadlocks differently: worklist marks weeks `Paid` that financials treats as immutable history, so regeneration logic forks per team. AD-4 audits both writes faithfully; it never says one entity has one command owner.

**Failing AD:** AD-4 governs *how* writes happen (command + audit) but not *who may own the write path to an entity*. The capability map actively assigns two owners.

**Fix (new AD):** "**Single command owner per aggregate.** Each entity belongs to exactly one service that exposes its command functions; all other services (and agents) invoke that owner's commands. `PAYMENT_SCHEDULE_WEEK` belongs to `services/financials`; `services/worklist` approval calls `financials.approve_week(...)`. Regeneration must preserve terminal-status weeks (`Paid`, `Payment Scheduled`) by contract." Add an "Owner" column to the ERD or capability map.

---

## F3 — HIGH — AD-10's "in services (or by ZEN)" lets queue and claim-detail compute the same derived flag differently

**Pair:** `services/worklist` builder (queue cards, dashboard aggregates) × `services/claims` builder (claim-detail pane).

**The two letter-compliant builds:**
- Worklist needs `payment_due`, `rtw_blocked`, `risk` for `priorityScore` ordering and supervisor KPI counts. Governed by AD-8 + AD-10, it implements them as **ZEN JDM rules** (they're business-tunable thresholds — exactly what AD-8 assigns to ZEN), e.g. `payment_due := next unpaid week within 5 business days`.
- Claims-detail needs the same flags for the stage-adaptive header badges. Governed by AD-10's equally valid branch — "computed at read time **in services**" — it implements them as Python in `services/claims`, e.g. `payment_due := next unpaid week within 7 calendar days` (the prototype's `forEach` derivation, faithfully ported).

**Incompatibility / failure:** The queue card shows a red "payment due" chip; the open detail pane for the *same claim* shows none. The supervisor KPI "claims with payment due: 14" doesn't match what handlers see when they click through. Both builds satisfy AD-10 to the letter (computed at read time, never stored, never client-writable) and AD-8 (each team can argue its classification). AD-10's caching clause even *presupposes* "the same service that computes it" — quietly assuming a single computer that no rule mandates. This is the spine's own stated fear in AD-2 ("two divergent implementations of the same rule") recurring one layer down, between two services instead of service-vs-prompt.

**Failing AD:** AD-10 ("in services (or by ZEN)" — unassigned owner, unassigned mechanism); AD-8's ZEN/Python boundary is defined by *kind of rule*, not by *field*, so both teams can claim their choice.

**Fix (tighten AD-10):** "Every derived field named here has exactly one computing owner and one mechanism, listed in a **derivation registry** (field → owning service → Python|ZEN JDM doc id). `days_open`, `payment_due`, `rtw_blocked`, `siu_review`, `risk` → `services/worklist` via ZEN; `total_paid`, `total_incurred` → `services/financials` via Python. Any endpoint that returns a derived field obtains it from the owner; re-derivation elsewhere is a build error."

---

## F4 — HIGH — AD-9's "optimistic updates for inline edits" collides with AD-1/AD-10 server-only derivations, and no cross-feature invalidation contract exists

**Pair:** `web/features/claim-detail` team × `web/features/queue` + `web/features/dashboard` teams.

**The two letter-compliant builds:**
- Claim-detail, obeying AD-9 verbatim ("optimistic updates for inline edits"), optimistically writes the edited `weeklyWage` into its `['claim', claimId]` cache and, obeying AD-1 (the SPA computes nothing), leaves the derived `benefitAmount`, `risk`, and reserve-check banner **stale** until the mutation settles and invalidates `['claim', claimId]` — which is the only key it knows about.
- Queue and dashboard, also obeying AD-9 ("mutations invalidate"), cache `['worklist', filters]` and `['dashboard', 'kpis', scope]`. They own no mutations, so they invalidate nothing, and nothing invalidates them: the detail team cannot be expected to know the dashboard's key shapes, and no convention publishes them.

**Incompatibility / failure:** Three visible-simultaneously surfaces show three states of one claim: detail shows new wage + stale derived benefit (a *mathematically inconsistent pair on one screen*, precisely the drift AD-10 exists to prevent — now reproduced in the client cache); the queue card keeps the old `priorityScore` position; the supervisor dashboard (which "aggregates the same data handlers edit") shows pre-edit reserves until an arbitrary staleTime expires. Second-order case: the optimistic wage passes locally but the server's ZEN reserve check rejects it with a 422 problem+json — AD-9's rollback behavior and the error convention's "status → toast/inline" mapping are both unspecified, so each feature invents its own.

**Failing AD:** AD-9 mandates two idioms (optimistic + invalidate) without a query-key taxonomy, a mutation→invalidation matrix, or a rule for optimistic display of server-derived fields.

**Fix (tighten AD-9):** "(a) Query keys follow a published registry: `['claims','list',scope,filters]`, `['claims','detail',claimId]`, `['dashboard',metric,scope]` — defined once in `web/src/api/keys.ts` beside the generated client. (b) Every mutation hook declares its invalidation set in the same registry; an inline claim edit invalidates `['claims','detail',claimId]` **and** `['claims','list']` **and** `['dashboard']` prefixes. (c) Optimistic updates apply to the edited field only; derived fields render a pending state (never a stale number) until refetch — a derived value on screen is either server-fresh or visibly loading. (d) 409/422 problem+json rolls back the optimistic value and renders `problem.detail` inline at the edited field."

---

## F5 — HIGH — No list-endpoint convention: queue and analyst drill-down ship incompatible pagination/filter/sort idioms

**Pair:** `api/` + `services/worklist` (handler queue endpoints) × `api/` + dashboard/analyst endpoints (FR-AN-1..6 "built scope-aware from day one so it bolts on").

**The two letter-compliant builds:**
- Queue team: `GET /api/v1/claims?stage=treatment&sort=-priorityScore&offset=0&limit=50` returning a bare JSON array (the queue is ~30 claims per handler; they see no need for an envelope). REST resource route ✓, camelCase ✓, OpenAPI ✓.
- Analyst team: `GET /api/v1/claims?filter.stage=treatment&filter.employer=EMP-3&cursor=eyJp...&pageSize=100` returning `{items, nextCursor, totalCount}` because drill-down/export needs stable cursors and counts. REST ✓, camelCase ✓, OpenAPI ✓.

**Incompatibility / failure:** Two shapes for "list of claims" in one generated TS client; TanStack Query infinite-scroll hooks can't be shared; `totalCount` exists on one and not the other so the dashboard's "showing 50 of 1,204" widget can't sit on the queue endpoint; filter grammar (`stage=` vs `filter.stage=`) forks the URL-state code in `web/`. The API-style convention row specifies route shape and SSE only — silence on pagination, filtering, sorting, and response envelope is a fork license.

**Failing convention:** "API style" row.

**Fix (extend the convention):** "All collection endpoints: cursor pagination (`cursor`, `limit` ≤ 200), envelope `{items, nextCursor, totalCount?}`, sorting `sort=-field,field`, filtering as flat query params named after the camelCase field. One shared FastAPI dependency + one shared Pydantic generic implement this; hand-rolled list responses fail review."

---

## F6 — MEDIUM-HIGH — `TIMELINE_EVENT` has three writers and no vocabulary

**Pair:** `services/claims` builder × `services/financials` / `services/worklist` builders (and prospectively `agents/` via approved writes).

**The two letter-compliant builds:**
- `services/claims` writes timeline events on stage transitions and injury additions with `{event_type: "STAGE_CHANGE", payload: {from, to}}` — types SCREAMING_SNAKE, payload a free JSON blob.
- `services/worklist`/`financials` write events for approvals and schedule generation with `{event_type: "payment.approved", payload: {"weekNo": 12, "amountCents": 84500}}` — dotted lowercase types, camelCase payload keys (arguing the payload is "API JSON").

**Incompatibility / failure:** Every write is a compliant AD-4 command with a compliant audit row — AD-4 governs the audit trail, not the domain timeline. The claim-detail timeline tab must now render N ad-hoc payload shapes; filtering "show payment events" requires knowing every team's type spelling; the enum convention explicitly covers only `stage` and Excel item statuses, so `event_type` is conventionally unowned.

**Failing AD/convention:** AD-4 (write mechanics only, no entity ownership — same root cause as F2); Enums convention (scope too narrow).

**Fix:** Fold into F2's single-owner AD: `TIMELINE_EVENT` is owned by `services/claims`, which exposes `record_event(claim, type: TimelineEventType, payload: TypedModel)` where `TimelineEventType` is a Postgres enum in the shared generated TS types, and each type has a versioned Pydantic payload model. Other services call the owner.

---

## F7 — MEDIUM — Cents convention stops at the ZEN boundary; rounding for statutory math unspecified

**Pair:** `rules/` builder (ZEN wrapper + JDM authoring) × `services/financials` builder (typed statutory Python).

**The two letter-compliant builds:**
- Business analysts author JDM reserve-band documents in dollars (`incurred > 50000 → band C`) — natural for the decision-table UI, and the "Money as integer cents in **DB and API**" convention says nothing about the rules-engine input context, which is neither DB nor API.
- The `rules/` wrapper passes claim context to ZEN exactly as services hold it: integer cents. `50000` now means $500.00; every band threshold is off by 100×. Separately, `services/financials` computes 66.67% of AWW in cents and must round — banker's rounding vs half-up vs round-at-weekly vs round-at-total is unspecified, so the property-tested Python and the Excel spec's figures disagree by cents, and reserve totals drift from summed schedule rows.

**Incompatibility / failure:** Silent 100× misclassification in ZEN outcomes (priority weights, reserve bands, SLA targets are all AD-8 tunables fed by money fields); penny drift between `total_incurred` (financials) and dashboard sums (worklist aggregates) that AD-10 says must never diverge.

**Failing convention/AD:** "Dates & money" row (scope: DB and API only); AD-8 (no I/O contract for JDM context).

**Fix (tighten conventions + AD-8):** "Money is integer cents in DB, API, **and every ZEN evaluation context**; JDM documents are authored and validated against a published JSON Schema for their input context, with cents-suffixed field names (`incurredCents`). Statutory rounding: round half-up to the cent at each weekly benefit; totals are sums of rounded weeks. Property tests pin both."

---

## F8 — MEDIUM — AD-4's audit event has named fields but no schema: `before/after` and `resource` diverge per service

**Pair:** `services/claims` commands × `services/financials` commands (consumed by `services/audit` + compliance reporting, NFR-1).

**The two letter-compliant builds:**
- Claims commands log `before`/`after` as full-entity snake_case snapshots and `resource` as the claim business ID (`WC-1042`) — reasonable, since the ID convention says claims are exposed by business ID.
- Financials commands log field-level diffs `{field, old, new}` in camelCase ("it mirrors the API") and `resource` as `payment_schedule_week:8731` (surrogate — the ID convention says "surrogate ids elsewhere").

**Incompatibility / failure:** Both satisfy AD-4's literal list "(user, role, action, resource, before/after, timestamp)". The compliance query "show every change to claim WC-1042" cannot join across the two shapes; PHI-in-audit exposure differs (full snapshots duplicate PHI broadly; diffs don't); retention/redaction policy can't be applied uniformly.

**Failing AD:** AD-4 (field names without types/shape, no owner of the schema).

**Fix (tighten AD-4):** "`services/audit` owns a single `AuditEvent` Pydantic model and the only `emit()` API: `resource = (entity_type, surrogate_id, business_id?)`, `changes = [{field, before, after}]` in DB snake_case, PHI fields tagged for redaction. Command functions construct events only through it."

---

## F9 — MEDIUM — Postgres enum *values* vs camelCase JSON: `Payment Scheduled` has three legal spellings

**Pair:** `data/` + `api/` builder (Pydantic serialization) × `web/` builder (generated TS types + UI matching).

**The two letter-compliant builds:**
- Backend A serializes enum values verbatim from the Excel-mandated Postgres enums: `"status": "Payment Scheduled"` (the enum convention says statuses are "per Excel"). Backend B on the next router applies the naming convention's "API JSON … camelCase (Pydantic alias generator)" to values as well as keys: `"paymentScheduled"`.
- The web team's badge/color map and filter chips are written against whichever spelling appeared first in the generated types; the other router's responses fall through to the "unknown status" branch.

**Incompatibility / failure:** Two conventions genuinely overlap: "per Excel" values contain spaces and title case; "camelCase" governs API JSON. Nothing states that the alias generator applies to *keys only* and enum *values* pass through verbatim (or the reverse). Filter query params (`?status=Payment%20Scheduled` vs `?status=paymentScheduled`) fork with it — compounding F5.

**Failing conventions:** Naming row × Enums row (unresolved intersection).

**Fix:** One sentence in the Enums row: "Enum *values* are SCREAMING_SNAKE machine codes (`PAYMENT_SCHEDULED`) in DB, API, and generated TS; Excel display strings are a UI label map shipped in the shared types file. camelCase applies to JSON keys only."

---

## F10 — MEDIUM — Nobody owns embedding refresh: inline edits silently stale the RAG index

**Pair:** `services/claims` builder (inline-edit commands) × `services/rag` builder (pgvector search over `CLAIM_EMBEDDING`).

**The two letter-compliant builds:**
- Claims commands mutate claim fields + audit (AD-4 complete). Re-embedding is not their concern — AD-2/AD-6 put AI behind `agents/` and `services/rag`, and touching Ollama from `services/claims` feels like a layer smell.
- `services/rag` reads embeddings at query time and owns similarity search. It never observes claim mutations — no event bus exists in the spine, and polling for dirty rows is somebody's job, but the capability map row "Similar-case + labor-law RAG" names storage (AD-3) and locality (AD-5) only.

**Incompatibility / failure:** Claim narrative is edited (injury description corrected from "wrist" to "crush injury, hand"); its embedding still encodes the old text; copilot "similar cases" answers are confidently wrong in a way AD-2 does not catch (the *retrieval* is wrong, not the arithmetic). Same gap for `KNOWLEDGE_CHUNK` re-indexing cadence. AD-10's spirit — "a derived value may be cached only if refresh is owned by the same service that computes it" — is the exact principle, but AD-10's binds list never mentions embeddings, so neither team is bound.

**Failing AD:** AD-10 (embeddings are a cached derivation but are outside its binds); no eventing/refresh mechanism anywhere in the spine.

**Fix (extend AD-10 binds + one mechanism sentence):** "`claim_embedding` rows are derived data owned by `services/rag`; AD-4 command functions that touch embedded source fields enqueue a re-embed (transactional outbox table in the same PostgreSQL, drained by an api-container worker). Search results include `embedded_at`; the copilot's similar-case answer discloses staleness beyond a configured threshold."

---

## Aggregate diagnosis

Three root causes generate all ten pairs:

1. **Write governance without write ownership.** AD-4 says *how* to mutate, never *who may* mutate an entity → F2, F6, F8.
2. **Computation placement with an "or".** AD-10's "in services (or by ZEN)" and AD-8's kind-based split leave every derived field claimable by two teams → F3, F7, F10.
3. **Contracts defined for the HTTP seam only.** AD-7 and the conventions bind endpoints; the agent→service seam, the ZEN context seam, and the client-cache seam are unbound → F1, F4, F5, F9.

Recommended new ADs: **AD-11 Single command owner per aggregate** (F2, F6), **AD-12 Derivation registry: one owner + one mechanism per derived field, embeddings included** (F3, F10), **AD-13 Collection-endpoint contract** (F5), plus tightenings to AD-4 (audit schema, F8), AD-7 (scope as repository-level value object, F1), AD-9 (query-key registry + optimistic rules, F4), and the money/enum convention rows (F7, F9).
