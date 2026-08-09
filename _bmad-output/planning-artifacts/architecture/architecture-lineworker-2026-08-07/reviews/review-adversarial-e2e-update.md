# Adversarial Review — AD-15 Story-Scoped Playwright E2E Gate (delta only)

- **Target:** ARCHITECTURE-SPINE.md, delta of 2026-08-09 — AD-15, amended Testing & CI convention row, Playwright 1.61.x stack row, `e2e/` source-tree entry, E2E browser-matrix Deferred entry
- **Method:** two-compliant-implementers attack — every scenario below has both parties obeying AD-15 and every other AD to the letter, yet the build diverges or the gate silently stops gating
- **Date:** 2026-08-09
- **Verdict:** AD-15 is the right gate but is under-specified at several load-bearing joints. Findings F1–F3 and F6 are gate-breaking as written; each finding closes with a tightening that keeps the AD's intent intact.

Severity scale: **Critical** (gate produces wrong answers or cannot be built as written) · **High** (compliant authors reliably diverge or flake) · **Medium** (silent decay or compliance ambiguity) · **Low** (hygiene).

---

## F1 — "Freshly seeded stack" has three readings, and "reset per worker run" self-clobbers on one shared Postgres — **Critical**

**Scenario.** Playwright's default is `fullyParallel: true` with N workers. The fixture row says the DB seed/reset runs "per worker run"; the done-gate says the spec must pass "against a freshly seeded stack." There is exactly one composed stack (nginx → FastAPI → one PostgreSQL). Three compliant readings exist:

1. *Per CI run*: seed once at suite start. Worker A's spec approves a payment on WC-1005 (`payment_scheduled`); worker B's spec, running concurrently or later, asserts WC-1005's seeded reserve figures. B fails or passes depending on scheduling. Both authors obeyed AD-15.
2. *Per worker*: each worker's fixture resets the shared DB at worker start. With N > 1 workers, worker 2's reset truncates the rows worker 1's in-flight spec is mid-assertion on. The suite is nondeterministically red against a fully green application.
3. *Per spec*: isolated but nowhere stated, and directly contradicted by the "per worker run" wording — so no author may assume it.

The same ambiguity splits the done-gate from the merge gate: a story's spec passes its own "freshly seeded" gate run (it ran alone, reading 3 de facto) and then fails in the merge-time full suite (reading 1), turning the "permanent regression floor" red with no code change.

**Tightening.** Amend AD-15 to define both terms exactly:
- The gate configuration is `fullyParallel: false`, `workers: 1` — committed in `e2e/playwright.config.ts` and not overridable by CI flags. (The Deferred list can carry "parallel e2e workers" as a future decision requiring per-worker DB isolation.)
- "Freshly seeded stack" = containers may be reused within a run, but the database is reset **before every spec file** by the shared reset fixture. "Per worker run" wording is deleted.
- A story's done-gate run and the merge-time full suite therefore see identical initial state per spec file; passing one predicts the other.

---

## F2 — Two specs mutating the same seeded claim WC-nnnn: order-dependent merge suite — **Critical** (collapses to Low if F1's per-spec-file reset is adopted)

**Scenario.** Even fully serialized, under a per-run or per-worker reset: story 3-2's spec (payment approval) mutates WC-1005; story 4-1's spec (supervisor drill-through) asserts the seeded aggregate that includes WC-1005's incurred total. On a PR, each runs alone against a fresh seed and passes — the done-gate approves both stories. On merge to `main`, the full suite runs them in filename order and 4-1 fails, or passes, depending on whether `3-2-*.spec.ts` sorts before `4-1-*.spec.ts`. Adding an unrelated story 3-9 later reshuffles nothing, but renaming per F8 does. Both authors obeyed every rule; the regression floor is a coin flip.

