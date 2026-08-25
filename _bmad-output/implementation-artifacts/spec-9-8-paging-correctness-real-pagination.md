---
title: 'Story 9.8: Paging Correctness & Real Pagination'
type: 'bugfix'
created: '2026-08-25'
baseline_revision: 'e0a7668db1bdb25bf7a1a57bf6198a168c7e755c'
final_revision: '75f78243beba94d0e0495caceb3cb2f038ce133d'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-lineworker-2026-08-07/ARCHITECTURE-SPINE.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** Five paging surfaces publish figures that are artefacts of paging rather than of the book: the worklist, drill-through and queue cursors are offsets, so a claim leaving the population between two requests slides the window and silently skips a row; `/glossary` and `/personas` publish a `{items, nextCursor, total}` envelope with no `LIMIT`, a structurally-null cursor and `total = len(items)`; `list_meetings` re-`COUNT(*)`s the whole book on every page; every queue "Show more" computes four stage groups and discards three; no date-sensitive list states the `as_of` it resolved, so page two shows deadline-driven actions aged against a date up to `MAX_CURSOR_AGE` (7 days) old; and both dashboard tables auto-fetch a page nobody clicked while a failed refetch destroys already-walked rows.

**Approach:** Move the three ranked lists from offset to keyset resumption on the existing total order, extend Story 7.2's proven `asOf` publication to every date-sensitive list payload, make the two fake-paginated endpoints honestly pageable, drop `list_meetings`' per-page recount to the first page only (the treatment `emails.py` already ships), narrow the queue's paging request with a `groups` parameter, and fix the two SPA paging surfaces so expansion state follows the cursor and a failed refetch is non-destructive.

## Boundaries & Constraints

**Always:**
- Keyset resumption happens **after** the Python fold, over `priority.order_key` = `(-score, claim_id)` (`server/services/worklist/priority.py:160`), which is already a documented total order. The score is never expressed as a SQL `ORDER BY` — AD-2 fixes priority scoring in exactly one place, in Python.
- A cursor whose shape or pinned versions no longer apply is refused with 400 `/problems/invalid-cursor`. Never silently reinterpreted, never a silent page one.
- `as_of` stays a keyword-only service seam resolved once as `today = as_of or utc_today()`, returned on the frozen result dataclass, copied by the router onto an `as_of: date` response field. This is the `/dashboard/trends` pattern verbatim.
- `total` means the size of the whole scoped list, never `len(items)`. Where it cannot be had cheaply on every page it is `None` and the field is `int | None`, per the spine's `total?`.
- Money stays integer cents; all existing scope predicates (AD-7) and audit behaviour are untouched.
- Every amended test keeps asserting the property it was written to defend; a test is amended only where the contract genuinely changed, and the amendment is stated in its docstring.

**Block If:**
- Satisfying keyset resumption appears to require the priority score, or any derived rank, in a SQL `ORDER BY` or a materialized ranking column — that is an AD-2 breach and an architecture decision.
- The accepted-limits partition surfaces a register entry whose triage-table assignment is genuinely ambiguous (it reads as open work rather than an accepted limit) — reclassifying someone else's recorded reasoning is not a dev-time call.
- Closing the UTC-versus-viewer-local date divergence on the dashboard paths appears to require deciding a viewer-day policy for those endpoints — that is a product decision and sits with 9.13.
- Making `/personas` genuinely pageable appears to require a session or a scope decision on a deliberately pre-auth, unauthenticated endpoint.

