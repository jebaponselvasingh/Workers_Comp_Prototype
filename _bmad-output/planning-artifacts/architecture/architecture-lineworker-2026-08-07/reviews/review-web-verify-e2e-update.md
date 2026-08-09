# Web-Verification Review — Playwright E2E Update (AD-15, Testing & CI row, Stack row)

- **Review date:** 2026-08-09
- **Reviewer:** web-verification pass against live sources (npm registry, playwright.dev)
- **Scope:** ARCHITECTURE-SPINE.md — AD-15 "Story-scoped Playwright E2E gate", the "Testing & CI" convention row, and the Stack table row `Playwright (@playwright/test) | 1.61.x, pinned at project init; Chromium is the gate browser`.

## Verdict

The Playwright capabilities the spine relies on (tags + `--grep` filtering, custom/worker-scoped fixtures, `data-testid`-or-role selector policy) are all real and current. Two corrections are needed: the pinned version is one minor behind current stable (1.62.1, not 1.61.x), and "reset per worker run" against one shared PostgreSQL collides with Playwright's default multi-worker parallelism unless the spine states a workers/isolation decision.

---

## Finding 1 — Version pin: 1.61.x is NOT the current stable (correction required)

**Spine says:** `Playwright (@playwright/test) | 1.61.x, pinned at project init`.

**Live web says:** The npm registry `dist-tags.latest` for `@playwright/test` is **1.62.1** (published 2026-07-30). Release history from the registry:

| Version | Published |
| --- | --- |
| 1.60.0 | 2026-05-11 |
| 1.61.0 | 2026-06-15 |
| 1.61.1 | 2026-06-23 |
| 1.62.0 | 2026-07-24 |
| **1.62.1 (latest)** | **2026-07-30** |

1.63.0 alpha builds are publishing daily (through 2026-08-09), so 1.63 stable is likely 4–6 weeks out on Playwright's usual cadence.

Note: `playwright` and `@playwright/test` release in lockstep — both are at 1.62.1. (A search-result summary claiming `@playwright/test` latest is "1.61.1" reflected a stale npm page cache; the registry API is authoritative.)

**Correction:** Since "project init" is now (post-2026-07-30), the pin should read **1.62.x** (currently 1.62.1). Nothing in AD-15 depends on a 1.62-only feature — 1.62's headline changes (stories/galleries component-testing model, AbortSignal cancellation, WebP screenshots, `Reporter.preprocess()` filtering, isolated-retries strategy) don't touch tagging or fixtures — so this is a freshness fix, not a design change. If the team deliberately wants to trail one minor for stability, the row should say so explicitly rather than implying 1.61.x is current.

## Finding 2 — Tagging syntax and `--grep` filtering: VERIFIED, current, and recommended

**Spine says:** specs `tagged @story:<epic>-<story> and @epic:<n>`; CI runs "the touched story's spec plus the `@smoke` tag"; `@live-ai` tag excluded from the gate.

**Live web confirms** (playwright.dev/docs/test-annotations):

- Tags are a first-class feature. Two supported forms: the dedicated details-object option — `test('title', { tag: '@smoke' }, async ({ page }) => {…})` — and inline `@`-tokens in the test title. The `tag` option (array-capable, `tag: ['@story:1-3', '@epic:1']`) also works on `test.describe()` to tag a whole group, and has been stable since 1.42.
- Docs constraint: "tags must start with `@` symbol" — the spine's `@story:1-3`, `@epic:1`, `@smoke`, `@live-ai` all comply; colons and hyphens are legal.
- Filtering is exactly as the spine implies: include with `npx playwright test --grep @smoke`, exclude with `--grep-invert @live-ai`, OR via `--grep "@fast|@slow"`, AND via lookaheads; also settable in config via `testConfig.grep` / `testProject.grep` (a per-project grep is a clean way to permanently exclude `@live-ai` from the gate project).

**One practical nuance (not a contradiction):** `--grep` is a regex substring match, so `--grep "@story:1-3"` would also match a hypothetical `@story:1-30`. With `<epic>-<story>` keys that can exceed one digit, CI invocations should anchor the pattern (e.g. `--grep "@story:1-3\b"` or match the full spec filename instead). Worth a parenthetical in AD-15 or the CI script, not a spine change.

## Finding 3 — Fixtures model for the shared harness: VERIFIED; recommended pattern is project-dependency setup, not `globalSetup`

**Spine says:** shared harness in `e2e/fixtures/` — "one login-as-persona fixture (drives the real auth path), one deterministic DB seed/reset … reset per worker run".

**Live web confirms** (playwright.dev/docs/test-fixtures, /docs/test-global-setup-teardown):

