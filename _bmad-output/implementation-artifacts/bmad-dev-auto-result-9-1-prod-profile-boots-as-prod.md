---
status: blocked
warnings: [multiple-goals]
---

# BMad Dev Auto Result — Story 9.1

Status: **blocked**
Blocking condition: **intent gaps**

Intent: Story 9.1, "The Prod Profile Actually Boots As Prod" (`9-1-prod-profile-boots-as-prod`)
Halted at: `step-02-plan.md`, instruction 5 (intent gaps), after completing instruction 2 (investigation)
Date: 2026-08-24
Prior run: `bmad-dev-auto-result-20260824-1031.md` (blocked on a dirty tree; that condition is cleared — commit `633e514`)

No spec file was written. `_bmad-output/implementation-artifacts/spec-9-1-prod-profile-boots-as-prod.md` does not exist, because writing it would have required inventing an authentication design (see below). Per instruction 5, this run does not fantasize and does not leave open questions in a spec.

---

## The gap

**Story 9.1's first acceptance criterion cannot be specified without a decision nobody has made.**

The `ENV=prod` boot refusal is not an infrastructure gap. It is an authentication gate. `lineworker/server/api/app.py:437-450`, the first statement in `create_app` after `configure_logging`:

> `ENV=prod is refused while persona login is the authentication mechanism: POST /auth/login mints a session from an unauthenticated persona id. Resolve the Deferred IdP decision and replace _resolve_user in api/deps.py before enabling prod.`

That message names **exactly one** prerequisite: resolve the deferred identity-provider decision and replace `_resolve_user` (`lineworker/server/api/deps.py:106-107`, commented as "The OIDC swap point"). It names no TLS, backup, secret, or migration precondition — those are enforced separately by `${…:?}` guards in `compose.prod.yaml` per `DEPLOYMENT.md:214-224`.

So AC 1 ("the api boots under `ENV: prod`") expands to: **choose an identity provider and implement real OIDC session authentication, replacing the nine-persona seed-data auth the whole demo runs on.** That is:

1. **A feature, not a chore.** It touches `_resolve_user`, the login route, the session model, the persona picker that is the app's front door, and the seeded `app_user` / `user_employer_assignment` mapping.
2. **Explicitly deferred by architecture.** The spine's Deferred registry lists "identity provider choice (seed-data auth until then)"; `DEPLOYMENT.md:508` carries it as a reopen-when item. `epics.md`'s Additional Requirements repeat it under "Deferred by decision (do NOT build in v1)".
3. **The first clause of NFR-5** ("Real identity/authorization (OIDC-ready session auth)"), which Epic 8 never claimed and which Sprint Change Proposal 2026-08-24 assigned to Epic 9 without noticing it lands inside 9.1.
4. **Not a decision an unattended dev run may take.** Which IdP, how tokens map to `app_user`, what happens to the demo personas, and whether the persona picker survives are product and architecture calls.

### Unanswered questions

1. **Which identity provider?** No candidate is named anywhere in the repo. `compose.prod.yaml:170-172` says only what sets `ENV` "once the Deferred IdP decision lands"; there is no candidate mechanism in-tree.
2. **Does Story 9.1 own implementing it, or does a new story?** If 9.1 owns it, 9.1 is the largest story in Epic 9 by a wide margin and the go-live gate's critical path runs through an unstarted feature.
3. **What happens to the nine demo personas and the persona picker (UX-DR1)?** They are the app's only entry point and the basis of every e2e fixture (`e2e/fixtures/login.ts`'s `PERSONAS`, `loginAs`). Real auth either replaces them or coexists with them behind an env switch — a decision with UX and test-suite consequences.
4. **Is a dev-flagged prod overlay acceptable in the interim?** The prod overlay boots today and was executed green on 2026-08-23 (`DEPLOYMENT.md:569-654`) — it simply renders `ENV: dev`. If the answer is "TLS ingress + explicit correct settings is good enough until the IdP lands", then AC 1 should say so instead of demanding `ENV: prod`.

### Correction to the proposal and the epic

