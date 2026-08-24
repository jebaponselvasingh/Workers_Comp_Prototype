# Epic 9 Context: Deferred Debt Resolution & Go-Live Readiness

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 9 makes the delivered console deployable on real protected health information in a real jurisdiction. Epics 1–8 shipped every functional capability and closed the quality gate, but did so while consciously recording 245 deferrals in the deferred-work register, and three non-functional obligations — validated statutory content, security/PHI protection, and operations — were configured rather than achieved. This epic resolves that debt in production-risk order rather than the order it was recorded: the production profile has never booted as production, credentials sit in process state, TLS to the database is permitted rather than required, the purge cascade leaves PHI in audit diffs and export rows, disaster recovery is documented rather than executed, and the statutory forms and benefit figures on screen are placeholders. Every story closes by writing a resolution record into each register entry it answers; the register's exit criterion is that no entry is left silently open — each carries either a resolution or an explicit accepted-limit placement. This is a governance closure epic as much as a code one: it also carries the schema changes, architecture-spine amendments, and product decisions the earlier epics deliberately refused to make on someone else's behalf.

## Stories

- Story 9.1: The Prod Profile Actually Boots As Prod
- Story 9.2: Credential, Transport & Privilege Hardening
- Story 9.3: The Purge Cascade Actually Purges
- Story 9.4: Disaster Recovery Proven, Not Documented
- Story 9.5: Session Lifecycle & Revocation Policy
- Story 9.6: Validated Statutory Content (NFR-4)
- Story 9.7: Read Paths That Write, & The Actor They Name
- Story 9.8: Paging Correctness & Real Pagination
- Story 9.9: Deploy-Skew Survival & Contract Hygiene
- Story 9.10: The O(scope) Fold Push-Down
- Story 9.11: The AD-12 Mark-Stale Wiring & RAG Loose Ends
- Story 9.12: Correction Paths For PHI-Class Rows
- Story 9.13: Product & Placement Decisions Register

## Requirements & Constraints

- **Go-live gate.** Stories 9.1, 9.2, 9.3, 9.4 and 9.6 are hard prerequisites; nothing reaches real PHI in a real jurisdiction until all five are done. The rest are scheduled against capacity.
- **Claims must be evidenced, not asserted.** Where a capability cannot be exercised on the CI host (GPU overlay, at-rest volume encryption), the deployment docs record the exact manual verification, its executed date, and the host class required. A documented recovery procedure that has never been run does not count as met.
- **Statutory content must be sourced per jurisdiction.** Benefit minimum/maximum figures and form references need real per-state values with real effective dates, replacing illustrative seed data and placeholder form URLs, across fifteen plants in a dozen states. A fatal claim must be classified by a schema-level fatality indicator, not a severity or outcome proxy. Money stays integer cents.
- **PHI must not survive a purge, and a purge must not over-reach.** The single cascade has to cover free text copied into audit diffs, export-egress rows reachable by claim rather than only by age and actor, checkpoints written by an in-flight copilot run, and edits committed between the cascade's own commits. Equally, purging one subject may not touch records belonging to claims outside the purge set, and a shared binary must survive a sibling's purge by schema constraint rather than convention.
- **Credentials must not be readable from process state, and TLS must be required.** No database password in any process argv; the backup container runs unprivileged; a connection without TLS is refused rather than downgraded. The PHI log-blocking mechanism must be observable — counted and exposed where an operator can alert — and must catch PHI arriving through third-party log records.
- **Every audit row names the actor who caused it**, and no GET performs a durable non-idempotent write unless the exception is documented with its read-replica consequence. Scheduled jobs expose a last-run time and history rather than requiring log archaeology.
- **Paging must not lie.** Keyset cursors so a row leaving the population cannot cause a skip; list envelopes report a real count or drop it; every date-sensitive payload publishes the as-of date it resolved so clients and tests read one clock.
- **A rolling deploy is not an outage.** An enum member the client bundle predates degrades to a designed fallback rather than throwing and unmounting a pane, under one policy decided once for the whole SPA.
- **Aggregate cost is proportional to the answer**, not to the whole portfolio — or the endpoint's ceiling is documented with the portfolio size at which it fails.
- **Correcting a mistake must not destroy its audit thread.** PHI-class rows (meetings, diary notes, secondary injuries) need update commands with version compare-and-swap, so "delete and re-add" stops being the documented fix; one-way completion flags become clearable.
- **Decisions with product, clinical, actuarial or placement consequence get a named owner and a recorded date** rather than a developer's default. Several move money figures already on shipped screens, so each resolution is applied consistently across every surface that shows them.

