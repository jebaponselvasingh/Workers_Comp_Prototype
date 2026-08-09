# Spine Review — AD-15 E2E Gate Update (Delta Review)

- **Document:** `ARCHITECTURE-SPINE.md` (lineworker, updated 2026-08-09)
- **Scope of review:** Delta only — new AD-15, amended Testing & CI convention row, Playwright 1.61.x Stack row, `e2e/` source-tree entry, browser-matrix Deferred bullet — plus interactions with the pre-existing, already-gated spine.
- **Rubric:** good-spine checklist (real divergence points fixed and none missed; Rule enforceable and actually preventing its stated divergences; deferrals that cannot let two units diverge; no contradiction/weakening of existing ADs, esp. AD-5/AD-7/AD-11/AD-14; no touched dimension left silent).

## Verdict

**CONDITIONAL PASS.** The delta is well-aimed: it fixes the three real story-builder divergence points (per-story harness invention, prose-flaky AI assertions, "done on unit tests alone") with an enforceable file-naming + tag + done-gate rule, and it is consistent with AD-1 (real stack, no mocked API), AD-5 (no cloud, Ollama-absent default), and AD-14 (asserting the honest `ai_unavailable` path is exactly the right E2E posture). But the rule as written contains one internal contradiction that guts gate coverage of the spine's highest-risk behavior (the AD-6 interrupt gate), and it leaves two touched dimensions silent — parallelism vs. AD-6 single-flight, and the PHI class of Playwright artifacts under AD-11 — each of which is a live two-builders-diverge point. Fix the one critical and two high findings; the mediums/lows can ride the next editorial pass.

---

## Findings

### CRITICAL

