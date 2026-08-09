# Review — ARCHITECTURE-SPINE.md (lineworker)

**Reviewer:** rubric-walker
**Date:** 2026-08-07
**Artifact:** `_bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md`
**Verdict: CONDITIONAL PASS — the invariant core (AD-1..10) is genuinely strong and altitude-correct, but the spine fails checklist item 4: the operational/testing envelope is largely silent, and two AD seams would let independently built epics diverge.**

---

## Checklist walk

### 1. Fixes the real divergence points for independently built epics/features — MOSTLY YES, with gaps

What it gets right — and these are exactly the divergence points this rebuild needs:

- **Client-side logic re-accretion** (AD-1) — the single worst failure mode of rebuilding a prototype whose entire brain is one `<script>` block. Correctly elevated to AD #1.
- **LLM vs deterministic math** (AD-2) — with financial figures and a copilot in the same product, "who computes the dollar amount" is the sharpest seam between the copilot epic and the financials epic. Named and fixed.
- **Store sprawl** (AD-3) — pre-empts the classic "RAG epic brings its own vector DB, copilot epic brings its own chat store" drift. Explicitly folds LangGraph checkpoints and audit into one Postgres. Good.
- **Audit path** (AD-4) — every write-bearing epic (inline edits, worklist approvals, diary, agent writes) would otherwise invent its own audit approach or none.
- **RBAC location** (AD-7) — directly targets the prototype's client-side `HANDLER_MAP`, the most dangerous thing to port faithfully. Server-side dependency injection of scope is the right enforcement point.
- **Rules-engine boundary** (AD-8), **frontend state idiom** (AD-9), **derived-field drift** (AD-10) — all real, all seams between independently built features.
- The Capability → Architecture Map is a genuinely useful device: every FR family is placed in a module and bound to ADs, so an epic author can look up their lane.

Gaps (real divergence points missed — detailed under items 3–4):

- **Testing strategy** — nothing. The only test-shaped word in the document is "property-tested" inside AD-8. Two epics will pick pytest-vs-something, vitest-vs-jest, different fixture/DB-test strategies, different definitions of "done". For independently built features this is a first-order divergence axis, and the spine altitude owns it.
- **The AD-2 / AD-8 overlap on priority and SLA** (see item 2) is itself an unfixed divergence point.
- **Binary storage seam** (see item 3).
- **Pagination/filtering convention for list endpoints** — the queue, worklist, dashboard drill-down, and audit views all return lists; nothing fixes cursor-vs-offset, filter param shape, or sort param shape. Low, but three epics will invent three answers.

### 2. Every AD's Rule is enforceable and prevents its stated divergence — 8 of 10 clean

- **AD-1, AD-3, AD-4, AD-5, AD-6, AD-7, AD-9, AD-10** — all pass. Each rule names a checkable condition ("no router or agent touches a SQLAlchemy session for writes", "the Ollama port is never published", "no endpoint accepts a caller-supplied scope", "never client- or user-writable") and each condition, if held, actually prevents the stated divergence. AD-1's "if a number appears on screen, a service computed it" is a model of an enforceable litmus test. AD-10's cache exception is well-bounded (refresh owned by the computing service).
- **AD-2 vs AD-8 — seam conflict.** AD-2: `priorityScore` and "SLA aggregation exist exactly once, in `services/financials`" as code. AD-8: "priority weights, reserve bands, SLA targets" are ZEN JDM documents. Both can be true (Python skeleton reads JDM-supplied tunables), but the spine never says so, and the Capability Map binds the queue to *both* ADs without resolving which half of the computation lives where. Two teams reading this will split code/JDM differently — e.g. one puts the whole priority formula in JDM, another hardcodes weights in Python. This is precisely the "same rule forked between config and code" divergence AD-8 claims to prevent. Needs one sentence: *formula shape is Python; its named coefficients/thresholds are JDM inputs* (or whatever the intended split is).
- **AD-8 classification test is example-driven, not criterial.** "Business-tunable" vs "statutory" is illustrated but no decision rule is given for a new borderline rule (is a reserve-band change tunable? is a state waiting-period statutory data or JDM?). Minor, but the AD's whole job is to make two teams classify identically.