Sprint Change Proposal 2026-08-24 §5 states 9.1 is "the only go-live-gate story with no upstream decision dependency", and `epic-9-context.md:63` repeats it. **Both are wrong.** 9.1 carries the heaviest decision dependency in the epic. The sequencing diagram in §5 and the epic preamble in `epics.md` need amending, and the go-live gate's critical path needs rethinking: 9.2 and 9.3 were sequenced *after* 9.1 on the grounds that hardening must be verified in a booting prod profile.

---

## Recommended resolution: split the story

The `multiple-goals` warning carried from step-01 is the shape of the fix. Story 9.1 as written bundles three independently shippable goals; only the first is blocked.

| Goal | Blocked? | Recommendation |
|---|---|---|
| (a) `ENV=prod` bootable | **Yes — IdP decision** | Split out as its own gate story (e.g. 9.14 "Real Identity & Authorization", or fold into a re-scoped 9.1 once the decision lands). Needs PM + Architect before any dev run |
| (b) Registry-backed images + pgaudit privilege path | No | Keep in a re-scoped 9.1 — fully specifiable now |
| (c) CI additions + documentation corrections | No | Keep in a re-scoped 9.1 — fully specifiable now |

A re-scoped 9.1 covering (b) and (c) is ready to spec immediately; the investigation below is sufficient for it. Suggested re-scoped title: **"Prod Profile Deployability & The Gate's Own Gaps"** — it stops claiming a prod boot it cannot deliver.

---

## Investigation findings (complete — reusable by the re-scoped story)

Four subagent investigations ran to completion. Recording their load-bearing facts so the next run does not repeat them.

### The prod boot refusal (goal a)

- `api/app.py:437-450` raises `RuntimeError`; `entrypoint.sh` has no env guard, so the refusal fires at factory call — a crash-loop, and `web` gates on `api: service_healthy`, so the whole overlay stays down (`compose.prod.yaml:163-165`).
- **Two tests currently assert the refusal stays:** `tests/test_deploy_tls_posture.py:389-418` (`test_the_prod_profile_does_not_set_env_prod_because_the_api_refuses_to_boot_under_it`, asserts `"ENV: prod" not in` the prod render) and `tests/test_problem_json.py:155-156` (`pytest.raises(RuntimeError, match="ENV=prod is refused")`). Lifting the guard is a deliberate three-file change, not a one-line deletion.
- **Second-order effect:** `app.py:531-545` makes `openapi_url`/`docs_url`/`redoc_url` `None` under prod — branches that are unreachable-true today, so the prod overlay currently serves `/docs`, `/redoc` and `/openapi.json`. Recorded at `DEPLOYMENT.md:517-521`. Lifting the guard silently changes that posture, which is a thing to verify rather than discover.
- **`ENV` is a three-member enum** (`config.py:102-105`: `dev`, `e2e`, `prod`). Non-test consumers of `settings.env` are only five sites: `config.py:731`, `config.py:737`, `app.py:437`, `app.py:500` (a log field), `app.py:557` (mounts the e2e-only admin router).
- **Prose that restates the refusal and would need updating:** `compose.prod.yaml:151-175`, `deploy/.env.example:34, 76-78, 672-690`, `DEPLOYMENT.md:231-238, 507-521`, `RESTORE-DRILL.md:432-435`, `README.md:68-77, 302-306`, `server/scripts/purge_claim.py:17-22` (argues there is no HTTP purge route *because* prod is refused).
- **`README.md:68-77` and `RESTORE-DRILL.md:432-435` both state the profile is unbootable, and the README assigns finishing it — "health endpoints, the `ENV` question, the encrypted-volume documentation" — to Story 8.4.** 8.4 shipped without doing it. Three documents carry the claim and one names an owner that is now wrong.

### The env-keyed settings pattern (goals a, b)