**Never:**
- Never remove or relocate the GET-that-writes (`materialize_schedule`, reached from `GET /claims/{id}` and `GET /claims/{id}/financials`). That is **Story 9.7**, which is blocked on the Architect's scheduler decision. This story only makes a walk able to *detect* the rewrite.
- Never reduce the O(scope) per-page fold. Re-ranking the whole scoped book on every page request is **Story 9.10**'s payload and stays as it is.
- Never refactor the six per-service cursor codecs into a shared `pagination` module. Follow each service's existing idiom.
- Never move sorting, filtering or arithmetic over server payloads into the browser — `web/src/features/queue/noDerivation.test.ts` statically walks `features/dashboard` and `features/queue` and fails the build on it.
- Never accept `asOf` as a wire *input* on the dashboard or queue endpoints. This story publishes the resolved value only.
- No schema change and no Alembic migration: nothing here needs one.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Row leaves population mid-walk | Page 1 walked; a claim ranked above the page boundary is settled; page 2 requested with the returned cursor | Every remaining claim appears exactly once; no row skipped, none repeated | No error expected |
| Cursor predating the deploy | An offset-shaped cursor issued before this change | 400 `/problems/invalid-cursor` | Refused, never a silent page one |
| Cursor pins a superseded rule version | Cursor carries `weights_version` N; document is now N+1 | 400 `/problems/invalid-cursor` | Existing behaviour, preserved |
| Reserve-verdict facet rewritten mid-walk | `filter[reserveVerdict]=light`; a single-claim read moves a claim out of `light` between pages | The cursor detects the change and the page is refused rather than silently skipping | 400 `/problems/invalid-cursor` |
| Glossary paged | `GET /glossary` with the default limit against 25 seed terms | `LIMIT` applied, `nextCursor` non-null while rows remain, `total` from `COUNT(*)`, sort order preserved | No error expected |
| Glossary cursor walked to exhaustion | Successive `cursor` values followed to the end | Every term visited exactly once, in `sort_order`; `nextCursor` null exactly once, on the last page | No error expected |
| Personas paged | `GET /personas` unauthenticated with a cursor | Real `LIMIT` and cursor; no scope data leaks; still reachable with no session | 400 on a malformed cursor |
| Meetings page two | `list_meetings` with a cursor | `total` is `None` on cursor pages, present on the first; no whole-book `COUNT(*)` issued | No error expected |
| Queue "Show more" on one stage | `groups` parameter naming a single stage | Only that group is ranked and serialised; the other three are not computed | No error expected |
| Queue initial load | No `groups` parameter | All four groups returned, unchanged — "every group is always the truth" survives | No error expected |
| Date-sensitive payload | Any of queue, worklist, drill-through, trends | Payload publishes the `asOf` it resolved | No error expected |
| Base refetch onto a new first cursor | User walked to page 3; base query refetches and mints a different first cursor | Accumulated pages are discarded coherently, expansion state resets, and **no** page-two fetch fires without a click | No error expected |
| Failed refetch with walked rows | Base query refetch rejects while `data` is retained | Already-walked rows stay rendered; a non-destructive inline warning appears | Full-height alert reserved for the no-data case |

</intent-contract>

## Code Map

- `server/services/worklist/priority.py:160` -- `order_key = (-score, claim_id)`, the documented total order the keyset cursor resumes on
- `server/services/worklist/priority_claims.py:314` -- worklist `Cursor` (offset), encode `:356`, decode `:379`, paging `:822-857`
- `server/services/worklist/drill_through.py:845` -- drill `Cursor` (offset + `filters` + `bands_version`); docstring `:874-880` already concedes the unpinnable verdict; paging `:1620-1661`
- `server/services/worklist/queue.py:99` -- canonical `MAX_CURSOR_AGE`/`MIN_PAGE_LIMIT`/`MAX_PAGE_LIMIT`; `Cursor` `:107`; four-group loop `:506-518`
- `server/services/claims/meetings.py:226` -- **keyset precedent** `(date, time, id)`; `list_meetings` `:643`, unconditional recount `:744-757`
- `server/services/claims/emails.py:1039` -- **the recount fix to copy**: `total = None if decoded is not None else await count(...)`
- `server/services/worklist/trends.py:1366` -- the `today = as_of or utc_today()` seam; published via `dashboard.py:2873`
- `server/api/routers/dashboard.py:905-918` -- `PriorityClaimsResponse` (no `asOf`); `:1207-1221` `DrillClaimsResponse` (no `asOf`); `:1223` `_drill_filters`
- `server/api/routers/claims.py:168-196` -- `StageGroupResponse`/`StageGroupsResponse`; `:275-300` queue params; `:286` "Never narrows the response"
- `server/api/routers/glossary.py:41-68` -- envelope, no `LIMIT`, `total = len(items)`; repo `server/data/repositories/glossary.py:30`
- `server/api/routers/auth.py:62-93` -- personas envelope, same shape; repo `server/data/repositories/identity.py:85`
- `server/api/schemas.py:17` -- `ApiModel`, the camelCase alias generator; there is no shared list-envelope class
- `web/src/features/dashboard/PriorityClaimsTable.tsx:360` -- `expanded` boolean; destructive base-error branch `:430`; non-destructive page-error branch `:501`
- `web/src/features/dashboard/drill/DrillClaimsPage.tsx:103` -- same pair; `moveTo()` `:170` already resets on filter change
- `web/src/api/dashboard.ts:201`/`:276` -- the two `useInfiniteQuery` hooks keyed on `firstCursor`
- `web/src/api/claims.ts:75-97` -- `useStageGroupPages`; discards three groups at `:91`
- `web/src/api/queryKeys.ts:127`/`:169` -- the cursor-keyed cache keys whose swing causes the auto-fetch
- `web/src/features/queue/noDerivation.test.ts` -- static guard constraining any browser-side fix
- `_bmad-output/implementation-artifacts/deferred-work.md` -- register; 9-8 payload row in the triage table, entries at `:41`, `:48`, `:79-100`, `:391`, `:510`, `:569-628`

