# Sprint Change Proposal — Deferred-Work Triage into Epic 9

- **Date:** 2026-08-24
- **Project:** Workers_Comp_Prototype (LINEWORKER)
- **Raised by:** Jeba
- **Workflow:** `bmad-correct-course`, batch mode
- **Priority lens applied:** production readiness — what would break, leak, or lie in a real deployment
- **Source of record:** `_bmad-output/implementation-artifacts/deferred-work.md` (245 entries, 49 sections, Stories 1.3 → 8.4, 2026-08-10 → 2026-08-23)

---

## 1. Issue Summary

### The trigger

Development completed on 2026-08-23. All 8 epics and all 40 stories in `sprint-status.yaml` read `done`, and Story 8.4 closed the AD-15 CI gate at commit `7aa1e4c`. There is no failing test, no broken build, and no unimplemented acceptance criterion.

What triggered this change request is what accumulated *alongside* that completion: **`deferred-work.md` grew to 245 entries and has no owner, no priority, no schedule, and no exit criteria.** Only 5 entries record a closure and only 1 carries a `resolution:` block. The register is high-quality prose — every entry states the defect, the evidence, and the reason deferral was legitimate at the time — but it is prose, not a backlog. Nothing in the project plan consumes it.

### Issue type

**Technical debt materialised as a governance gap** — not a technical limitation, not a new stakeholder requirement, not a misunderstanding of scope. Each individual deferral was correctly argued at the time. The change is that the reasons which justified deferral have expired *en masse*: 21 entries defer to "a later story", and there are no later stories.

### Evidence

Three classes of evidence make this actionable rather than theoretical:

1. **Deferrals whose named resolver shipped and did not resolve them.** Story 3.3 deferred the read-path audit-actor problem pending "a system actor, or a background refresher"; Story 3.4 built the system actor (`UserRole.system`, migration 0029) *and* the scheduled-jobs hook, applied neither, and recorded that fact. Story 2.3 deferred the AD-12 mark-stale obligation to Epic 6; by Story 3.1 it had grown from one command to five; Epic 6 shipped `services/rag` and walked none of them. The toast primitive was deferred from 3.4 → 4.1 → 4.2 → 4.3, arrived in 4.3, and the two surfaces that asked for it were not migrated to it.

2. **The production profile has never run as production.** `api/app.py` refuses to boot under `ENV=prod`, so `compose.prod.yaml` deliberately renders `ENV: dev`. Recorded in Story 8.2's implementation, re-recorded in 8.2's code review, and re-recorded again in 8.4's code review — the story whose headline deliverable was "the prod compose starts the full stack." The GPU overlay is proven only to render. NFR-5's at-rest encryption was not evidenced by the executed boot.

3. **Measured wrong numbers on shipped screens.** WC-20051's Bills tab shows a cost-bar legend reading "Medical $475" against a bills card reading "$6,144 paid" — a 13× gap, two records of one claim about 40px apart. The Total Paid KPI card excludes $335,985 of `status = paid` bills and expenses that the Bills tab displays. "Total incurred" labels a figure that omits reserve, and Story 7.4 put that same basis on four more figures.

### Why this is a correct-course matter and not a retrospective note

Six entries are **go-live blockers under the project's own NFRs**, not cleanups:

| Entry | NFR breached |
|---|---|
| The schema carries no fatality indicator — a fatal claim is classified Path B and its handler never sees the death-benefit filings | NFR-4 |
| The nine statutory form URLs are NY WCB / FNSB placeholders presented to handlers as their jurisdiction's forms, across 15 plants in a dozen states | NFR-4 |
| The 17 seeded statutory rate schedules are illustrative figures with an inferred effective date | NFR-4 |
| `notes.py`, `meetings.py`, `emails.py` copy free text verbatim into `audit_event.after`, so purging a note does not purge its PHI | NFR-5 / AD-11 |
| The database password is in the argv of every `pg_dump` / `pg_basebackup` / long-lived `pg_receivewal` — readable by anything that can read `/proc` or run `docker top` | NFR-5 |
| TLS to Postgres is *permitted*, not *enforced* (`ssl=on` with `host`, not `hostssl`, records) | NFR-5 |

---

## 2. Impact Analysis

### 2.1 Epic impact (checklist §2)

| Check | Finding | Status |
|---|---|---|
| 2.1 Can the epic containing the trigger still be completed as planned? | **N/A in the usual sense — all 8 epics are already `done`.** No epic needs reopening: each was completed against its own acceptance criteria, and every deferral was recorded rather than hidden. Reopening Epic 8 to absorb this work would falsify a `done` status that is accurate. | [x] Done |
| 2.2 Required epic-level changes | **Add one new epic.** Not "modify existing epic scope" (would rewrite closed history), not "remove or defer" (nothing is non-viable), not "redefine" (the original understanding held). | [x] Done |
| 2.3 Remaining planned epics affected | None — there are none. Epic 9 becomes the only open epic. | [N/A] |
| 2.4 Does the issue invalidate future epics or necessitate new ones? | Necessitates exactly one. No epic is made obsolete: the debt is *inside* delivered features, not a replacement for them. | [x] Done |
| 2.5 Should epic order or priority change? | No resequencing of 1–8 (all closed). Epic 9's own internal order is the substance of this proposal, and it is driven by the production-readiness lens rather than by source-story order. | [x] Done |

**Consequence for the tracker:** Epic 9 enters `sprint-status.yaml` with status `backlog` and 13 stories at `backlog`. The AD-15 bijection lint requires one `e2e/stories/9-N-*.spec.ts` per non-`backlog` key — so specs are authored per story as each moves to `ready-for-dev`, not up front. **Story 9.0 note:** 8.4's own review records that the bijection lint checks the tracker against `e2e/stories/` and *nothing checks either against `epics.md`* — adding 13 story keys is the first change large enough for that blind spot to matter, so closing it is folded into Story 9.1.

### 2.2 Artifact conflict analysis (checklist §3)

**PRD (§3.1).** There is no standalone PRD in `planning-artifacts/`; `epics.md` carries the Requirements Inventory (FR, NFR-1…8, Additional Requirements, UX-DR1…12) and is the requirements document of record. **Assessment recorded, not treated as a blocker.**