- Mechanism: nullable field + `@property` deriving from `self.env`. **Exactly two exist:** `session_cookie_secure` (`config.py:356`) → `cookie_secure` (`config.py:733-737`, derives `env is Env.prod`); `scheduler_enabled` (`config.py:403`) → `scheduler_runs` (`config.py:727-731`, derives `env is not Env.e2e`).
- Prod overlay sets both explicitly: `SESSION_COOKIE_SECURE: "true"` (`compose.prod.yaml:243`), `SCHEDULER_ENABLED: "true"` (`:253`), argued at `:221-252` — necessary precisely because the overlay renders `ENV: dev`.
- **The pattern-derived guard:** `tests/test_deploy_ops_posture.py:466-506` reads `config.py` as text and derives its subject with a regex requiring, in order, `def <name>(self) -> bool:`, `if self.<field> is not None:`, `return self.<field>`, and a final line beginning `return self.env is`. It has a vacuity guard at `:492`. It would **not** match: a docstring or comment between `def` and the `if`; any non-`bool` annotation; an inverted guard; a ternary or `or`-fallback; `self.env ==` / `self.env in (…)`; a `model_validator`; a `default_factory`. It also assumes the env var name is `field.upper()`.

### Compose overlays and images (goal b)

- Intended invocation is three files with prod **last** (`DEPLOYMENT.md:246-257`); order matters for `!override`, `restart:` and the `pg_hba` mount.
- **`compose.prod.yaml` contains no `image:` and no `build:` key in any of its 487 lines** — it inherits both from base. So `docker compose pull` against the prod profile fails for both local tags, and `up` works only because the Dockerfiles are present. For `backup` the failure is **quiet**: the stack starts and takes no backups (`deferred-work.md:1130`).

| Tag | Declared | Built from | Registry-backed |
|---|---|---|---|
| `lineworker/postgres:pg18-pgaudit` | `compose.yaml:248`, `compose.e2e.yaml:101` | `deploy/postgres/Dockerfile` (`FROM pgvector/pgvector:pg18`, `postgresql-18-pgaudit`) | No |
| `lineworker/backup:pg18` | `compose.yaml:401`, `compose.e2e.yaml:194` | `deploy/backup/Dockerfile` (`FROM postgres:18-trixie`) | No |
| `ollama/ollama:latest` | `compose.yaml:154` | pulled | Yes, but a **moving tag**; `:161-164` says ":latest here and a digest at deploy" — no digest-pin procedure exists (`deferred-work.md:666`) |

- No push, `docker save`, or registry step anywhere in `deploy/` or `DEPLOYMENT.md`. **`DEPLOYMENT.md §8` is referenced by `compose.prod.yaml:42-44` as carrying the registry decision but has no image-registry subsection** — its five H3s are IdP, blob backend, scheduler, observability, Ollama capacity. Possible doc drift to confirm.
- **Migration 0050** (`server/data/versions/20260822_0050_pgaudit.py:81`): `CREATE EXTENSION IF NOT EXISTS pgaudit`. Its docstring (`:8-29`) blames `shared_preload_libraries`; the actual requirement is **superuser** (pgaudit is not a trusted extension), so a managed or least-privilege owner fails with `permission denied to create extension "pgaudit"` and the docstring misdirects. Part of this AC is a docstring correction.
- **Healthchecks.** `ollama`: `start_period: 30m` (`compose.yaml:235-243`), and `DEPLOYMENT.md:279-292` **forbids `up -d --wait`** because ollama may never go healthy — so turning the wall into a budget must keep that documented startup story coherent. Prod `web` healthcheck is an HTTPS loopback with `--no-check-certificate` (`compose.prod.yaml:140-145`), and whether BusyBox `wget` in `nginx:stable-alpine` has `/usr/bin/ssl_client` is **unverified anywhere** — prod `web` health is unproven (`deferred-work.md:1090-1093`).
- **GPU overlay** is 61 lines whose only config is `ollama.deploy.resources.reservations.devices` (`:53-61`). No CPU fallback — container *create* fails without the NVIDIA toolkit. CI only renders it (`ci.yaml:321-326`). Never booted: `DEPLOYMENT.md:577-581`, checklist `:759`.
- **At-rest encryption has a designed procedure that was never executed.** `DEPLOYMENT.md:141-204` states compose cannot enforce it, prescribes `docker info --format '{{.DockerRootDir}}'`, `findmnt`, `lsblk`, `cryptsetup status`, and requires the output be pasted into § Executed boot. Unticked at `:741`; deviation recorded at `:582-590` (Docker Desktop host). So the AC is "execute and record an existing procedure", not "design one" — and it needs a Linux/native-engine host, which the project has never had.