**Tightening.** Primary fix is F1's per-spec-file reset — adopt it and this finding closes. As defense in depth, add one sentence to AD-15: *a spec may mutate only claims it declares (exported `const MUTATES: string[]` at the top of the spec, empty for read-only specs); the fixture asserts at teardown that no undeclared claim changed version.* This also documents blast radius for humans debugging cross-spec failures.

---

## F3 — The interrupt approve/reject UI cannot be exercised with Ollama absent: AD-15's two requirements contradict each other — **Critical**

**Scenario.** AD-15 requires copilot specs to assert "interrupt approve/reject UI," and requires the default suite to run "with Ollama absent." But per AD-13, write tools are never exposed to LLM tool selection — a `pending_approval` payload is *emitted by the model*. Per AD-14, every write-proposing flow (RTW letter explicitly included) is `requires_llm: true`, which with Ollama absent returns `error: ai_unavailable` before any interrupt can exist. So with Ollama absent there is **no path that produces an interrupt**. Two compliant authors resolve this oppositely:

- Author A tags the approve/reject spec `@live-ai`. It is excluded from the gate — the single most safety-critical UI in the system (the human gate on AI claim writes, AD-6) ships ungated forever.
- Author B, forbidden from tagging away their story's acceptance criteria, mocks the copilot SSE endpoint to fabricate an `interrupt` event — violating AD-15's "never mocked API responses" and testing a stream the server never emitted.

Either way the gate fails to do its job, and the two specs encode contradictory beliefs about what the no-Ollama stack does.

**Tightening.** The spine already has the precedent: the Testing & CI row mandates graph tests "against a stub chat model." Extend it one level down. Amend AD-15: *the e2e compose profile (F4) includes a deterministic Ollama-API-compatible stub container (test-only; never present in dev or prod compose) that returns scripted responses per QAS key, including a scripted `pending_approval` emission for write-flow specs. "Never mocked" is clarified to mean the application's own API surface (nginx → FastAPI → Postgres are always real); the upstream inference dependency may be stubbed only via this container, only in the e2e profile.* AD-14 degradation specs then run a second stack variant with the stub stopped (or a stub "down" mode) to assert `ai_unavailable`. `@live-ai` remains the tag for real-model runs. AD-5 is untouched: the stub is on the internal network and is not a cloud path.

---

## F4 — No defined compose profile for the gate: specs pass locally against dev compose, fail in CI, and "Ollama absent" is itself ambiguous — **High**

**Scenario.** `deploy/` defines `compose.yaml` and `compose.gpu.yaml`; dev compose includes Ollama ("CPU-only small models allowed"). AD-15 says the default suite runs "with Ollama absent" but names no compose profile, so CI's stack is whatever the CI author improvises. Story implementer A develops their copilot spec against dev compose with Ollama up: `requires_llm: true` actions stream real tokens, and their "structure" assertions capture the success sequence (`messages…done`). The spec passes locally, then fails in CI where the same action yields `error: ai_unavailable`. Meanwhile "absent" has two behaviors: service not in the compose file (connection refused, fast fail) vs. container present-but-stopped or wrong hostname (connect timeout — AD-14's bounded retry now runs its full backoff and the spec's timeout budget decides the verdict). Two CI authors picking different "absent" mechanics get different pass/fail on identical specs.

**Tightening.** Add to AD-15 and the source tree: *`deploy/compose.e2e.yaml` is the only stack the gate runs against — locally and in CI (one committed entrypoint, e.g. `make e2e`, runs compose-up + seed + suite). It contains the F3 stub (or, for degradation runs, points `OLLAMA_URL` at a closed port on an existing host so failure is immediate connection-refused, not timeout). A spec that passes only against dev compose does not satisfy the done-gate; the done-gate is defined as passing via the committed entrypoint.*

---

## F5 — AD-6 single-flight threads: compliant copilot specs 409 each other and themselves — **High**