- Custom fixtures via `test.extend()` are the documented mechanism, and login-style fixtures are literally the docs' worked example. A parameterized login-as-persona fixture (persona as an option fixture, so a spec can declare `test.use({ persona: 'handler-1' })`) is idiomatic and type-safe.
- **Worker-scoped fixtures** (`{ scope: 'worker' }`) exist and are the documented home for expensive setup: "Worker fixtures are set up for each worker process … where you can set up services, run servers, etc." A worker-scoped seed/reset fixture matches the spine's "reset per worker run" wording exactly. Automatic fixtures (`auto: true`) let the reset run without every spec listing it.
- **Current recommended pattern for stack-level one-time setup:** the docs now explicitly recommend **project dependencies** (a `setup` project that other projects depend on, with a `teardown` counterpart) over the legacy `globalSetup`/`globalTeardown` config options: "This is the recommended approach, as it integrates better with the Playwright test runner" — setup shows in the HTML report, records traces, and can use fixtures; `globalSetup` gets none of that. For AD-15 this maps naturally to: one `setup` project that waits for the composed stack and runs the initial seed migration + pre-authenticates the nine personas into stored `storageState` files; the worker-scoped fixture then handles per-worker reset. The spine doesn't contradict this, but naming "project-dependency setup (not globalSetup)" in AD-15 would pin the harness to the current recommendation.

## Finding 4 — Parallelism default vs one shared PostgreSQL: latent conflict AD-15 should resolve

**Spine says:** one Playwright project drives "the real composed stack (nginx → FastAPI → PostgreSQL)" with a deterministic DB seed/reset "reset per worker run".

**Live web says** (playwright.dev/docs/best-practices): Playwright runs test **files in parallel across multiple workers by default**, and the best-practices doc is emphatic that "each test should be completely isolated from another test and should run independently with its own … data". A per-worker *reset* of one shared database is destructive cross-worker state: worker B's reset (or its test writes) lands mid-flight in worker A's assertions. As written, AD-15's harness is only sound under one of:

1. `workers: 1` (and `fullyParallel: false`) for the E2E project — simplest, honest for a suite this size, but serializes the merge-gate suite; or
2. per-worker isolation — the worker-scoped fixture creates/uses a worker-keyed database or schema (`workerInfo.workerIndex` / `parallelIndex`) so each worker resets only its own data, which requires the FastAPI stack to route by worker (extra plumbing the single-compose-stack design doesn't currently have).

**Correction:** AD-15 should state which it chooses. Given one composed stack and story-scoped suites, option 1 (`workers: 1` in the gate config) is the defensible default; the spine's "reset per worker run" then remains literally correct (one worker, reset at run start).

## Finding 5 — Selector and assertion policy: consistent with current guidance (minor emphasis note)

**Spine says:** "selectors by accessible role or `data-testid` only (never CSS classes — shadcn class names churn)".

**Live web confirms** (playwright.dev/docs/locators, /docs/best-practices): current guidance recommends prioritizing `getByRole()` ("the closest way to how users and assistive technology perceive the page") and warns against DOM/CSS-structure-dependent selectors ("Your DOM can easily change…"). `getByTestId()` is documented as legitimate but positioned as the fallback "when you can't locate by role or text" — the spine's ordering (role first, testid second) matches this exactly; banning CSS classes is directly aligned. No change needed; if anything, AD-15 could add "prefer role/text; testid is the escape hatch," which is the docs' precise stance. The docs also stress **web-first assertions** (`await expect(locator).toBeVisible()` auto-retries) — nothing in AD-15 contradicts this, and the "assert structure, not prose" rule for copilot streams is compatible (assert on stream-event markers rendered into the DOM via web-first assertions, not on sleeps).

## Summary of required edits

| Location | Current | Change to |
| --- | --- | --- |
| Stack table, Playwright row | `1.61.x, pinned at project init` | `1.62.x (1.62.1 current as of 2026-08-09), pinned at project init` |
| AD-15 rule | "reset per worker run" with no workers decision | State the parallelism decision: `workers: 1` for the gate suite (or worker-indexed DB isolation if parallel workers are ever wanted) |
| AD-15 rule (optional) | harness described generically | Name the pattern: project-dependency `setup` project (not `globalSetup`) + worker-scoped auto fixture; anchor `--grep` story-tag patterns |

## Sources

- https://registry.npmjs.org/@playwright/test (dist-tags + publish times, queried 2026-08-09)
- https://playwright.dev/docs/test-annotations
- https://playwright.dev/docs/test-fixtures
- https://playwright.dev/docs/test-global-setup-teardown
- https://playwright.dev/docs/best-practices
- https://playwright.dev/docs/locators
- https://playwright.dev/docs/release-notes
- https://www.npmjs.com/package/@playwright/test?activeTab=versions