> **Discovered while executing this proposal:** `epics.md`'s own front matter names `docs/BRD-Workers-Comp-Console.md (git HEAD)` as its first input document — the acting PRD — and **that file is not in HEAD.** It was deleted in commit `99f8171` ("stories updated", 2026-08-09), the same day all 40 story files were batch-created. Every story since has been built against a requirements document that is not in the working tree; it is recoverable only from `git show 99f8171^:docs/BRD-Workers-Comp-Console.md`. This is a governance defect independent of the deferred-work register and is added to Story 9.1's doc tasks: either restore the BRD to `docs/` or amend the front matter to name what is actually of record.

- No FR is contradicted. Every FR that Epics 1–8 claimed is genuinely delivered.
- **Three NFRs are asserted rather than achieved**, and this proposal's honest position is that they should not be read as closed:
  - **NFR-4** (validated statutory content) — "statutory form references validated per jurisdiction before go-live" is explicitly a *pre-go-live* clause, and it is unmet on all three counts (forms, rate figures, effective dates).
  - **NFR-5** (security & PHI protection) — "all claim-derived stores encrypted at rest and in transit" and "single purge cascade" are configured but not enforced or complete.
  - **NFR-8** (operations) — "documented restore drill" is met for Procedure A only; the off-host transport that NFR-8 names has never been exercised anywhere.
- **MVP is still achievable.** No scope reduction is proposed. The MVP as defined was a working console, and it works.

**Architecture (§3.2).** `ARCHITECTURE-SPINE.md` and `docs/Architecture-LINEWORKER.md` need updates in four places:

| Area | Conflict | Action |
|---|---|---|
| AD-8 (two-tier rules) | Eight parameter sets are Python literals that AD-8's reading makes tuning: `SCHEDULE_WEEKS`/`MIN_WEEKS`/`MAX_WEEKS`, the comp rate's 0–150% domain, `DIARY_CHECK_IN_DAYS`, the SIU 60 threshold, `export_limits.maxRows` | Either promote or write the placement rule down as a rule; Story 9.13 decides once |
| AD-10 (one computer per derived value) | The reserve check is deliberately outside the registry, so `derivations.get("reserve_check")` does not resolve and Epic 6's tool registry reaches `services/financials` directly | Amend AD-10 to state the registry's boundary explicitly, or widen the contract |
| AD-12 (one write-owner per entity) | The mark-stale obligation is unwired across five AD-4 commands; two GET endpoints and six export GETs perform durable writes | Story 9.7 + 9.11; AD-12 needs a "read paths may write" clause or the writes must move |
| AD-15 / §8 CI bullet | `docs/Architecture-LINEWORKER.md § 8` still carries the older, lighter gate statement — "lint + typecheck + tests on every merge; migrations must apply clean" — with no mention of Playwright, story specs, `@smoke`, the merge-to-main full suite, or the bijection lint | Doc-only correction, folded into Story 9.1 |

Also: the spine's own **Deferred registry** and `docs/Architecture-LINEWORKER.md § 10` list the intentionally-not-built items (IdP, MinIO, worker-container scheduler, observability, Ollama queueing). Several Epic 9 stories are exactly those items becoming due — **the scheduler in particular is now blocking three separate deferrals** (session reaper, background schedule refresher, backup catch-up). Its reopening trigger has fired.

**UX/UI (§3.3).** No standalone UX spec file; UX-DR1…12 live in `epics.md`. Two conflicts:

- **UX-DR11** ("replace all native dialogs with in-app toasts; loading/empty/error states everywhere") — the toast primitive now exists but the payment-approval sheet and the meeting scheduler, the two surfaces that asked for it, were not migrated. UX-DR11 is partially unmet on the surfaces that motivated it.
- **UX-DR5 / UX-DR3 robustness** — every `Record<Enum, …>` label map in the console throws a `TypeError` and unmounts the case-file pane when the server sends an enum member the cached bundle predates. This is a UX-visible deploy-skew failure with no designed state, and `TimelineTag` already renders raw lowercase tokens (`edit`, `document`, `compliance`) because the label map deferred since Story 2.3 was never written.

**Other artifacts (§3.4).**

| Artifact | Impact |
|---|---|
| `deploy/compose.prod.yaml`, `compose.gpu.yaml` | Cannot boot as prod; GPU overlay never booted; two locally-built image tags (`lineworker/postgres:pg18-pgaudit`, `lineworker/backup:pg18`) with no registry, so `docker compose pull` and any air-gapped deploy has no image |
| `deploy/DEPLOYMENT.md`, `RESTORE-DRILL.md` | Procedure B undrilled; SSH transport unexercised; post-restore purge re-run is a manual step nothing tracks |
| `.github/workflows/ci.yaml` | No `concurrency:` group (two pushes = two full e2e jobs); cannot boot the prod profile; branch protection is a claim about a GitHub settings page that the repo cannot assert |
| `.github/required-checks.yml` | Makes the *list* mechanical; nothing makes the *enforcement* mechanical |
| `lineworker/web/openapi.json` | Gitignored, so the stale-generated-client CI check only ever inspects `schema.d.ts` |
| Monitoring / observability | Absent by decision, and now load-bearing: the PHI blocked-value processor fires invisibly, `status.json` is read by nothing but a healthcheck, pgaudit's role-class capture goes to a file nothing reads, and "did Tuesday's batch run?" is answered by grepping logs |
| Testing strategy | Four stories (7.1, 7.4, 7.5, and 7.5 again) declined to demonstrate a *narrowed* analyst at HTTP level because the seed holds exactly one analyst and they are `scope_all` — AD-7 on the analyst routes is asserted at service level only |

### 2.3 Technical impact

- **Code:** ~40 modules across `server/services/`, `server/api/`, `server/data/`, `web/src/`, plus migrations. Three items are schema changes (fatality indicator, `state_rate_schedule` composite key, `blob_key` uniqueness).
- **Infrastructure:** prod/GPU compose profiles, two container images needing a registry, `pg_hba.conf` (`host` → `hostssl`), backup container user, credential passing.
- **Deployment:** the scheduler decision (in-process vs worker container) unblocks three stories and is the single highest-leverage architectural choice in this epic.
- **Data:** the demo dataset itself is implicated — the line-item/column reconciliation and the calendar's `paid` emission are decisions about what the seeded portfolio *means*, not code defects.