**Scenario 1 (cross-spec).** The thread key is `(scope, user_id, conversation_seq)` and only nine seeded personas exist. Two copilot specs both log in as the same handler persona and open the same demo claim — same scope, same user, same live `conversation_seq` → **one thread**. Under any future parallelism (or a fixture that reuses a page/session across specs without starting a new conversation), the second spec's message lands while the first spec's run is active or its interrupt is pending, and AD-6 mandates a 409. Both specs obey AD-15's fixture rules; one fails.

**Scenario 2 (intra-spec).** A spec drives a multi-turn acceptance flow: send message → assert → send next message. AD-6 rejects the second message with 409 unless the first run has reached its terminal event (`interrupt | error | done`). On a fast local machine the stream completes before the next `click`; in CI it doesn't. The spec is timing-flaky by construction, and the author who "fixes" it with a retry loop is papering over exactly the 409 that AD-6 says is correct behavior.

**Tightening.** Amend the AD-15 fixtures clause: *`e2e/fixtures/` exports the only permitted copilot driver: a helper that (a) starts a new conversation (`conversation_seq` bump) at test start, and (b) exposes `send()` which resolves only after the run's terminal stream event (`interrupt | error | done`) is observed in the UI — specs never post to the copilot input directly. One dedicated 409 spec asserts the single-flight rejection deliberately; all others are structurally unable to trigger it.* (F1's `workers: 1` removes scenario 1 today; the helper keeps it removed when parallelism is revisited.)

---

## F6 — A compliant seed/reset is impossible with the application DB role: AD-4's grants force every fixture author to invent credentials — **High**