## Tasks & Acceptance

**Execution:**
- [x] `server/services/worklist/priority_claims.py` -- replace the offset `Cursor` with keyset resumption on `(-score, claim_id)`; publish the resolved `as_of` on the result dataclass -- AC 1, AC 2; the skip is inherent to offset
- [x] `server/services/worklist/drill_through.py` -- same keyset change preserving `filters`/`bands_version` pinning; add a cursor member that pins the reserve-verdict facet so a mid-walk rewrite is refused, not skipped; publish `as_of` -- AC 1, AC 2, AC 6
- [x] `server/services/worklist/queue.py` -- same keyset change; honour a `groups` narrowing so only requested stages are ranked, defaulting to all four; publish `as_of` -- AC 1 (see Design Notes), AC 2, AC 5
- [x] `server/api/routers/dashboard.py` -- add `as_of: date` to `PriorityClaimsResponse` and `DrillClaimsResponse`, populated from the service -- AC 2
- [x] `server/api/routers/claims.py` -- add the `groups` query parameter to `GET /claims/queue` and `as_of` to the queue envelope; correct the `stage` docstring that promises the response is never narrowed -- AC 2, AC 5
- [x] `server/api/routers/glossary.py` + `server/data/repositories/glossary.py` -- apply `LIMIT`, mint a real cursor, source `total` from `COUNT(*)`, accept `filter[…]`/`sort` -- AC 4
- [x] `server/api/routers/auth.py` + `server/data/repositories/identity.py` -- same for `/personas`, preserving unauthenticated reachability and leaking no scope data -- AC 4
- [x] `server/services/claims/meetings.py` -- compute `total` on the first page only, widening `MeetingPage.total` and `MeetingListResponse.total` to `int | None`, following `emails.py:1039` -- AC 4
- [x] `web/src/features/dashboard/PriorityClaimsTable.tsx` -- reset `expanded` when the first cursor changes so no page-two fetch fires unclicked; test `isError` **after** the data branch so a failed refetch keeps walked rows behind an inline warning -- AC 3
- [x] `web/src/features/dashboard/drill/DrillClaimsPage.tsx` -- identical pair of fixes, keeping the existing filter-change reset -- AC 3
- [x] `web/src/api/claims.ts` -- send `groups` for incremental pages while the initial load stays unnarrowed -- AC 5
- [x] `web/src/api/schema.d.ts` -- regenerate via `npm run generate:api` and commit -- CI gates staleness
- [x] `server/tests/` -- amend `test_glossary.py` (`test_the_endpoint_takes_no_parameters`, `test_camel_case_envelope_is_published`), `test_auth.py` (`test_personas_groups_the_seed_by_role`), `test_meetings.py`, `test_priority_claims.py`, `test_drill_through.py`, `test_claims_queue.py`; add keyset-specific tests covering every I/O Matrix row -- these tests pin the contracts this story deliberately changes
- [x] `web/src/features/dashboard/PriorityClaimsTable.test.tsx` + `drill/DrillClaimsPage.test.tsx` -- add cases for cursor-swing reset, no-unclicked-fetch, and non-destructive refetch failure -- AC 3
- [x] `e2e/stories/9-8-paging-correctness-real-pagination.spec.ts` -- new story spec with one `@smoke` test, tagged `@story:9-8-paging-correctness-real-pagination` -- **required**: `scripts/lint_story_specs` enforces the sprint-status bijection in CI
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- write `resolution:` blocks into every entry this story answers, and, as the first Epic 9 story to land, move the ~65 accept-and-close entries under a new `## Accepted limits` heading, each with a one-line statement of what was accepted and why -- epic register exit criterion
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- set `epic-9` to `in-progress` and `9-8-paging-correctness-real-pagination` to its completed status -- keeps the bijection lint honest