### CI, required checks, and the bijection lint (goal c)

- `ci.yaml` is 654 lines, **5 jobs**, all `ubuntu-latest`, **no job-level `if:`** — stage separation lives in step-level `if:` inside `e2e`. Triggers: `push.branches:[main]` + bare `pull_request` (`:36-39`).

| Job | Check-run name | Purpose |
|---|---|---|
| `server` `:56` | server lint+typecheck+pytest | ruff, mypy, `lint_log_phi`, **`lint_story_specs`** (`:107`), pytest |
| `web` `:112` | web lint+typecheck+vitest | `generate:api` + `git diff --exit-code src/api/schema.d.ts` (`:133-138`) |
| `migrations` `:146` | alembic upgrade against fresh DB | builds+runs the postgres image, `alembic upgrade head`, pytest |
| `compose` `:270` | compose config (AD-5 port posture) | renders 4 profiles, port posture, then a guard-driving loop deriving `${VAR:?}` names from `compose.prod.yaml` and re-rendering per var × {unset, empty} (`:480-530`) |
| `e2e` `:532` | playwright story specs + smoke | boots `compose.e2e.yaml` `--wait`, PR-narrow grep or the AD-15 full suite on push (`:644-647`) |

- **No `concurrency:` key anywhere in `.github/`** — confirmed by grep.
- **Adding a 6th job breaks a test until the manifest is updated.** `required-checks.yml` is consumed by nothing at runtime; its sole consumer is `tests/test_required_checks.py`, which asserts workflow⊆manifest **and** manifest⊆workflow with a vacuity floor of ≥5 (`:159, :178, :194, :209`) and requires each `reason:` to exceed 40 characters (`:226`). A prod-boot job therefore needs a `required-checks.yml` entry in the same commit. The "list is mechanical, enforcement is a settings page" claim on record is **correct** and pinned by a header-honesty test (`:242`).
- **The bijection lint** (`scripts/lint_story_specs.py`, 496 lines) parses `development_status:` line-by-line with no YAML parser, toggling on any non-indented line (`:196-215`). Keys are **captured and compared for equality**, never prefix-matched — `_SPEC_NAME = ^(?P<key>\d+-\d+)-` is greedy so `9-10-x.spec.ts` yields `9-10`, and `problems` requires `tags == {key}` (`:437`). Paths resolve from module location, not cwd (`:92-109`); `main` takes two optional positionals (`:460-462`). Three refusal conditions guard vacuity (`:365, :371, :377-380`).
- **Smallest correct epics-file reconciliation:** add `EPICS = REPO_ROOT/"_bmad-output"/"planning-artifacts"/"epics.md"`, one pattern `^### Story (?P<epic>\d+)\.(?P<num>\d+):\s*(?P<title>\S.*)$` (53 headings, zero duplicates, no other `### Story` shape), a detector `epic_stories(text) -> dict[str, str]`, an `epics: Mapping[str,str] = {}` parameter on `problems` with a defaulted empty-set short-circuit so existing call sites keep working, symmetric-difference findings both ways, and a third optional argv slot.
  - **Landmine:** reconcile **numeric keys only, never slugs.** Eight Epic 9 tracker slugs deliberately differ from slugified epics titles (`9-1-prod-profile-boots-as-prod` vs `the-prod-profile-actually-boots-as-prod`; also 9-3, 9-4, 9-6, 9-7, 9-10, 9-11, 9-12). A slug-level rule fails on merge.
  - Verified today: tracker keys and epics headings are **already exactly equal, 53/53**, so the new rule ships green.
