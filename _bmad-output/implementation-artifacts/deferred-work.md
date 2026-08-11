# Deferred Work

Items raised in review that were consciously deferred. Each carries the reason it was not actionable at the time.

## Deferred from: code review of 1-3-role-persona-login (2026-08-10)

- **No session reaper; login never revokes existing sessions.** Every `POST /auth/login` inserts a row unconditionally and expired rows are deleted only "on contact" in `resolve_session`, so a persona can hold an arbitrary number of simultaneously-valid tokens and the `session` table is append-mostly. Deferred: how many concurrent sessions a persona may hold (multi-device vs single-session) is a policy decision beyond this story, and the cleanup job needs a scheduler that does not exist yet.
  - *Re-raised by the 2026-08-10 code review of Story 1.4*, with the sharper case: `login` does not revoke **the session presented in its own request cookie**, so a login that skips `POST /auth/logout` (a second tab, or the guard's redirect after an unrelated failure) leaves the previous token live and replayable for the full TTL even though the browser has discarded it. Still deferred for the same reason — revoking the presented cookie is a one-line change, but it *is* the single-session policy decision, made silently. The "↩ Switch" button, the only persona-switch path in the UI, does revoke.
- **`/personas` has no `filter[…]`/`sort`/cursor parameters.** The Lists convention specifies these for all list endpoints; the persona picker returns a fixed ten rows in the `{items, nextCursor, total}` envelope without them, and the role partition happens in the SPA. Deferred: the convention's pagination/filter machinery should be built once, with Epic 2's first genuinely pageable list endpoint, rather than improvised for a ten-row public picker.
- **An idle SPA never notices server-side session expiry.** With `staleTime: 30_000`, `refetchOnWindowFocus: false` and a guard that checks only on mount, a user left on `/dashboard` past `expires_at` keeps seeing the authenticated shell until their next navigation. Deferred: the right fix (periodic revalidation, or a refresh-on-focus policy) is coupled to the session-lifetime decision above.
- **`ApiModel` has no `extra` policy.** With `populate_by_name=True` and Pydantic's default, request models accept both `personaId` and `persona_id` and silently ignore unknown fields, so the camelCase boundary is a convention rather than an enforced contract. Deferred: strictness here is a project-wide decision affecting every future endpoint, not a defect of this story.

## Deferred from: code review of 1-6-domain-glossary (2026-08-10)

- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-domain-glossary.md`
  summary: The list envelope on `GET /api/glossary` asserts a pagination that does not exist — `nextCursor` is structurally always null, the repository read has no `LIMIT`, and `total` is `len(items)` computed from the very list it is returned beside, so a truncation could never be detected.
  evidence: Real, but pre-existing convention rather than a defect of this story: `/personas` shipped the identical shape in Story 1.3, and item 2 of this file already records that the convention's pagination/filter machinery should be built once, with Epic 2's first genuinely pageable list endpoint. The concrete hazard is deferred with it — if a later story adds a `LIMIT` to `list_terms` for any reason, `total` silently changes meaning from "rows that exist" to "rows on this page", and `test_every_persona_gets_every_term_in_order` would fail on `items` first and mask the semantic break. When that machinery lands, `total` should come from a `COUNT(*)` or be dropped (the convention writes it as optional).

- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-domain-glossary.md`
  summary: `data/seed/extract_prototype_seed.py` documents and defaults to `../docs/Workers_Comp_Prototype.html`, which from `server/` resolves to `lineworker/docs/…` — a path that does not exist; the real one is `../../docs/…`.
  evidence: Verified by running it: the documented command fails with `FileNotFoundError`. Predates this story (Story 1.2 shipped it) and belongs to the portfolio seed, not the glossary. Story 1.6's own extractor had the same bug copied into it and fixed there; this one is left alone because re-running it rewrites `seed_data.json`, which is Story 1.2's committed artifact and outside this story's blast radius.

## Deferred from: code review of 2-1-prioritized-filterable-claim-queue (2026-08-11)

- source_spec: `_bmad-output/implementation-artifacts/spec-2-1-prioritized-filterable-claim-queue.md`
  summary: The queue's DB-backed tests build the app on the migration *owner* connection, so the `SELECT`-only grant on `rule_document` is asserted by reading `information_schema` and never by serving a request as `lineworker_app`.
  evidence: Real, and pre-existing rather than introduced here — `tests/test_glossary.py` established the pattern in Story 1.6 and `conftest.py` exposes only `seeded_db_url` (the owner URL). The concrete hazard is that a missing or wrong grant on a new table would 500 every request in the deployed stack while the suite stayed green; only the e2e stack, which does run as the app role, would catch it. The fix is a shared app-role client fixture in `conftest.py`, which belongs with the fixture rather than with any one story.

- source_spec: `_bmad-output/implementation-artifacts/spec-2-1-prioritized-filterable-claim-queue.md`
  summary: A missing, uncompilable or invalid rule document surfaces as an unhandled 500 rather than an RFC 9457 problem document, and it takes `/stats/topbar` down with it.
  evidence: Real: `rules.engine.load` raises when no document is effective on the date, and neither `/claims/queue` nor `/stats/topbar` catches it. Deferred because a 500 is a defensible answer to a broken deployment — the documents are seeded by migration, so their absence means the migration chain did not run — and because choosing the right shape (503 + `Retry-After`? a degraded queue?) is a service-availability policy decision that spans every future JDM consumer, not just this endpoint.

- source_spec: `_bmad-output/implementation-artifacts/spec-2-1-prioritized-filterable-claim-queue.md`
  summary: The seed migration inserts the committed JDM documents without compiling them, so a syntactically valid but uncompilable document passes the migration and fails at request time instead.
  evidence: Real but narrow: `tests/test_rules_engine.py` evaluates both committed documents, and CI runs it, so a bad document cannot reach `main` — the exposure is a hand-edited document on a developer's branch. Compiling inside `upgrade()` would also make the migration chain depend on the ZEN runtime, which is a heavier coupling than the failure warrants.

- source_spec: `_bmad-output/implementation-artifacts/spec-2-1-prioritized-filterable-claim-queue.md`
  summary: Every queue request reads, derives and scores the caller's entire scoped portfolio before slicing — including page-2 requests, which redo all of it — because the sort key is Python arithmetic and cannot be pushed into SQL.
  evidence: Real and by design at this size (100 claims, and a `scope_all` persona is still the whole table). It is recorded because the cost is proportional to the portfolio and the queue is on the handler's critical path: the first portfolio that makes this matter needs either a materialised score column refreshed on write (which AD-10 forbids as a user-writable column but would permit as a derived cache) or a scoring pass pushed into SQL from the same JDM parameters. Neither is a decision this story should make.

- source_spec: `_bmad-output/implementation-artifacts/spec-2-1-prioritized-filterable-claim-queue.md`
  summary: `utc_today` is defined in `rules/engine.py` and re-exported through `services/derivations`, so `services/worklist/queue.py` imports its clock from the derivations package while importing its parameters from `rules.parameters` — two doors to one value.
  evidence: Real, cosmetic today, and a genuine trap later: the moment a second clock source appears (a request-scoped "as of" for back-dated reporting, say), the two import paths make it easy to have half the derivations on one and half on the other. It belongs in a small `services/clock` or on the caller context, which is a cross-cutting placement decision.

- source_spec: `_bmad-output/implementation-artifacts/spec-2-1-prioritized-filterable-claim-queue.md`
  summary: Both queue oracles resolve "today" independently of the server, so a test run that straddles midnight UTC disagrees with the stack by a day on every `days_open`, score, order and marker expectation.
  evidence: Real and acknowledged in `e2e/fixtures/seed.ts`'s docstring but not in `tests/seed_fixture.py`'s. The exposure is a few seconds per day, and the alternative — pinning a date the running stack does not share — trades a rare flake for a permanent fiction. Recorded because the honest fix is for the API to report the `asOf` it resolved and for both oracles to consume it, which is a small wire change better made when a second date-sensitive endpoint needs it.