---

## 3. Recommended Approach

### Options evaluated (checklist §4)

| Option | Assessment | Effort | Risk | Verdict |
|---|---|---|---|---|
| **1. Direct adjustment** — add stories within the existing epic structure | Would mean reopening Epics 1–8 and reassigning debt to the story that recorded it. Falsifies four `done` epics, scatters one coherent body of work across eight closed contexts, and orders the work by *source story* rather than by *production risk* — the exact inversion of the chosen lens | Medium | High | **Not viable** |
| **2. Potential rollback** — revert completed stories to simplify | Nothing is defective enough to revert. Every deferral is a recorded gap in working code, not a failed approach. Rolling back Story 8.2's TLS work to "do it properly" would remove a partial posture and gain nothing | Low | High | **Not viable** |
| **3. PRD MVP review** — reduce or redefine scope | The MVP is delivered and working. There is nothing to cut. The honest correction is not to scope *down* but to state that three NFRs are not yet closed — which is a documentation change, not a scope change | Low | Low | **Partially adopted** (see below) |
| **4. New epic (hybrid of 3 + a new epic)** | Preserves closed history, groups the work by production risk, gives the register an owner and an exit criterion, and lets the go-live blockers be sequenced first regardless of which story recorded them | High | Low | **Selected** |

### Selected path: Option 1-as-new-epic + the NFR honesty correction from Option 3

**Add Epic 9: Deferred Debt Resolution & Go-Live Readiness** — 13 stories, ordered by production risk — and amend `epics.md` to record that NFR-4, NFR-5 and NFR-8 are closed by Epic 9 rather than by Epic 8.

**Rationale:**

- **Preserves the integrity of the tracker.** Epics 1–8 stay `done` because they are. A `done` status that gets quietly reopened is worth less than a new epic that is honestly `backlog`.
- **Orders by risk, not by history.** The production-readiness lens puts "the prod profile cannot boot" and "the purge cascade does not purge all PHI" first, even though they were recorded in Epic 8, and puts "the `edit` timeline tag renders lowercase" near the end, even though it was recorded in Epic 2.
- **Gives the register an exit.** Every entry in `deferred-work.md` maps to exactly one Epic 9 story or to the decisions register in Story 9.13. When a story lands, its entries gain a `resolution:` block. The register stops being append-only.
- **Sustainability.** The alternative — leaving 245 entries as prose — means the next developer re-derives this analysis, and the register keeps growing without a consumer.

**Trade-off accepted:** Epic 9 is large (13 stories, comparable to Epics 2 and 6 combined) and much of it is unglamorous infrastructure with no demo surface. That is what the work is. The mitigation is the ordering: Stories 9.1–9.4 and 9.6 are the go-live gate, and everything after 9.6 can be scheduled against appetite rather than against a deadline.

**Effort estimate:** 13 stories at Epic 8's observed cadence (4 stories over 2026-08-22 → 08-23, but those were unusually large) — realistically 3–4 weeks for 9.1–9.6, and 9.7–9.13 sized to available capacity.

**Timeline impact on MVP:** none — the MVP is delivered. Impact on *go-live*: Stories 9.1, 9.2, 9.3, 9.4 and 9.6 are hard prerequisites.

---

## 4. Detailed Change Proposals

### 4.1 Story map — the triage

All 245 entries map to exactly one destination. Counts are entries assigned, and are approximate at the margins where one entry spans two stories (each is assigned to the story that owns the fix, and cross-referenced from the other).

| Story | Title | Entries | Priority | Blocks go-live |
|---|---|---|---|---|
| 9.1 | The Prod Profile Actually Boots As Prod | ~14 | P0 | **Yes** |
| 9.2 | Credential, Transport & Privilege Hardening | ~11 | P0 | **Yes** |
| 9.3 | The Purge Cascade Actually Purges | ~15 | P0 | **Yes** |
| 9.4 | Disaster Recovery Proven, Not Documented | ~17 | P0 | **Yes** |
| 9.5 | Session Lifecycle & Revocation Policy | ~4 | P1 | No |
| 9.6 | Validated Statutory Content (NFR-4) | ~7 | P0 | **Yes** |
| 9.7 | Read Paths That Write, & The Actor They Name | ~8 | P1 | No |
| 9.8 | Paging Correctness & Real Pagination | ~13 | P1 | No |
| 9.9 | Deploy-Skew Survival & Contract Hygiene | ~16 | P1 | No |
| 9.10 | The O(scope) Fold Push-Down | ~22 | P2 | No |
| 9.11 | The AD-12 Mark-Stale Wiring & RAG Loose Ends | ~12 | P1 | No |
| 9.12 | Correction Paths For PHI-Class Rows | ~13 | P2 | No |
| 9.13 | Product & Placement Decisions Register | ~28 | Needs owner | Partly |
| — | Accept-and-close (documented limits, no work) | ~65 | — | No |

### 4.2 `epics.md` — proposed addition

**OLD:** file ends at Story 8.4's acceptance criteria (line 1202).

**NEW:** append the following, and amend the three NFR closure claims noted in §4.3.

---

#### Epic 9: Deferred Debt Resolution & Go-Live Readiness

The organization can deploy this on real PHI in a real jurisdiction. Epics 1–8 delivered every capability and recorded 245 conscious deferrals in `deferred-work.md`; this epic resolves them in production-risk order, and closes the three NFRs that Epic 8 configured but did not achieve. Every story ends by writing a `resolution:` block into each register entry it answers — the register's exit criterion is that no entry lacks one. Closes NFR-4, NFR-5 and NFR-8 in substance.

##### Story 9.1: The Prod Profile Actually Boots As Prod

As an operations engineer,
I want `ENV=prod` to be a bootable, CI-exercised configuration,
So that "the prod compose starts the full stack" is a fact rather than a rendering.

**Acceptance Criteria:**