**Acceptance Criteria:**
- Given a cursor issued under the previous offset contract, when it is replayed after this change, then it is refused with 400 `/problems/invalid-cursor` and never silently reinterpreted.
- Given the full CI suite, when it runs, then `noDerivation.test.ts` still passes — no fix moved derivation into the browser.
- Given the register, when the story closes, then no entry it answers lacks a `resolution:` block, and no accept-and-close entry remains outside `## Accepted limits`.
- Given `scripts/lint_story_specs`, when it runs, then the 9-8 story key reconciles against both `sprint-status.yaml` and `e2e/stories/`.

## Spec Change Log

Three implementation decisions the Intent did not settle, recorded because a
reviewer would otherwise have to infer them from a diff.

**1. A key past the end of a list is an ended walk, not a 400.** The offset
cursors refused an offset at or past the end, on the argument that an empty page
beside a non-zero `total` and a null `nextCursor` reads as a list that is
populated and finished at once. Under a keyset that state is no longer
contradictory: it means every row after the caller's position has left the list,
which is a walk that has genuinely ended, and the three fields say exactly that.
Keeping the refusal would force a reload for a walk that finished correctly. The
I/O matrix does not name this case; the three tests that pinned the old
behaviour were amended with the argument in their docstrings
(`test_queue_assembly.py`, `test_claims_queue.py`, and the `offset-past-the-end`
parameters in `test_priority_claims.py` / `test_drill_through.py`, which were
replaced by inputs no page could have minted — a non-finite score and an
offset-shaped token).

**2. `StageGroup.tsx` keeps both SPA paging rough edges.** AC 3 and the task
list name the two dashboard surfaces, and both are fixed. The queue's group is
the third instance of the same idiom, and it is *not* fixed: its `expanded`
lives in `useStageExpansion` above the queue pane and is read by the detail pane
too, so resetting it from inside the group means writing to a parent's state
during render. That is a change to the shared expansion hook rather than the six
lines the two tables needed, and making it here would have put a queue-and-detail
regression risk in a paging diff. Recorded in the register's 5.4 and 5.5 entries
rather than left implicit.

**3. Where the two new cursor codecs live.** `/glossary` and `/personas` have no
service — reference and identity data, no command, no policy — so their codecs
sit in `data/repositories/glossary.py` and `data/repositories/identity.py`,
beside the `ORDER BY` each names, with a locally-declared `InvalidCursor`. The
alternatives were a thin router holding the only statement of what "after" means,
or importing the worklist's exception into `data/`, which is the one direction
AD-1's dependency rule forbids. This follows the Never list's "each service's
existing idiom" rather than departing from it: six codecs, six owners, no shared
`pagination` module.

## Review Triage Log