**Scenario.** The reset fixture must return the DB to seed state. But per AD-4 the application role is INSERT-only on `audit_event` (no DELETE), and the only role that may delete audit rows is `audit_redactor`, RLS-constrained to rows older than the 7-year retention floor — which test-run rows never are. LangGraph checkpoint tables are written only by the saver. So a fixture using app credentials **cannot** truncate `audit_event` (or cleanly reset checkpoints), and residue accretes: spec runs pollute audit/timeline-adjacent assertions, and "freshly seeded" is quietly false for one table class. Author A ships exactly that (reset skips what it can't delete — silent divergence). Author B ships a fixture holding the `postgres` superuser password — solving reset by creating an unregistered god-credential that AD-4/AD-11 never accounted for, sitting in `e2e/fixtures/` one copy-paste away from a prod env file. Both are AD-15-compliant; neither is acceptable.

**Tightening.** Amend AD-15: *reset is not row deletion — it is `DROP SCHEMA … CASCADE` + `alembic upgrade head` + seed (which doubles as the Testing & CI row's "migrations run clean against a fresh DB" check on every spec file). It runs as a dedicated `e2e_owner` role defined only in `deploy/compose.e2e.yaml`'s Postgres init — the role does not exist in dev or prod images. Fixtures never hold the application role's or any prod role's credentials.* Register this as a scoped exception under AD-4 the way `audit_redactor` is.

---

## F7 — `@smoke` is undefined (who tags what), and "the touched story's spec" has no resolver — **High**

**Scenario A (empty smoke).** No rule says who applies `@smoke`, so nobody does — a tag with no owner is applied by no compliant implementer, since tagging someone else's spec isn't their story. Every PR gate degenerates to "the touched story's spec" alone. A PR on story 5-2 that regresses story 1-3's login flow passes its PR gate; the breakage surfaces only at merge, on `main`, as a red full suite that blocks everyone — the exact failure mode the PR gate exists to prevent.

**Scenario B (everything is smoke).** The opposite compliant reading: each implementer, wanting their story protected on every PR, tags their own spec `@smoke`. Within two epics the "smoke" suite *is* the full suite and PR time balloons until someone starts skipping the gate.

**Scenario C (no resolver).** "The touched story's spec" — resolved how? A PR that only touches `services/derivations` (shared by many stories) names no story. One CI author maps branch names, another maps `sprint-status.yaml` diffs, a third runs nothing when no mapping hits — the gate passes vacuously on precisely the riskiest PRs (shared-code changes).

**Tightening.** Amend AD-15: *`@smoke` is a curated set owned by sprint planning, not by story implementers — initially the persona-login spec, one queue spec, and the copilot `ai_unavailable` degradation spec; budget ≤ 5 minutes; membership changes only via sprint-planning/correct-course, never in a story PR. Story resolution: every implementation PR declares its story key (branch prefix `story/<key>-…` or PR-title tag), and CI resolves spec = `e2e/stories/<key>.spec.ts`; a PR with no story key (shared/infra change) runs `@smoke` + the full suite of every epic whose services it touches — never zero specs. CI fails the PR if a declared key has no spec file (see F8).*

---

## F8 — Story keys renamed or split in epics vs. frozen spec filenames: the gate passes vacuously — **Medium-High**

**Scenario.** Correct-course renumbers story 2-4 to 2-5 (or splits 2-4 into 2-4a/2-4b) in the epics and `sprint-status.yaml`. The spec file `e2e/stories/2-4-….spec.ts` and its `@story:2-4` tag were committed weeks ago. Implementer A "keeps history clean" and renames the spec to match; implementer B treats shipped specs as the frozen "permanent regression floor" and doesn't touch it. Under A, any tooling keyed on the old tag (and the new 2-4 story that later reuses the freed number) silently binds to the wrong spec. Under B, the F7 resolver looks up `2-5-….spec.ts`, finds nothing, and — absent F7's fail-on-missing rule — the story's done-gate and PR gate both pass **vacuously**. A story reaches `done` having run zero E2E assertions, which is the one outcome AD-15 exists to prevent, achieved by two people following it.

**Tightening.** Amend AD-15: *spec filename and `@story:` tag are bound to the story key at spec creation; any correct-course change that renames, renumbers, or splits a story key must rename the spec file and tag (and split its assertions) in the same change set. A CI structural lint enforces the bijection both ways: every story in `sprint-status.yaml` at `review`/`done` has exactly one matching spec file, and every file in `e2e/stories/` matches exactly one current story key — an orphan on either side fails the lint.* (This lint is also what gives F7's resolver its fail-closed behavior.)

---

## F9 — Playwright traces/screenshots/videos capture claim-shaped data; AD-11 gives no ruling, so one author disables debugging and another builds an unregistered PHI-shaped export path — **Medium**

**Scenario.** Playwright's `trace: 'on-first-retry'` / screenshot-on-failure record full DOM snapshots, network bodies, and console output — i.e., complete claim records as rendered. AD-11 declares "everything derived from claim data" PHI-class, bans PHI from operational logs, and routes all PHI lifecycle through `services/audit`'s purge cascade. The seed is synthetic demo data — but the spine never *says* the seed is exempt from PHI-class treatment. Compliant author A reads AD-11 strictly: traces/screenshots off entirely — every CI failure is now an unreproducible text assertion diff, and the gate's practical debuggability collapses (people start rerunning until green). Compliant author B reads the seed as obviously fake and uploads traces as CI artifacts with default retention on shared runner storage — creating a data-export pipeline that AD-11 governs nowhere, and which becomes a real PHI leak the day anyone points `e2e/` at a staging environment with real data (nothing currently forbids that).

**Tightening.** Two sentences in the spine: *(1) The seed migration dataset is declared synthetic and non-PHI by construction; artifacts derived from it (traces, screenshots, videos, DB dumps of the e2e stack) are exempt from AD-11 handling but retained in the CI artifact store ≤ 14 days. (2) The `e2e/` suite may run only against the `compose.e2e.yaml` stack seeded by the fixture — running it against any environment containing non-seed claim data is forbidden, and the login fixture asserts a seed-marker row before any spec runs (fail-closed).*

---

## F10 — `@live-ai` is excluded from the gate and scheduled nowhere: mandated specs that never execute — **Medium**

**Scenario.** AD-15 requires the tag and excludes it from the gate, but no run is defined for it. Compliant authors write `@live-ai` specs (the letter of the rule) that no pipeline ever executes; six weeks later they're broken against the current stream protocol and nobody knows. Worse, their existence creates false confidence — "the live-model path has specs" — while the only assertions ever exercised are the stub/absent paths. Two authors also diverge on *what* `@live-ai` may assert: one asserts structure only (consistent with the AD), another asserts prose keywords "since it's excluded from the gate anyway," reintroducing exactly the flake class AD-15 bans when someone finally schedules the run.

**Tightening.** One sentence: *`@live-ai` specs obey the same structure-not-prose rule as gated specs and run in a scheduled non-blocking job (e.g. nightly on the GPU host, alongside the deferred answer-quality eval); failures file into sprint status rather than blocking merges. A tag with no scheduled runner may not be required by an AD* — if the nightly can't exist yet, the tag moves to Deferred with the eval harness.

---

## F11 — `data-testid` has no naming convention or ownership: per-story selector dialects in shared components — **Low**

**Scenario.** AD-15 mandates role/`data-testid` selectors but web components ship testids only when some story's spec needs them. Story A's implementer adds `data-testid="claim-row"` to the queue item; story B's implementer, needing the same element a sprint later on a branch, adds `data-testid="queueItem"` beside it (or edits A's, breaking A's frozen spec). Neither violated anything written.

**Tightening.** One line in the conventions row: *testids are kebab-case `<feature>-<element>[-<qualifier>]`, added in the story that ships the component; specs prefer accessible-role selectors and fall back to testid; changing an existing testid is a breaking change to `e2e/` and must update every referencing spec in the same PR (the merge-time full suite enforces this mechanically once F1 makes it deterministic).*

---

## Summary table

| # | Finding | Severity | One-line tightening |
| --- | --- | --- | --- |
| F1 | "Freshly seeded" / "per worker run" ambiguity self-clobbers one shared Postgres | Critical | `workers: 1` committed in config; DB reset before every spec file; delete "per worker run" |
| F2 | Two specs mutating the same WC-nnnn → order-dependent merge suite | Critical | Per-spec-file reset (F1) + declared `MUTATES` list asserted at teardown |
| F3 | Approve/reject interrupt untestable with Ollama absent — internal contradiction | Critical | Deterministic Ollama-API stub container in the e2e profile; "never mocked" = app API only |
| F4 | No compose profile for the gate; "Ollama absent" mechanics undefined | High | `deploy/compose.e2e.yaml` + one committed entrypoint is the only done-gate path |
| F5 | AD-6 single-flight 409s between and within compliant copilot specs | High | Fixture-owned copilot driver: new conversation per test, `send()` awaits terminal event |
| F6 | App DB role (AD-4 grants) cannot perform reset; authors invent credentials | High | Reset = drop schema + alembic + seed under an e2e-profile-only `e2e_owner` role |
| F7 | `@smoke` unowned; "touched story's spec" has no resolver; vacuous pass on shared-code PRs | High | Sprint-planning-owned smoke set; PR story-key declaration; never-zero-specs rule |
| F8 | Story renames vs frozen spec filenames → vacuous done-gate | Medium-High | Rename spec+tag in the same change; CI bijection lint between sprint-status and `e2e/stories/` |
| F9 | Traces/screenshots of claim-shaped data unruled by AD-11 | Medium | Seed declared synthetic/non-PHI; bounded artifact retention; e2e runs only against the e2e stack (fail-closed marker check) |
| F10 | `@live-ai` mandated but scheduled nowhere → spec rot | Medium | Nightly non-blocking run, same structure-only assertion rule; no tag without a runner |
| F11 | No testid naming/ownership convention | Low | Kebab-case `feature-element`, shipped with the component, breaking-change rule |