### 3. Nothing under Deferred could let two units diverge — TWO EXCEPTIONS

- **Binary storage for documents/photos — divergence risk.** FR-scoped features (documents tab, photo evidence) must accept and serve bytes from day one; "only metadata schema is fixed" does not tell the documents epic and the photos epic where interim bytes go or through what interface. Two epics can plausibly ship one-filesystem-volume and one-MinIO-shim implementation. Fix: keep the *backend* deferred but fix a storage-service interface now (a `services/` blob port that both features must call), so the deferral is swap-safe like the identity-provider one.
- **State statutory coverage** — mild. If the benefit-calc epic ships before jurisdictions are chosen, at minimum the seed set for dev/test must be named or the epic stalls/invents its own. Low severity.
- The rest are clean: identity provider is deferred *with* a swappable-dependency convention holding the seam; analyst depth is deferred with "aggregates scope-aware from day one" holding it; email egress, model pinning (config per AD-5), K8s, and fraud scoring are all genuinely deferrable without inter-unit divergence. This is how deferrals should be written — most entries name the thing that keeps the seam safe meanwhile.

### 4. Every owned dimension decided/deferred/open — FAILS on the operational envelope

This is the spine's biggest hole, and it is the dimension the checklist flags as the one most often left silent.

- **Backups — one word, zero decisions.** The entire treatment is "`prod` (Compose, GPU, TLS, backups)". No tool (pg_dump? WAL archiving? pgBackRest?), no RPO/RTO, no ownership, no restore-test expectation, and — for a single on-prem Docker host holding HIPAA-adjacent PHI in *one* Postgres (AD-3 concentrates everything there, which raises the stakes) — no off-host copy decision. Not decided, not in Deferred, not an open question. Silent.
- **Monitoring/observability — silent.** Logging convention exists (structlog, good), but nothing on health checks (Compose healthchecks? readiness for Ollama model load?), metrics, alerting, or even "we accept none at this scale". A one-line deliberate decision would satisfy the checklist; silence does not.
- **Secrets management & encryption at rest — silent, and this is PHI.** "12-factor env vars" covers config, but DB credentials in Compose env files, TLS cert handling, and disk-level encryption for the Postgres volume are never mentioned. Compliance row in the Capability Map cites pgaudit but nothing covers data-at-rest. For HIPAA-adjacent data this belongs in an AD or an explicit deferral with a deadline (like the identity-provider entry has: "decide before first deployment outside dev").
- **Testing & CI — silent** (also cited under item 1). No test conventions, no CI gate, no "how a feature proves it didn't break another feature" story. Independently built epics with no shared verification convention is a divergence generator.
- **Data seeding/migration** — the ~100 synthetic claims from the prototype/Excel are the obvious dev dataset; who converts them and where the seed lives is unstated. Low.
- Also noted: the frontmatter binds NFR-1..4 but only NFR-1 appears in the Capability Map; NFR-2..4 are never visibly discharged.

What *is* covered environmentally: dev/prod environments, single-host Compose, ingress topology, internal-only ports, GPU strategy, TLS at nginx. So the envelope is half-drawn — deployment shape yes; operations no.

### 5. Diagrams valid mermaid with real structure — PASS