### 2026-08-25 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 15: (high 2, medium 4, low 9)
- defer: 1: (high 0, medium 1, low 0)
- reject: 1
- addressed_findings:
  - `[high]` `[patch]` A queue cursor whose stage was absent from `groups` was silently served as page one with a 200 — the one answer the spec's Always forbids. `queue.py` now refuses it as `/problems/invalid-cursor`, with a paired test.
  - `[high]` `[patch]` `_verdict_digest` hashed the whole filtered ranked list, so an ordinary settle or stage edit produced a 400 claiming the reserve verdicts had been recomputed — false, and contradicting the I/O matrix's own row-leaves-population row. Now a conjunction of a pre-facet `_population_digest` and the partition digest, so only a genuine verdict rewrite refuses; a forged cursor missing either pin is refused too. Two tests: the rewrite still refuses, an ordinary population change walks.
  - `[medium]` `[patch]` The drill-through case labelled `offset-shaped-cursor-from-before-the-keyset-change` carried a valid keyset payload that `decode_cursor` accepted at today's date; the route-level 400 came from a version mismatch, so AC 1's headline compatibility break was untested. Payload made genuinely offset-shaped.
  - `[medium]` `[patch]` `test_a_forged_cursor_carrying_infinity_is_a_400_not_a_500` passed vacuously — the infinity sat in `o`, no longer read, so it died on `KeyError` and never reached the `ArithmeticError` guard it defends. Moved to a field that is read.
  - `[medium]` `[patch]` A forged cursor carrying an integer past int32 reached asyncpg as a `DataError` and escaped as a 500 — on `/personas`, whose decoder is the only validator before auth. Both decoders now bound integer members, with tests.
  - `[medium]` `[patch]` `StageGroupsResponse`'s four optional fields were papered over client-side with a non-null assertion that would have thrown inside the query; an absent group is now a handled end-of-walk.
  - `[low]` `[patch]` Nine further fixes: meetings `total` nullability narrowed before render; persona-count comment corrected to ten; the spec's own e2e grep corrected to the repo's `@story:9-8` convention (it had been selecting only the smoke test); import-time drift guards converted from `assert` to explicit raises; refusal helpers typed `NoReturn`; three `Cursor` docstrings corrected to stop overclaiming a guarantee keyset does not give; an empty `groups` refused rather than answered with four nulls; the `filtered_total` cost note; and the `encode_cursor` docstring's widening argument extended to address the priority score it now carries.


## Design Notes

**Why keyset is legal here.** The register records that keyset was skipped because "keyset pagination would need the sort key in SQL, which is what AD-2 forbids". That reasoning holds only for a SQL `ORDER BY`. `priority.order_key` is already a total order computed in Python, and its docstring says the tie-break exists precisely so "a cursor into the list would [not] repeat one claim and drop another". Resuming *after a key* in the already-ranked Python list is keyset semantics with the score still computed exactly once. The per-page re-rank cost is untouched and remains Story 9.10's.

```python
# after the fold, instead of ranked[offset : offset + page_size]
start = bisect_right_by_key(ranked, decoded.last_key) if decoded else 0
window = ranked[start : start + page_size]
```

**Why the queue is included.** AC 1's *Given* names the worklist and drill-through. The queue is included deliberately: AC 2 and AC 5 already amend it, it shares the identical offset shape, and the Story 5.4 register entry states the fix "should be made once, for both lists, rather than leaving the queue and the worklist paginating differently". Leaving the handler's primary surface on offset would preserve the exact defect on the most-used screen. Called out here rather than folded in silently.

**Why the SPA bug is a cache-key swing, not a stray effect.** There is no `IntersectionObserver` and no `useEffect` on either path. The infinite query is keyed on `firstCursor`; when the base query refetches onto a new cursor the component swings onto a fresh empty entry while `expanded` stays `true`, and `enabled: expanded && firstCursor !== null` fetches immediately. Resetting `expanded` on cursor change closes both halves of AC 3's second clause at once.

**What the verdict pin can and cannot do.** `ReserveVerdict` is derived on every read from `payment_schedule_week.status`, which a single-claim GET rewrites. This story cannot stop that write — that is 9.7. It can carry a pin so the walk *detects* the rewrite and refuses, which is what "the cursor can pin it" asks and what `bands_version` already does for the rule document.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . ../deploy && uv run ruff format --check . ../deploy` -- expected: clean
- `cd lineworker/server && uv run mypy .` -- expected: clean under `strict = true`
- `cd lineworker/server && uv run pytest` -- expected: all pass, including amended envelope and cursor tests
- `cd lineworker/server && uv run python -m scripts.lint_log_phi` -- expected: clean
- `cd lineworker/server && uv run python -m scripts.lint_story_specs` -- expected: 9-8 reconciles against sprint-status and e2e/stories
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts` -- expected: no diff after committing the regenerated schema
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: all pass, `noDerivation.test.ts` included
- `cd lineworker/e2e && npm run typecheck` -- expected: clean
- `cd lineworker/e2e && docker compose -f ../deploy/compose.e2e.yaml up -d --build --wait && npx playwright test --grep "@story:9-8\b|@smoke\b"` -- expected: pass; tear down with `docker compose -f ../deploy/compose.e2e.yaml down -v`