- **Test fixture pattern** (`tests/test_story_spec_lint.py`, 632 lines): real files in `tmp_path` via `write_status` / `write_spec` / `healthy` helpers, most tests calling `problems` through a local `run()`, and CLI tests driving argv (`main([str(status), str(specs)])` at `:426, :435, :449`). A third input follows the same shape plus a `write_epics` helper and an `EPICS.is_file()` assertion alongside `:476`.
- **Conventions:** ruff `line-length = 100`, `select = ["E","F","W","I","UP","B","SIM"]`; **mypy `strict = true` covering `scripts` and `tests`**; `testpaths = ["tests"]`. **PyYAML is not a declared dependency** — transitive only, and nothing in `server/` imports it. The no-YAML-parser stance is genuine and load-bearing. Ruff/mypy do **not** cover `.github/scripts/`, so a helper placed there would be unlinted.

### The e2e spec this story owes (goal c)

- Once `9-1-…` leaves `backlog`, the lint reports it as an orphan until `e2e/stories/9-1-prod-profile-boots-as-prod.spec.ts` exists with a filename exactly equal to the tracker slug and a `@story:9-1` tag in the `test.describe` title.
- **8.4 solved the ops-only-story problem and the pattern is directly imitable** (`e2e/stories/8-4-complete-quality-operations-gate.spec.ts`): a header docblock stating what a browser cannot assert and naming where the real proofs live (pytest modules), then two tests — a `@smoke` browser path (`page.goto("/")`, heading + persona combobox, `page.request.get("/api/healthz")` expecting exactly `{status:"ok",db:"ok"}`, `loginAs`, a non-empty `queue-card-id`) and a non-browser `docker compose ps --format json` check parsed by a local `composePs()` handling both JSON-array and NDJSON, with a **vacuity guard** so an empty `ps` fails loudly.
- **Trap:** `e2e/fixtures/reset.ts:4` pins `COMPOSE_FILE` to `deploy/compose.e2e.yaml` — the exact trap `DEPLOYMENT.md:692-706` records. A prod-boot job cannot reuse the e2e reset path.
- **Smoke-count drift:** `DEPLOYMENT.md:756` records "41 passed"; `RESTORE-DRILL.md:459` records "40 passed". Adding a 9-1 spec changes the count again — whichever the story touches must be updated, and the drift itself is worth closing.

### The executed-record format Story 9.1 must imitate

`RESTORE-DRILL.md:425-538` is the house format for an executed verification, and Story 9.1's manual-verification ACs should follow it: a bold key-value header (**Date (UTC)** start→end, **Revision** full SHA + working-tree state, **Executed by**, **Environment** with any deviation argued inline, **Procedure**, **Run id**); a `### Phase timings` table `| Phase | Step | Wall clock | Notes |` with a bold Total row; `### Deviations from this runbook` as bullets each naming what was therefore *not* exercised and where it is registered; `### Verification checklist, completed` as `- [x]` items each carrying the concrete number or fingerprint that proves it; and a `### Conclusion` with a bold verdict.

---

## To unblock

1. **PM + Architect decide the IdP question** (questions 1–4 above). This is the same class of decision as Story 9.13's register and the Architect's scheduler call — it belongs with them, not in a dev run.
2. **Correct-course the split.** Amend `epics.md` Epic 9, `epic-9-context.md:63`, and Sprint Change Proposal 2026-08-24 §5 to remove the "no upstream decision dependency" claim, re-scope 9.1 to goals (b) and (c), and add the auth work as its own gate story. Re-sequence the go-live gate: 9.2 and 9.3 were placed after 9.1 on the assumption that a booting prod profile comes first.
3. **Then re-invoke** on the re-scoped 9.1. The investigation above is sufficient to write that spec with no further exploration.

Alternatively, if the answer to question 4 is "a TLS-fronted, dev-flagged prod overlay is acceptable until the IdP lands", then AC 1 should be rewritten to say exactly that — and the whole of 9.1 becomes specifiable immediately, with `README.md`, `RESTORE-DRILL.md` and `DEPLOYMENT.md` corrected to describe that posture honestly rather than promising a prod boot.

## Note on subagent execution

`SKILL.md` requires synchronous subagent invocation and forbids detached execution. All four investigations were launched with synchronous intent; this harness returns `Agent` calls as background tasks and delivers results by notification. Control did return in every case and no finding was lost, but the run crossed turn boundaries, which the workflow's unattended model does not anticipate. The first investigation was also launched alone before the remaining three were batched, so parallelism was three-wide rather than four.