- **Layer diagram** (`graph TD`): valid syntax (quoted labels, cylinder `PG[(...)]`, piped edge labels). Carries real structure — it distinguishes API→AGENT invocation from AGENT→SVC tool access, which is the load-bearing subtlety of AD-6/AD-2, and the prose dependency rule matches the arrows.
- **Container diagram** (`graph LR` + subgraph): valid; quoted edge label `"http (internal net only)"` is correctly quoted; chained `Browser -->|HTTPS| web --> api` is legal. Encodes the actual security topology (sole ingress, internal-only Ollama/PG).
- **ERD**: valid `erDiagram` syntax; 20 entities with plausible cardinalities matching the prototype's data model (bills, schedule weeks, timeline, injuries, embeddings, audit). One row uses an empty relationship label (`: ""`) — legal in current mermaid but worth giving a real label (`covers`) for older renderers. Minor content nit: no relationship ties CLAIM to HANDLER except via employer assignment — consistent with AD-7's scoping model, so arguably deliberate, but AUDIT_EVENT }o--|| HANDLER implies only handlers act; supervisors/analysts also generate audit events. Low.

### 6. Seed is minimal — MOSTLY, two nits

- Nothing in the seed usurps an AD: source tree, ERD names, and deployment view are all skeleton, not policy. Conversely no AD is mere seed — every AD carries a Prevents that names a real divergence. The Conventions table is correctly conventions (naming, money-as-cents, RFC 9457, OpenAPI-generated TS types — all good picks that kill whole classes of drift).
- **Nit 1 — "latest" is not a version.** Three stack rows (Ollama "latest server", ZEN "latest MIT release", assistant-ui "latest") defeat the pin: two epics installing a month apart get different software. Pin or move to the model-pinning-style Deferred entry with a decide-by trigger.
- **Nit 2 — operational policy smuggled into seed prose.** "TLS, backups" and "Ollama and Postgres ports are internal-only" live in seed captions. The port rule is fine (restates AD-5 + one addition), but "backups" as a seed caption word is where a missing decision is hiding (see item 4).

---

## Findings summary

| # | Severity | Finding |
| --- | --- | --- |
| F1 | **High** | Operational envelope silent: backups (one word, no tool/RPO/ownership/off-host decision for single-host PHI Postgres), monitoring/health/alerting, secrets & encryption-at-rest — none decided, deferred, or opened (checklist item 4). |
| F2 | **High** | Testing/CI strategy entirely absent — no test conventions or verification gate for independently built epics; a first-order divergence axis at this altitude. |
| F3 | **Medium** | AD-2/AD-8 seam unresolved: `priorityScore`/SLA claimed by both "exactly once in `services/financials` (code)" and "ZEN JDM tunables"; no rule states the code-vs-JDM split, so teams will fork it — the exact divergence AD-8 exists to prevent. |
| F4 | **Medium** | Deferred binary storage lacks a fixed abstraction: documents and photos epics need byte storage now; without a mandated blob-service interface the deferral is not swap-safe (unlike the identity-provider deferral, which is). |
| F5 | **Low** | Stack rows pinned to "latest" (Ollama, ZEN, assistant-ui); NFR-2..4 bound in frontmatter but never discharged in the map; ERD empty relationship label and AUDIT_EVENT→HANDLER-only actor. |

## Recommended minimal fixes

1. Add one AD (or extend Conventions + Deferred) covering ops: backup mechanism + restore expectation + off-host copy, health checks, and a deliberate "no metrics/alerting at single-site scale" decision if that's the intent; add secrets/at-rest-encryption with a "decide before first deployment outside dev" trigger like the IdP entry.
2. Add a Testing convention row (or AD): frameworks, DB-test strategy, and the CI gate every epic must pass.
3. One sentence resolving F3: e.g. "Computation shape is Python in `services/financials`; named weights/targets/bands are JDM inputs fetched via `rules/` — JDM never contains formulas, Python never hardcodes tunables."
4. In the binary-storage deferral, fix a `services/` blob interface now; defer only the backend.
5. Pin the three "latest" rows or move them into Deferred with decide-by triggers.

The AD core is above the bar — Prevents/Rule discipline is real, the Capability Map closes the loop to requirements, and the deferrals are mostly written with seam-holding conventions. Close F1–F4 and this is a passing spine.