## Auto Run Result

Status: **done**
Date: 2026-08-25
Baseline: `e0a7668` · Branch: `development`

### What was implemented

Five paging surfaces stopped publishing figures that were artefacts of paging. The three
ranked lists (queue, worklist, drill-through) moved from offset cursors to keyset
resumption on `priority.order_key` — a `bisect_right` **after** the Python fold, so the
priority score is still computed exactly once and nothing reaches SQL (AD-2 intact, and
the register's recorded reason for skipping keyset turned out to cover only a SQL
`ORDER BY`). Every date-sensitive list payload now publishes the `asOf` it resolved,
extending Story 7.2's pattern. `/glossary` and `/personas` became genuinely pageable.
`list_meetings` counts on the first page only. The queue gained a `groups` narrowing.
Two SPA tables reset expansion on a cursor swing and no longer destroy walked rows on a
failed refetch.

### Files changed

- `services/worklist/{priority_claims,drill_through,queue}.py` — keyset cursors, `as_of`
  publication, `groups` narrowing, the reserve-verdict pin
- `api/routers/{dashboard,claims,glossary,auth,diary}.py` — `asOf` fields, `groups`
  parameter, real pagination, `NoReturn` refusal helpers
- `data/repositories/{glossary,identity}.py` — keyset cursors, int32 bounds, drift guards
- `services/claims/meetings.py` — first-page-only `total`
- `web/src/api/{claims,meetings,schema.d.ts}.ts` — `groups`, nullability, regenerated schema
- `web/src/features/dashboard/PriorityClaimsTable.tsx`, `drill/DrillClaimsPage.tsx`,
  `features/queue/QueuePane.tsx` — expansion reset, non-destructive refetch error
- 11 server test files, 4 web test files, `e2e/stories/9-8-…spec.ts` (new)
- `deferred-work.md` — 15 `resolution:` blocks, 66 entries partitioned under
  `## Accepted limits`, 1 new deferral
- `sprint-status.yaml` — `epic-9: in-progress`, `9-8: review`

### Review findings

15 patches applied (2 high, 4 medium, 9 low), 1 deferred, 1 rejected. No intent gaps and
no spec-level defects, so no repair loopback. Full breakdown in the Review Triage Log.

### Verification performed

All commands re-run independently after patching, not taken on report:

| Command | Result |
|---|---|
| `ruff check . ../deploy` + `format --check` | pass, 320 files |
| `mypy .` (strict) | pass, 304 files |
| `pytest` (disposable pgaudit Postgres, README flag set) | **3556 passed, 4 skipped** |
| `lint_log_phi` | pass |
| `lint_story_specs` | pass — 41 keys, each with one spec, each tagged |
| `npm run generate:api` idempotence | pass — no-op |
| web `lint` / `typecheck` / `test` | 0 errors (12 pre-existing warnings), **798 passed** |
| e2e `typecheck` | pass |
| `playwright --grep "@story:9-8\b\|@smoke\b"` | **45 passed** |

Register integrity was checked directly: 245 entries before and after, and the summary-line
multiset is byte-identical to `HEAD`, so the 66 moved entries were moved and not rewritten.

### Residual risks

1. **A row whose order key changes mid-walk is still skipped or repeated.** Keyset fixes a
   row *leaving* the population, which is what AC 1 asks. An edit that moves a claim across
   the cursor key is a narrower residual, now recorded in the register with the reason it
   was not closed: pinning a rank digest on every list is the same trap the reserve-verdict
   pin just demonstrated, one size larger.
2. **One register entry left unclassified, deliberately.** A Story 2.5 entry bundles two
   findings with two destinations; reclassifying someone else's recorded reasoning is a
   triage decision, not a dev-time one. It is named under its own heading in the triage table.
3. **`StageGroup.tsx` keeps both SPA rough edges.** Its expansion state lives in a shared
   hook the detail pane also reads, so the fix is a change to that hook rather than the six
   lines the two dashboard tables needed. Recorded in the 5.4/5.5 resolutions.
4. **`followup_review_recommended: true`** — 15 patches across server, repositories, routers,
   SPA and tests, two of them high-severity with behavioural and contract impact, and two
   more that restored test coverage which had been passing vacuously.