## Technical Decisions

- **Epics 1–8 stay closed.** This is additive work in a new epic, not a reopening — their `done` statuses are accurate.
- **Spine amendments are the architect's, not story implementation's.** Four need text alongside the stories depending on them: the tier-placement rule for parameter values (which are tunable rules versus domain formulas), the explicit boundary of the derivation registry (a derived value reading a rule document or second table sits outside it, and what that costs the copilot tool registry), a read-path-write clause or the removal of the need for one, and the real wiring status of the mark-stale obligation.
- **The scheduler mechanism is the highest-leverage open decision** — in-process versus worker container. Its reopening trigger has fired: session reaping, background schedule refresh, and missed-backup catch-up all block on it.
- **The mark-stale obligation is binding.** An audited command mutating an embedded source field calls the RAG service's mark-stale command in the same transaction, through the owning service rather than as a second writer — or the embedding source set is documented as excluding those fields. Embedding model dimensions and vector column width must agree.
- **Audit redaction rights stay narrow.** The dedicated redactor role's grants are the invariant the audit design rests on: membership goes to the role that actually assumes it, the grant must not require superuser in every profile, and the assumption window narrows so compromising the API process does not confer redaction rights for a whole day.
- **Retention thresholds are runtime configuration**, driven from a session-level setting rather than baked into a row-security policy; JSONB predicates must not be defeated by a JSON `null` standing in for SQL NULL.
- **Contract artifacts belong in version control** for drift checks to mean anything (the generated OpenAPI document in particular). The request-model policy on unknown and aliased fields is decided once and applied uniformly; one rule value has exactly one wire name, and each version field names the document it versions.
- **Migrations stay Alembic-only** and run clean against a fresh database, including this epic's three schema changes (fatality indicator, effective-dated rate-schedule key, shared-binary uniqueness). Images referenced by the production profile resolve from a registry, with a documented transfer path for air-gapped deployment.
- **Requirements provenance must be fixed.** The document named as the acting requirements source is missing from the working tree; it is either restored or the reference is amended to name what is actually of record.
- **Each story ships its own end-to-end spec** as it leaves backlog, and the story-key-to-spec bijection check is extended to reconcile the tracker against the epics document.

## UX & Interaction Patterns

- **Deploy-skew has a designed state.** Unknown enum values render a designed fallback chip; label maps are UI-owned and cover the full member set, including the timeline tags currently rendering as raw lowercase tokens.
- **Close the in-app-notification requirement where it was raised.** The toast primitive exists but the payment-approval sheet and meeting scheduler were never migrated; both use it, and a hung request leaves the scheduler an exit rather than a stuck state.
- **States must be distinguishable.** A past meeting nobody marked done must not render identically to a completed one; an orphaned decided payment week is flagged rather than silently changing what a schedule heading means; a resumed page walk resets expansion state and never fetches page two without a user action; a failed refetch does not replace already-walked rows with an error surface.
- **Every seam or unavailable capability shows a disabled control with an explanatory tooltip**, not a dead link.

## Cross-Story Dependencies

- **9.13 gates 9.6, 9.10 and 9.12.** Its product and placement decisions are the product manager's and must land first; three of them move money figures on shipped screens.
- **The architect's scheduler decision gates 9.4, 9.5 and 9.7.**
- **9.1 → 9.2 → 9.3 is a chain**: the production profile must boot before credential and transport hardening can be verified in it, and both precede proving the purge cascade in a production-shaped stack. 9.1 is the only gate story with no upstream decision dependency, so it goes first.
- **9.8, 9.9 and 9.11 are independent** and schedule freely against capacity.
- **9.6's schema work** (fatality indicator, effective-dated rate schedule) changes how classification and benefit figures are computed, so every surface consuming those values moves with it.
- **All thirteen share one register dependency**: each writes resolution records into the entries it answers, and the first story to land also carries the outstanding partition of the ~65 no-work entries into an accepted-limits section.