**Given** `api/app.py`'s `ENV=prod` boot refusal
**When** the prod prerequisites it guards are supplied
**Then** the api boots under `ENV: prod` and `compose.prod.yaml` sets `ENV: prod` rather than inheriting `dev`
**And** the two `env`-keyed defaults currently overridden explicitly are driven from the real value

**Given** the two locally-built image tags
**When** `docker compose pull` runs against the prod profile
**Then** `lineworker/postgres:pg18-pgaudit` and `lineworker/backup:pg18` resolve from a registry, and an air-gapped deploy has a documented image-transfer path

**Given** a least-privilege or managed Postgres
**When** migration 0050 runs
**Then** `CREATE EXTENSION pgaudit` either succeeds without superuser or fails with a message naming the actual cause

**Given** CI
**When** a push lands
**Then** the prod profile is booted and health-verified in a job, `concurrency:` cancels superseded runs, `ollama`'s `start_period` is a budget rather than a wall, and the bijection lint additionally reconciles `sprint-status.yaml` against `epics.md`

**Given** the GPU overlay and NFR-5's at-rest encryption
**When** neither can be exercised on the CI host
**Then** the deployment runbook records the exact manual verification, its executed date, and the host class required — no unevidenced claim survives in prose

**Given** `docs/Architecture-LINEWORKER.md § 8`
**When** read
**Then** its CI bullet states the AD-15 gate as shipped (Playwright, story specs, `@smoke`, merge-to-main full suite, bijection lint)

##### Story 9.2: Credential, Transport & Privilege Hardening

As a compliance officer,
I want no credential readable from process state and no TLS that a client can decline,
So that NFR-5's "encrypted in transit" is enforced rather than permitted.

**Acceptance Criteria:**

**Given** `pg_dump`, `pg_basebackup` and the long-lived `pg_receivewal`
**When** the backup container runs
**Then** no database password appears in any argv — `docker top` and `/proc` on the host reveal nothing — and the container runs as a non-root user

**Given** the `pg_hba.conf` records this project ships
**When** a client connects without TLS
**Then** the connection is refused (`hostssl`, not `host`), including the records Story 8.3 added

**Given** the `audit_redactor` role
**When** the migration grants it and the api later assumes it
**Then** membership is granted to the role that actually assumes it rather than to `CURRENT_USER`, and the grant does not depend on every profile's owner being a superuser
**And** the daily assumption window is narrowed so a compromise of the api process does not reach redaction rights for the whole day

**Given** the PHI blocked-value processor
**When** a PHI value reaches a log call — including through a foreign stdlib record's message body
**Then** it is blocked, counted, and the counter is exposed where an operator can alert on it

##### Story 9.3: The Purge Cascade Actually Purges

As a compliance officer,
I want a purge that leaves no PHI and no unrelated damage,
So that AD-11's single cascade is true in both directions.

**Acceptance Criteria:**

**Given** `notes.py`, `meetings.py` and `emails.py` copying free text into `audit_event.after`
**When** a note, meeting or email is purged
**Then** its PHI does not survive in the audit diff — either the diffs stop carrying bodies or the cascade redacts them by entity

**Given** `export.*` audit rows
**When** a claim is purged
**Then** the rows recording that claim's data leaving the system are reachable and redactable by claim, not only by age and actor

**Given** the cascade's two connections and two transactions
**When** the process dies between their commits
**Then** no state exists in which the redaction landed and its `redact` events did not

**Given** `purge_user`
**When** it runs
**Then** it does not delete diary notes, meetings or email logs attached to claims that are not being purged, and does not redact claim-field edits on claims outside the purge set

**Given** an in-flight copilot run and a concurrent claim edit
**When** a purge commits
**Then** neither can re-introduce PHI after the cascade has passed — checkpoint writes and post-`_redact` diffs are both fenced

**Given** two `document` or `photo` rows sharing one `blob_key`
**When** one claim is purged
**Then** the other claim's binary survives, enforced by the schema rather than by convention

**Given** the AD-11 ownership guard
**When** a delete is written against a table object or composed by an f-string
**Then** the guard sees it

**Given** the retention floor and `audit_event.before`/`after` predicates
**When** configured and queried
**Then** the floor is driven from a `current_setting()` GUC rather than baked into the RLS policy, and no predicate is defeated by a JSONB column holding the JSON scalar `null` in place of SQL NULL

##### Story 9.4: Disaster Recovery Proven, Not Documented

As a claims organization,
I want every documented recovery path to have been executed at least once,
So that NFR-8's restore drill covers what would actually be used.

**Acceptance Criteria:**

**Given** the off-host SSH transport
**When** a nightly run completes
**Then** it has shipped over SSH to a real remote in at least one exercised environment — not a local directory standing in for one

**Given** Procedure B (point-in-time recovery from base + WAL)
**When** the drill document claims it
**Then** it has been executed and its duration, steps and verification are recorded, as Procedure A's are

**Given** a restore into a cluster that is missing the roles the grants name
**When** an automated test runs
**Then** it restores into a *different* cluster and fails on that class of defect, rather than pinning it by an artifact-content assertion

**Given** the WAL spool ceiling and the nightly copy
**When** a database produces more WAL than `BACKUP_MAX_SPOOL_SEGMENTS` between two runs
**Then** the copy runs before the ceiling prunes, so no segment the night's copy would have shipped is lost

**Given** a failed nightly run, a died WAL receiver, or a flapping stream
**When** it happens
**Then** it is visible outside the container's own healthcheck; `wal_restarts` distinguishes now from months ago; the receiver's death is detected without waiting on the scheduler loop; and a missed window is caught up rather than skipped

**Given** a completed restore
**When** the system comes back
**Then** re-running the purge is tracked, prompted or verified rather than left as an untracked manual step

##### Story 9.5: Session Lifecycle & Revocation Policy

As a security owner,
I want the single-session question decided and enforced,
So that a discarded token is not replayable for its full TTL.

**Acceptance Criteria:**

**Given** the multi-device-versus-single-session policy
**When** decided by an owner
**Then** it is written down, and `POST /auth/login` enforces it — including revoking the session presented in its own request cookie

**Given** expired session rows
**When** the scheduler runs
**Then** they are reaped on a schedule rather than only "on contact" in `resolve_session`