**C1 — The interrupt approve/reject round-trip is untestable in the default gate as specified, and AD-15's own text contradicts itself about it.**
AD-15 requires copilot specs to assert "interrupt approve/reject UI" *and* requires the default suite to run "with Ollama absent." But per AD-6/AD-14, every graph path that reaches `interrupt()` (RTW letter is explicitly "tool-merge → LLM prose → `interrupt()`"; write proposals are LLM-emitted `pending_approval` payloads) is a `requires_llm: true` path, which with Ollama absent terminates in `error: ai_unavailable` before any interrupt exists. So under the rule as written, every approve/reject/stale-CAS-409 E2E spec must carry `@live-ai` — which is *excluded from the gate*. Net effect: the human-approval gate, the single most safety-critical behavior in the spine (AD-6's whole reason to exist), is the one behavior permanently outside the merge gate, while the convention row's stub-chat-model coverage stops at the graph level, never through the browser. Two story builders will resolve this differently: one invents a private fake-Ollama container, the other tags everything `@live-ai` and ships with the gate never exercising approval.
**Fix:** Add one sentence to AD-15 sanctioning a fixtures-owned deterministic stub model service in `e2e/fixtures/` (an Ollama-API-compatible fake on the internal Compose network, scripted responses per spec) as the *only* way to drive `requires_llm: true` paths in the gate; state explicitly that (a) it does not violate AD-5 (it is local and not a cloud path) and (b) it does not violate AD-14's "no canned text as model output" rule, which governs production behavior, not test harnesses. Require the interrupt approve/reject/stale-409 specs to run in the default gate against the stub; reserve `@live-ai` for real-model quality checks only.

### HIGH

**H1 — Parallelism vs. shared seed/reset and AD-6 single-flight is silent; "reset per worker run" is ambiguous.**
Playwright defaults to parallel workers. AD-15 gives the suite one composed stack, one DB, "one deterministic DB seed/reset … reset per worker run." If that phrase means per-worker resets against a *shared* Postgres, worker A's reset destroys worker B's in-flight state; and two workers touching the same claim's copilot thread trip AD-6's single-flight 409 by design, producing flakes that builders will "fix" by loosening assertions. If it means per-worker databases, nobody has said how nginx→FastAPI routes a worker to its database. This is precisely the kind of silent dimension where two story builders diverge (one sets `workers: 1`, another sets `fullyParallel` and starts retry-masking).
**Fix:** Amend AD-15 to fix the concurrency model in one clause — simplest consistent choice: `workers: 1`, `fullyParallel: false`, seed/reset once per suite run plus per-spec reset hooks where a spec mutates data; note that the AD-6 single-flight 409 is a *feature to assert*, never a condition to retry around. (Per-worker databases can be a later amendment if suite time demands it — defer that explicitly, like the browser matrix.)

**H2 — PHI class of Playwright artifacts (traces, screenshots, videos, HAR) is silent; AD-11 as written would make CI uploads a compliance surface.**
Playwright's on-failure traces/screenshots/videos capture rendered claim screens; HAR/trace files capture full API payloads. AD-11 declares "everything derived from claim data" PHI-class, encrypted, purge-cascaded. Read literally, every CI artifact upload becomes an encrypted, retention-managed PHI store — obviously not intended, but the spine never says why not: it never declares the E2E seed's data classification. One builder will treat artifacts as PHI and block artifact upload; another won't think about it; and nothing today *forbids* pointing `e2e/` at an environment holding real claims, at which point artifacts genuinely are PHI.
**Fix:** Add to AD-15: the `e2e/` seed is **synthetic-only by rule** (the dev seed-migration personas and demo claims; no real claim data may ever enter an E2E target environment), and therefore Playwright artifacts from it are expressly *not* PHI-class under AD-11; running the suite against any environment containing real PHI is prohibited. One sentence closes both the classification hole and the misuse path.

### MEDIUM

**M1 — How the E2E stack exists in CI is unstated.**
AD-15 demands "the real composed stack (nginx → FastAPI → PostgreSQL)" and the Structural Seed defines exactly two environments, `dev` (Compose, CPU) and `prod` — no CI environment, no statement of which compose file CI runs, and no statement of how "Ollama absent" is arranged (service omitted? stopped? dev compose currently includes it). Builders of the first story will each invent a CI compose incantation.
**Fix:** One line in AD-15 or the Operations row: CI runs the `dev` compose profile (CPU, no GPU toolkit) with the `ollama` service not started (and, once C1 lands, the stub model service in its place); `deploy/` owns a `compose.ci.yaml` or profile flag.

**M2 — "The touched story's spec" has no resolution mechanism.**
Nothing says how CI maps a PR to its story key — branch name, sprint-status.yaml state, changed files? Two builders will wire this differently, and a mis-mapped PR silently runs zero story specs while still passing `@smoke`.
**Fix:** Fix the mechanism in the convention row: the PR gate runs every spec under `e2e/stories/` whose file was added/modified in the PR, plus the spec matching the story key currently `in-progress`/`review` in `sprint-status.yaml`; if that set is empty the gate fails loudly rather than passing vacuously.

**M3 — `@smoke` membership is undefined.**
The tag is load-bearing (it is the only cross-story coverage a PR gets) but has no admission criteria, so it will either bloat until PR runs are slow or stay empty.
**Fix:** Define it in AD-15 in one clause — e.g. exactly one `@smoke` spec per epic, designated in the epic's first story, covering login-as-persona plus that epic's primary happy path.

### LOW

**L1 — The login-as-persona fixture silently rides the deferred IdP decision.** When the Deferred "Identity provider" bullet resolves to OIDC, "drives the real auth path" breaks in CI (no IdP in the compose stack). **Fix:** add one clause to the IdP Deferred bullet: the E2E login fixture is a rider on this decision (local-credential path retained for CI, or a test IdP added to the CI compose).

**L2 — "Stay green forever" lacks an amendment path.** Later stories legitimately change earlier behavior; without a stated path, builders will delete or skip old specs. **Fix:** append: earlier specs may be *amended* by the story that changes the behavior (never skipped/deleted), with the change noted in that story's record — a correct-course artifact for anything larger.

**L3 — The `data-testid` obligation is visible only to spec authors, not SPA builders.** AD-15 mandates role/`data-testid` selectors, but no `web/` convention obliges component builders to emit stable testids, so the burden lands as spec-side archaeology. **Fix:** one clause in AD-9 or the Naming convention row: interactive elements not addressable by accessible role carry a stable kebab-case `data-testid`, named for the entity + action.

---

## Checklist disposition

| Rubric question | Disposition |
| --- | --- |
| Fixes the real divergence points for story builders, misses none | Fixes harness/seed/selectors/done-gate/AI-flake — the right list. Misses parallelism (H1) and PR→story mapping (M2). |
| Rule enforceable; actually prevents stated divergences | Mostly — file path + tag + gate are mechanically checkable. The interrupt-coverage clause is unenforceable as written (C1). |
| Deferrals allow no divergence | Browser-matrix deferral is clean (Chromium-only stated as the binding gate). No new divergent deferral introduced. |
| Contradicts/weakens existing ADs | No weakening of AD-5 (Ollama-absent default is *more* conservative), AD-7 (real auth path, no scope bypass), AD-14 (degradation asserted, not worked around). AD-11 interaction unresolved (H2, silence not contradiction). Internal AD-15↔AD-6/AD-14 contradiction on interrupt testability (C1). |
| Touched dimensions left silent | CI infra (M1), test-data PHI class (H2), parallelism vs single-flight (H1), smoke membership (M3). |

**Recommendation:** Apply C1, H1, H2 as text amendments to AD-15 (three sentences total, plus one Deferred-bullet clause each for M1/L1) before the first story's E2E spec is authored; the gate is not safely enforceable until then.
