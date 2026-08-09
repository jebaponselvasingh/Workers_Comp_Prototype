# Security & Compliance Review — ARCHITECTURE-SPINE.md (LINEWORKER)

- **Reviewer lens:** Does the spine fix the security/compliance invariants that independently built units could otherwise diverge on? (HIPAA-adjacent PHI: diagnoses, ICD-10, wages, disability status; US workers' comp insurance.)
- **Document reviewed:** `_bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md` (draft, 2026-08-07)
- **Date:** 2026-08-07
- **Verdict:** **CONDITIONAL — not ready to bind.** The spine fixes the *architectural* invariants well (server-authoritative logic, audited writes, one datastore, local-only inference) but is missing the *data-protection* invariants almost entirely. Every gap below is a place where two independently built units will make different, incompatible compliance choices.

---

## What the spine gets right (keep as-is)

These genuinely prevent divergence and are at the correct altitude:

- **AD-4 (audited command writes)** — audit event in the same transaction as the mutation, no direct session writes from routers/agents. This is the single most important compliance invariant in the document and it is stated crisply.
- **AD-3 (one PostgreSQL)** — explicitly motivated by avoiding "a second backup/security/compliance domain." Correct instinct; the gaps below are where that promise isn't followed through (Ollama state, backups, binary storage).
- **AD-5 (local-only AI)** — closes the prototype's credential-less `fetch` to api.anthropic.com and bans cloud fallback code paths outright. "There is no cloud-API code path, including fallbacks" is exactly the kind of sentence a spine should contain.
- **AD-7 (server-authoritative RBAC)** + Auth convention ("JWT/session never carries claim scope") — kills the client-side `HANDLER_MAP` failure mode and prevents scope-in-token drift.
- **AD-6 (human-gated agent writes routed through AD-4)** — AI mutations inherit the audit invariant rather than getting their own path.
- **Logging convention** partially: "audit events are DB rows, not log lines" correctly separates the audit record from operational logs.

---

## Findings

### F-1 [CRITICAL] — No encryption-at-rest invariant anywhere

The word "encryption" does not appear in the document. TLS terminates at nginx (transit at the ingress: covered), but there is **no rule for data at rest**: the PostgreSQL volume (all PHI, chat history, audit log per AD-3), the deferred document/photo binary store, Docker volumes generally, and backups. On a single on-prem Docker host this is precisely the kind of thing each deployment/unit will decide differently (LUKS? pgcrypto? nothing?), and it is a baseline HIPAA Security Rule expectation for ePHI.

**Fix at spine altitude:** one invariant — *all persistent stores that can contain PHI (Postgres volume, backups, document/photo storage when decided, and any AI-sidecar state) are encrypted at rest; the mechanism (volume-level vs storage-level) is deployment config, the requirement is not.* The deferred binary-storage decision should inherit this invariant explicitly so it can't be decided later without it.

### F-2 [HIGH] — LangGraph checkpoints / chat history are PHI but are not governed as PHI

AD-3 co-locates checkpointer tables in Postgres (good — one backup/security domain), and AD-6 keys them per `(claim_id, user_id)`. But co-location is not classification. Copilot conversations will quote diagnoses, ICD-10 codes, wages, fraud indicators — the checkpoint tables **are** a PHI store, yet no invariant says they are subject to the same access control (AD-7 scoping — can a handler read another handler's chat about a claim they lost access to?), retention, or purge as claim data. Prompts sent to Ollama, and RAG context stuffed into them, are the same data in flight. Nothing prevents one team treating checkpoints as disposable framework plumbing while another treats them as records.

**Fix at spine altitude:** an invariant stating *chat/checkpoint/prompt content is PHI: scoped by AD-7 at read time, included in retention/purge (F-3), never exported or logged outside AD-3's datastore.*

### F-3 [HIGH] — No data-retention / purge posture at all

Nothing in the spine (including Deferred) addresses retention or deletion. Workers' comp claims carry long statutory retention obligations (state-variable, often claim-closure + N years), and any purge/erasure event must cascade coherently across the derived-PHI stores the spine itself creates: `CLAIM_EMBEDDING` pgvector rows (embeddings of PHI are PHI), LangGraph checkpoints (F-2), timeline events, documents/photos, and backups. Independently built units will diverge immediately on soft-delete vs hard-delete, and nobody will own the cascade.

**Fix at spine altitude:** one invariant: *deletion/retention is a service-layer command (per AD-4, itself audited) that owns the cascade to embeddings, checkpoints, and binaries; no unit implements its own delete.* The retention *schedule* (per-state, per-record-type) can be deferred like the statutory-rate question — the *mechanism and single ownership* cannot. Add it to Deferred at minimum with an owner, like `state_rate_schedule` refresh has.

### F-4 [HIGH] — Audit log has no immutability or retention rule

AD-4 mandates *emitting* audit events but nothing prevents *updating or deleting* them. Since audit rows live in the same Postgres instance the application writes to (AD-3), any component with DB write access can, absent a rule, alter history — and an auditable-integrity claim (NFR-1, §7.3 per the capability map) rests on it. pgaudit appears in the deployment diagram but its relationship to the application audit table is unstated.

**Fix at spine altitude:** *audit_event is append-only (no UPDATE/DELETE grants to the application role; enforced at the DB, not by convention) and has a defined retention floor.* One sentence added to AD-4 closes this.

### F-5 [HIGH] — No "no PHI in application logs" invariant

The logging convention specifies structlog JSON and correctly keeps audit out of log lines — but says nothing about what *may* appear in operational logs. Default behavior across independently built routers, services, and agent nodes (LangGraph verbose tracing is notoriously chatty) will be to log request bodies, tool inputs/outputs, and prompt content — i.e., PHI in plaintext files outside AD-3's "one compliance domain," unencrypted, with unmanaged retention. This is a classic divergence point: each unit decides its own log verbosity.

**Fix at spine altitude:** add to the Logging convention: *logs carry identifiers (claim business ID, surrogate ids) and event metadata only — never medical, wage, or free-text claim content; LLM prompt/response bodies are never written to logs or traces.*

### F-6 [MEDIUM] — Ollama internal-only rule covers the network, not the sidecar's own state or access

AD-5 is necessary but not sufficient. Two gaps at spine altitude: (a) the Ollama server keeps its own state (model files, and depending on configuration, request logs / debug output containing full prompts) in a container volume — an ungoverned PHI-adjacent store that contradicts AD-3's single-compliance-domain rationale; (b) the internal API is unauthenticated, so *anything* on the internal Docker network can submit or replay inference — fine today with three containers, a silent assumption once any other service joins the network.

**Fix at spine altitude:** extend AD-5: *Ollama is stateless with respect to request content (prompt logging disabled; its volume holds model weights only) and is reachable only from the `api` container.* Implementation (network policy vs compose config) stays out of the spine.

### F-7 [MEDIUM] — Backups named but ungoverned

"prod (Compose, GPU, TLS, backups)" is the entire backup posture. Backups of a PHI database inherit every obligation of the live data: encryption (F-1), access control, retention/purge participation (F-3 — a purged claim resurrectable from backup is not purged), and off-host handling. Left unstated, the backup script becomes a shadow compliance domain — exactly what AD-3 exists to prevent.

**Fix at spine altitude:** one line: *backups are encrypted, access-controlled like the live store, and covered by the retention/purge policy.*

### F-8 [MEDIUM] — Secrets management is "12-factor env vars" and nothing else

DB credentials, future OIDC client secrets, and TLS keys are covered only by "12-factor env vars; one settings.py". No invariant prevents secrets landing in `deploy/` env templates committed to git, in compose files, in structlog output (pydantic-settings will happily repr them), or in the SPA build. This diverges per unit and per developer.

**Fix at spine altitude:** *secrets never appear in the repo, in images, in logs, or in the OpenAPI/TS-generated client; env injection at deploy time only; `SecretStr` (or equivalent redaction) in settings.* Rotation procedure is implementation detail and can stay out.

### F-9 [MEDIUM] — Authn/session invariants are half-fixed; the deferred IdP needs a guard-rail

What *is* fixed is good (server-resolved role/persona, no scope in tokens, auth as FastAPI dependency, IdP swappable). What is missing: (a) an explicit invariant that **every** `/api` route requires an authenticated session — AD-7 governs *scoping* but only "list/detail/aggregate endpoints"; SSE copilot streams and command endpoints must be inside the same perimeter by rule, not by habit; (b) any session-lifetime posture (idle timeout for a PHI console is a compliance expectation, and "session length" is a unit-divergence classic); (c) the Deferred IdP entry says "decide before first deployment outside dev" — good, but it should also state that *dev-mode local credentials never ship with real claimant data*, since the prototype's persona-dropdown habit is the exact regression AD-7 warns about.

**Fix at spine altitude:** one invariant: *no unauthenticated route exists under `/api` (including SSE); sessions expire; the persona-picker pattern is dev-only and gated from any environment holding real PHI.*

### F-10 [LOW] — Internal plaintext transit is implied but never decided

The deployment diagram shows `api → ollama` as `http (internal net only)` and says nothing about `api → pg`. Plaintext on a single-host internal Docker network is a defensible posture — but it is currently an *accident of the diagram*, not a decision. State it (single-host internal traffic may be plaintext; the moment any component leaves the host, TLS is required) so a future second-host deployment doesn't silently carry the assumption across a real network.

---

## Non-findings (checked, acceptable at this altitude)

- **Transit encryption at ingress** — HTTPS/TLS at nginx as sole ingress is stated. Adequate, given F-10 is addressed.
- **PHI leaving the network via AI** — AD-5's absolute ban (including no fallback code path) is the right spine-level treatment.
- **Model supply chain** (pulling `qwen3`/`bge-m3` from a public registry) — real, but an ops-hardening concern, not a divergence-preventing invariant. Deferred "Model pinning" is the right home; suggest adding digest-pinning there when models are pinned.
- **Analyst export (FR-AN-1..6)** — export of PHI-bearing aggregates is deferred as its own epic; acceptable, but that epic must inherit F-5's no-PHI-in-logs and AD-7 scoping when written.
- **Email/calendar egress** — correctly deferred *with* its own compliance-review flag. Good pattern; F-3 and F-1 should get the same treatment.

## Summary of required spine changes

| # | Severity | One-line change |
| --- | --- | --- |
| F-1 | critical | Add encryption-at-rest invariant covering Postgres, backups, binaries, sidecar volumes |
| F-2 | high | Classify chat/checkpoint/prompt content as PHI: scoped, retained, purgeable like claim data |
| F-3 | high | Add single-owner retention/purge command invariant with cascade to embeddings/checkpoints/binaries |
| F-4 | high | Make audit_event append-only at the DB with a retention floor (amend AD-4) |
| F-5 | high | Extend Logging convention: identifiers only; never medical/wage/free-text or prompt bodies in logs |
| F-6 | medium | Extend AD-5: Ollama prompt-logging off, weights-only volume, reachable from `api` only |
| F-7 | medium | Backups encrypted, access-controlled, and inside the purge policy |
| F-8 | medium | Secrets never in repo/images/logs/generated clients; redaction in settings |
| F-9 | medium | All `/api` routes (incl. SSE) authenticated; sessions expire; persona-picker never meets real PHI |
| F-10 | low | State the internal-plaintext decision explicitly with its single-host boundary condition |

None of these require choosing implementations — each is a one-to-three-sentence invariant or convention amendment. With F-1 through F-5 incorporated, the spine would be fit to bind for a PHI workload.