**Given** an idle SPA past `expires_at`
**When** the user has not navigated
**Then** it notices — by periodic revalidation or a refresh-on-focus policy consistent with the decided session lifetime

##### Story 9.6: Validated Statutory Content (NFR-4)

As a claims handler,
I want the statutory content on screen to be my jurisdiction's, sourced and current,
So that NFR-4's "validated before go-live" clause is satisfied.

**Acceptance Criteria:**

**Given** a fatal claim
**When** its path is classified
**Then** a schema-level fatality indicator drives Path C — not a severity/outcome proxy that no real claim can meet — and the handler sees the AFF-1, C-64 and C-65

**Given** `path_required_form` keyed on path alone
**When** a claim is filed under one of fifteen plants across a dozen states
**Then** the forms shown are that jurisdiction's, from validated per-state references, and no NY WCB / FNSB placeholder is presented as a real form

**Given** `state_rate_schedule`
**When** a new benefit year's figures arrive
**Then** the table holds them — the unique constraint is `(state_code, effective_date)` and `rate_for_state` selects with `effective_date <= as_of ORDER BY effective_date DESC LIMIT 1`
**And** the seeded figures are replaced with sourced per-jurisdiction rates carrying their real effective dates

##### Story 9.7: Read Paths That Write, & The Actor They Name

As an auditor,
I want every audit row to name the actor who caused it and every GET to be a read,
So that AD-4's record is honest and the api can be served from a replica.

**Acceptance Criteria:**

**Given** `materialize_schedule` running under the reading caller
**When** a supervisor or analyst opens a case file
**Then** no audit row names them as the actor of a write they did not make — the refresh runs under the system actor Story 3.4 built, on the scheduler Story 3.4 hooked, or not on the read path at all

**Given** `GET /claims/{id}`, `GET /claims/{id}/financials` and the six export routes
**When** they are called
**Then** none performs a durable non-idempotent write, or the exception is stated in AD-12 with its replica consequence

**Given** an export audit row designated the compliance record of PHI egress
**When** the file's delivery is questioned
**Then** the row can answer whether it was delivered

**Given** the payment batch and the daily audit sweep
**When** an operator asks "did Tuesday's batch run?"
**Then** a last-run timestamp and run history answer it without grepping logs
**And** the audit sweep is batched rather than two unbatched full-table scans of the largest table in the schema, registered serially

##### Story 9.8: Paging Correctness & Real Pagination

As any console user,
I want a page walk that cannot skip a row and a list envelope that does not lie,
So that no figure I read is an artefact of paging.

**Acceptance Criteria:**

**Given** the worklist and drill-through offset cursors
**When** a claim leaves the population between two page requests
**Then** no row is silently skipped — the cursor is keyset, not offset

**Given** a resumed walk
**When** page two is fetched
**Then** deadline-driven actions are not computed against an `as_of` up to `MAX_CURSOR_AGE` old, and every date-sensitive payload publishes the `asOf` it resolved (extending Story 7.2's pattern to the queue, the worklist and the drill-through)

**Given** `PriorityClaimsTable` and `DrillClaimsPage`
**When** the base query refetches onto a different first cursor
**Then** `expanded` resets, no page-two fetch fires without a user click, and a failed *refetch* does not replace already-walked rows with an error alert

**Given** `GET /api/glossary`, `/personas` and `list_meetings`
**When** they answer
**Then** `nextCursor` and `total` mean what the Lists convention says — `total` from a `COUNT(*)` or dropped, `LIMIT` applied, filter/sort parameters present, and `list_meetings` not recounting the whole book per page

**Given** the "Show more" path on the stage-grouped queue
**When** one group's next page is requested
**Then** the other three groups are not computed and discarded — via the `groups` filter parameter Story 5.4 recommended

**Given** a claim-verdict facet derived from a written table
**When** a concurrent single-claim read rewrites it mid-walk
**Then** the cursor can pin it

##### Story 9.9: Deploy-Skew Survival & Contract Hygiene

As a user mid-deploy,
I want an unknown enum to degrade a chip rather than unmount the pane,
So that a rolling deploy is not an outage.

**Acceptance Criteria:**

**Given** every `Record<Enum, …>` label map in the console
**When** the server sends a member the cached bundle predates
**Then** the value degrades to a designed fallback — no `TypeError`, no unmounted case file — under one policy decided once for the whole SPA (fallbacks, cache-busting, or a version handshake)

**Given** `TimelineTag`
**When** any tag renders
**Then** a UI-owned label map covers the full set including `edit`, `document` and `compliance`, matching `STAGE_LABEL` / `DISABILITY_LABEL` / `COMM_STATUS_LABEL`

**Given** the generated client check in CI
**When** `openapi.json` drifts
**Then** the build fails — the file is in version control rather than gitignored

**Given** `ApiModel`
**When** a request carries an unknown field or a snake_case alias
**Then** the project's decided `extra` policy applies, uniformly across every endpoint

**Given** one rule value with two wire names and `rulesVersion` naming different documents on different routes of one dashboard
**When** a client reads them
**Then** each rule value has one wire name and each version field names the document it versions
**And** published thresholds no component reads are removed

**Given** `lineworker/web`
**When** a diff is reviewed
**Then** a formatter and a `format` script exist, so reflow is not a third of the diff
**And** `noDerivation.test.ts`'s known limits (prop-rename blindness, `===` absent from the operator set, the three age edges excluded from `DERIVED_FIELDS`) are either closed or recorded as accepted with their consequence stated

##### Story 9.10: The O(scope) Fold Push-Down

As an analyst with a real portfolio,
I want aggregate cost proportional to the answer, not to my whole book,
So that the dashboard survives a portfolio larger than 100 claims.

**Acceptance Criteria:**

**Given** the eight recorded O(scope) fold endpoints — queue, portfolio charts, priority claims, drill-through, fraud panel and its three rate breakdowns, trends, financial decomposition, and the six export routes
**When** any is called
**Then** its dominant per-claim Python fold is pushed into SQL, or the cost is bounded by the answer's size, or the endpoint's ceiling is documented with the portfolio size at which it fails

**Given** the case file, the console's most-fetched payload
**When** it is served
**Then** the three per-request reads Stories 3.1 and 3.2 added are amortised, and the reference tables behind them are cached in a way that survives their becoming effective-dated

**Given** the copilot panel and the narrating quick actions
**When** the panel opens or an action runs
**Then** the full `claim_detail` assembly is not run to render one greeting sentence, and no action reads it twice
**And** the three scoped queries plus saver read issued per run and per history fetch are answered by one join

**Given** the embedding and probe clients
**When** they call Ollama
**Then** connections pool rather than building a fresh `httpx.AsyncClient` per call
**And** the model-availability poll does not run for a supervisor who never opens the copilot

**Given** Recharts 3's dependency graph
**When** the production bundle is built
**Then** the full Redux runtime, ten d3 packages and `react-is@17.0.2` inside a React 19.2 app are either justified in writing or removed
**And** `uv.lock`'s `zen-engine` transitive set has had one deliberate supply-chain read

**Given** an empty book and a scoped ANN search
**When** either runs
**Then** no `IN ()` child read is issued that cannot return a row, and pgvector's HNSW post-filtering does not return fewer than `k` neighbours than the caller's book actually holds

##### Story 9.11: The AD-12 Mark-Stale Wiring & RAG Loose Ends

As the architecture's owner,
I want AD-12's mark-stale obligation actually wired,
So that an embedding cannot silently describe a claim as it used to be.

**Acceptance Criteria:**

**Given** the five AD-4 commands that write embedding source fields — `update_claim_fields`, `add_additional_injury`, `remove_additional_injury`, `update_claim_severity`, `update_comp_rate_override`
**When** each commits
**Then** it calls `services/rag`'s mark-stale command in the same transaction, or the embedding source set is documented as excluding every field those commands write

**Given** the constrained-hardware dev pairing the architecture verifies
**When** `nomic-embed-text` is used for embeddings
**Then** its 768 dimensions are not written into a `vector(1024)` column — the column, the model, or the pairing changes

**Given** `search_knowledge` and the labour-law corpus
**When** implemented and tested but unreachable from the browser
**Then** either an HTTP surface exists or the corpus and its search are removed from the shipped image

**Given** the chat model pulled and served with no application caller
**When** a developer runs their first `up`
**Then** they do not download several gigabytes for a model this build never speaks to

**Given** the initial embedding pass
**When** a fresh environment is seeded
**Then** a non-e2e trigger exists

**Given** the copilot's staleness disclosure
**When** it discloses
**Then** it reads `embedding_staleness_threshold` rather than reusing `insight_staleness_disclosure_days`

**Given** a copilot resume whose caller has lost scope on the drafted claim
**When** it resumes
**Then** the proposal is discarded and audited as the spec's I/O matrix promises, rather than wedging the thread
**And** `create_document` bumps `claim.version` so the letter save is idempotent under replay
**And** the spec's I/O matrix agrees with the shipped checkpoint sweep

##### Story 9.12: Correction Paths For PHI-Class Rows

As a claims handler,
I want to correct a mistake without destroying its audit thread,
So that "delete and re-add" stops being the documented fix.

**Acceptance Criteria:**

**Given** a meeting with a mistyped date, a diary note with a typo, and a secondary injury with the wrong body part
**When** the handler corrects any of them
**Then** an update command exists with `expected_version` CAS, and the audit reads as one correction rather than two unrelated events

**Given** `delete_meeting`'s audit `before` diff
**When** it is written
**Then** `claim_business_id` is not taken from a row read before the DELETE

**Given** a `payment_scheduled` or `paid` week that survives a schedule shortening past it
**When** the Bills tab renders
**Then** the orphan is flagged and the heading does not read "4-week schedule shown" above five rows

**Given** `reviewed`, `confirmed` and `osha_logged` completions
**When** one is set in error
**Then** it can be cleared, as the prototype's `confirmDocument` toggles
**And** a seam row carries the story's disabled control with its tooltip rather than only a disabled deep link

**Given** the payment-approval sheet and the meeting scheduler
**When** an action succeeds
**Then** both use the toast primitive Story 4.3 shipped, closing UX-DR11 on the two surfaces that asked for it
**And** a past meeting nobody ticked does not render identically to a completed one while still offering "✓ Done"
**And** a hung POST leaves the scheduler with an exit

**Given** `MeetingParticipant`, now the shared stakeholder vocabulary for emails
**When** the code is read
**Then** its name matches its role (`Stakeholder`)

##### Story 9.13: Product & Placement Decisions Register

As a product owner,
I want the decisions this codebase deliberately refused to make on my behalf,
So that a person with standing decides them.

**Acceptance Criteria:**

**Given** the ~28 register entries that name a product, clinical, actuarial or architectural-placement decision
**When** this story runs
**Then** each is presented as a decision with its options, its cost, and the surfaces it moves — and each receives a recorded decision with an owner and a date

**Given** the money-basis decisions
**When** decided
**Then** the "Total incurred" label (paid vs paid-plus-reserve, now on four figures across the KPI cards and the decomposition surface), the Total Paid basis (which excludes $335,985 of `status = paid` bills and expenses the Bills tab shows), and the line-item-versus-`paid_*`-column reconciliation (a measured 13× gap on WC-20051, 40px apart on one tab) are each settled with their consequences applied consistently across every surface

**Given** the clinical and lifecycle decisions
**When** decided
**Then** TTD-versus-TPD for a recovered worker, whether a secondary injury's severity moves the claim's risk band and priority score, which of the two body-part vocabularies is canonical, whether clinical edits are permitted after settlement, and whether the calendar may mark an unapproved week `paid` are each settled

**Given** the placement decisions AD-8 and AD-10 leave open
**When** decided
**Then** the rule "which tier owns this value" is written down once — covering `SCHEDULE_WEEKS`/`MIN_WEEKS`/`MAX_WEEKS`, the comp rate's 0–150% domain, `DIARY_CHECK_IN_DAYS`, the SIU 60 threshold, `export_limits.maxRows`, `pageLimit`'s home, and whether the reserve check joins the AD-10 registry
**And** the seed-migration rule ("seeds of derived data go through the generator; seeds of vocabularies are frozen") is written as a rule rather than re-argued per file

**Given** the demo-data decisions
**When** decided
**Then** the two hash-bucket derivations driving checklist triggers, the absent document and photo bytes with no ingest path anywhere in Epics 1–8, `photo.source` as an unparseable provenance sentence, and the scheduler mechanism (in-process versus worker container — now blocking three separate deferrals) are each settled
**And** whether a narrowed analyst is demonstrated at HTTP level is decided rather than declined a fifth time

---

### 4.3 `epics.md` — NFR closure corrections

**Story 8.x closure claims:** Epic 8's summary states "Closes NFR-5, NFR-7, NFR-8."

**OLD:**
> Per-write audit and encryption were built into Epics 1–7 as they went; this epic delivers the compliance capabilities that stand alone. Closes NFR-5, NFR-7, NFR-8.

**NEW:**
> Per-write audit and encryption were built into Epics 1–7 as they went; this epic delivers the compliance capabilities that stand alone. Closes NFR-7. NFR-5 and NFR-8 are configured here and closed in substance by Epic 9 — see `deferred-work.md` and Stories 9.1–9.4.

**Rationale:** the prod profile has never booted as prod, TLS to Postgres is permitted rather than enforced, the purge cascade leaves PHI in audit diffs, and the off-host transport NFR-8 names has never been exercised. Leaving "Closes NFR-5, NFR-8" unqualified means the next reader believes those obligations are met.

**Also:** NFR-4 is claimed by no epic and is unmet on all three counts. Add to the Requirements Inventory a note that NFR-4 closes with Story 9.6.

### 4.4 Architecture spine — proposed amendments

Four amendments, each small and each with its Epic 9 story named. Full text drafted when the story reaches `ready-for-dev`; the placements are:

1. **AD-8** — add the tier-placement rule Story 9.13 decides (which values are tuning, which are domain).
2. **AD-10** — state the registry's boundary explicitly: a derived value that reads a rule document or a second table is outside it, and what that costs the copilot tool registry.
3. **AD-12** — add the read-path-write clause (or record that Story 9.7 removed the need for one) and the mark-stale obligation's actual wiring status.
4. **Deferred registry** — mark the scheduler's reopening trigger as **fired** (three deferrals now block on it) and record the SSH transport, Procedure B and GPU overlay as unexercised rather than deferred.

`docs/Architecture-LINEWORKER.md § 8`'s CI bullet correction is folded into Story 9.1 as a doc task.

### 4.5 `deferred-work.md` — proposed restructure

**OLD:** 49 append-only sections ordered by the story that recorded each entry; 1 `resolution:` block in 245 entries.

**NEW:** same entries, same prose — nothing is deleted, because the evidence is the value — plus:

- a **front-matter triage table** mapping every entry to its Epic 9 story or to `accept-and-close`;
- a required `resolution:` block on every entry an Epic 9 story lands, following the format the one existing block already establishes;
- the ~65 `accept-and-close` entries moved under a `## Accepted limits` heading with a one-line statement of what was accepted and why, so they stop reading as open work.

**Rationale:** the register's problem is not its content, it is that nothing consumes it and nothing closes it. This gives it both without losing an argued word.

---

## 5. Implementation Handoff

### Scope classification: **Major**

Three of the five classification signals are present: a new epic, four amendments to the architecture spine, and three NFR closure claims being corrected. This is not a backlog reshuffle.

### Handoff plan

| Recipient | Responsibility | Deliverable in |
|---|---|---|
| **Product Manager (John)** — `bmad-agent-pm` | Owns Story 9.13's decision register. ~28 decisions need a person with standing, and 3 of them (the money bases) move numbers on shipped screens. Also owns the NFR closure corrections in §4.3 | Before 9.1 starts — Stories 9.6, 9.10 and 9.12 each depend on decisions in 9.13 |
| **Solution Architect (Winston)** — `bmad-agent-architect` | Owns the four spine amendments in §4.4, and the single highest-leverage decision in this epic: **the scheduler mechanism** (in-process vs worker container), which unblocks Stories 9.4, 9.5 and 9.7 | Before 9.4 |
| **Developer (Amelia)** — `bmad-dev-story` | Implements 9.1 → 9.12 in the stated order, one story per fresh context, writing a `resolution:` block into each register entry the story answers | Per story |
| **Scrum/PO** | `sprint-status.yaml` gains Epic 9 and 13 story keys at `backlog`; `bmad-sprint-planning` regenerates; per-story e2e specs authored as each key leaves `backlog` (AD-15 bijection lint) | On approval |

### Sequencing and dependencies

```
9.13 (decisions) ──┬─→ 9.6 (statutory content)  ─┐
                   ├─→ 9.10 (folds)              │
                   └─→ 9.12 (correction paths)   │
                                                 ├─→ GO-LIVE GATE
Architect: scheduler decision ─┬─→ 9.4 (DR)     │
                               ├─→ 9.5 (session) │
                               └─→ 9.7 (writes)  │
                                                 │
9.1 (prod boots) ─→ 9.2 (credentials) ─→ 9.3 ───┘
                                        (purge)
9.8, 9.9, 9.11 — independent, schedule against capacity
```

**Go-live gate:** 9.1, 9.2, 9.3, 9.4, 9.6. Nothing ships to real PHI in a real jurisdiction until those five are `done`.

### Success criteria

1. Every one of the 245 register entries carries either a `resolution:` block or an `Accepted limits` placement — no entry is silently open.
2. The prod profile boots as `ENV=prod` in CI, and the GPU overlay and at-rest encryption have a dated manual verification record.
3. A purge leaves no PHI in `audit_event`, in export rows, or in checkpoints — proven by the integration sweep Story 8.1 built, extended to those three stores.
4. Procedure B and the SSH transport have each been executed once, with recorded duration and verification.
5. No statutory figure or form reference on screen is unsourced; a fatal claim reaches Path C.
6. `deferred-work.md` stops growing faster than it closes — measured at each Epic 9 story's `done`.

---

## Checklist Record

| § | Item | Status |
|---|---|---|
| 1.1 | Triggering story identified | [x] Done — no single story; the trigger is Epic 8's completion with 245 open entries |
| 1.2 | Core problem defined and categorised | [x] Done — technical debt as a governance gap |
| 1.3 | Initial impact and evidence gathered | [x] Done — three evidence classes, six NFR-breaching entries tabulated |
| 2.1 | Current epic completable as planned? | [x] Done — all 8 already `done`; none reopened |
| 2.2 | Epic-level changes determined | [x] Done — add one epic |
| 2.3 | Remaining planned epics reviewed | [N/A] — none remain |
| 2.4 | Issue invalidates future epics / needs new? | [x] Done — one new, none obsolete |
| 2.5 | Epic order or priority change? | [x] Done — 1–8 unchanged; Epic 9 internally ordered by production risk |
| 3.1 | PRD conflicts | [!] Action-needed — no standalone PRD; `epics.md` carries requirements; three NFR closure claims need correcting (§4.3) |
| 3.2 | Architecture conflicts | [!] Action-needed — AD-8, AD-10, AD-12, AD-15/§8 and the Deferred registry (§4.4) |
| 3.3 | UI/UX conflicts | [!] Action-needed — UX-DR11 partially unmet; every enum label map is a deploy-skew crash (Stories 9.9, 9.12) |
| 3.4 | Other artifacts | [!] Action-needed — compose profiles, CI, deployment docs, `openapi.json`, observability, analyst-scope test strategy |
| 4.1 | Option 1: direct adjustment | [ ] Not viable — would falsify four `done` epics and order by history rather than risk |
| 4.2 | Option 2: rollback | [ ] Not viable — nothing defective enough to revert |
| 4.3 | Option 3: MVP review | [x] Partially adopted — no scope cut; NFR honesty correction taken |
| 4.4 | Recommended path selected | [x] Done — new Epic 9 (13 stories) + NFR closure correction |
| 5.1 | Issue summary written | [x] Done — §1 |
| 5.2 | Epic + artifact impact documented | [x] Done — §2 |
| 5.3 | Recommended path with rationale | [x] Done — §3 |
| 5.4 | MVP impact and action plan | [x] Done — MVP unaffected; go-live gated on 9.1–9.4 + 9.6 |
| 5.5 | Agent handoff plan | [x] Done — §5 |
| 6.1 | Checklist completion reviewed | [x] Done |
| 6.2 | Proposal accuracy verified | [x] Done — every claim traced to a named register entry, story file, or commit |
| 6.3 | Explicit user approval | [x] Done — approved by Jeba, 2026-08-24, without conditions |
| 6.4 | `sprint-status.yaml` updated | [x] Done — `epic-9: backlog` + 13 story keys + `epic-9-retrospective: optional`; `last_updated` 2026-08-24 |
| 6.5 | Next steps confirmed | [x] Done — see §6 |

---

## 6. Execution Record (2026-08-24)

Approved without conditions. Applied:

| Artifact | Change |
|---|---|
| `epics.md` — Epic List | Epic 9 entry added; Epic 8's entry corrected to "Closes NFR-7" with NFR-5/8 pointing at Epic 9 |
| `epics.md` — FR Coverage Map | NFR closure lines split: NFR-7 → Epic 8; NFR-5/8 configuration → Epic 8, closure in substance → Epic 9 (9.1–9.4); NFR-4 → Epic 9 (9.6), with the note that no epic 1–8 claimed it |
| `epics.md` — Epic 8 body | Closure claim corrected with the four reasons stated inline |
| `epics.md` — new Epic 9 section | 13 stories with full Given/When/Then acceptance criteria; go-live gate and blocking decisions stated in the epic preamble |
| `sprint-status.yaml` | `epic-9: backlog`, 13 story keys at `backlog`, `epic-9-retrospective: optional`, gate and decision-blockers as comments; `last_updated` → 2026-08-24 |
| `deferred-work.md` | Triage table inserted at the head — all 245 entries assigned to one of the 13 stories or to *Accepted limits*, with the exit criterion and the five already-closed entries named |

**Story keys as registered** (AD-15 bijection lint matches anchored, so `9-1` cannot claim `9-10`'s spec):
`9-1-prod-profile-boots-as-prod`, `9-2-credential-transport-privilege-hardening`, `9-3-purge-cascade-actually-purges`, `9-4-disaster-recovery-proven`, `9-5-session-lifecycle-revocation-policy`, `9-6-validated-statutory-content`, `9-7-read-paths-that-write`, `9-8-paging-correctness-real-pagination`, `9-9-deploy-skew-survival-contract-hygiene`, `9-10-o-scope-fold-push-down`, `9-11-mark-stale-wiring-rag-loose-ends`, `9-12-correction-paths-phi-class-rows`, `9-13-product-placement-decisions-register`.

### Deliberately not done here

1. **The four architecture-spine amendments (§4.4).** Held for the Architect (`bmad-agent-architect`) rather than self-authored — AD-8, AD-10 and AD-12 are binding decisions, and the spine is the document every story derives from.
2. **The *Accepted limits* partition in `deferred-work.md`.** The triage table assigns the ~65 entries; physically moving them under a new heading is a bulk edit to 290KB of argued prose, and it lands with the first Epic 9 story rather than as an unreviewed sweep. Recorded in the triage table as outstanding.
3. **Per-story e2e specs.** AD-15's bijection lint requires exactly one `e2e/stories/9-N-*.spec.ts` per non-`backlog` key. All 13 keys are `backlog`, so the lint is satisfied now; specs are authored as each story leaves `backlog`.
4. **`bmad-sprint-planning` regeneration.** Left to the PO, in a fresh context.

### Next steps

| Order | Who | Action |
|---|---|---|
| 1 | PM (John) — `bmad-agent-pm` | Story 9.13's decision register. Gates 9.6, 9.10, 9.12. Three of its decisions move money figures on shipped screens |
| 2 | Architect (Winston) — `bmad-agent-architect` | The four spine amendments, and the scheduler mechanism decision. Gates 9.4, 9.5, 9.7 |
| 3 | PO | `bmad-sprint-planning` to regenerate against the 9-story-key addition |
| 4 | Dev (Amelia) — `bmad-create-story` then `bmad-dev-story` | Story 9.1 first — it is the only go-live-gate story with no upstream decision dependency |
