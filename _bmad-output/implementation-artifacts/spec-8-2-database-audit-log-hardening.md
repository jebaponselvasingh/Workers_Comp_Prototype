---
title: 'Story 8.2 — Database Audit & Log Hardening'
type: 'feature'
created: '2026-08-22'
baseline_revision: '7d046a5db4049deb32d8740eec581b0225b05bd7'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true # twenty-two findings were patched and thirteen of them changed behaviour rather than prose: the prod overlay could not boot at all and a test pinned it that way, the recipe the failure message prints started a database that failed that suite, both DML-absence probes raced the collector they document, the URL guard denylisted five parameters where the failing class is every libpq-only name, and the lint could not see `bind`, `str(exc)` or `_META_KEYS`. What a follow-up should read is whether the inverted `ENV: prod` assertion and the `directives()` helper it needed leave any other prose/assertion collision, whether the widened lint's new interpolation rules have false positives the parametrized controls do not enumerate, and whether the allowlisted URL query-parameter set is right for both drivers rather than only for the two the tests exercise
context:
  - '{project-root}/_bmad-output/implementation-artifacts/8-2-database-audit-log-hardening.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-8-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-8-1-phi-purge-cascade-retention.md'
warnings: ['oversized', 'multiple-goals']
---

<intent-contract>

## Intent

**Problem:** Every observability surface this build has is unhardened. Postgres runs with stock logging and no pgaudit at all, so there is no DDL/role capture and nothing stops a future statement-logging setting from turning the DB log into a second, unredactable copy of claim values — the one store the Story 8.1 cascade cannot reach. The structlog PHI ban is a docstring and six hand-written stderr-scanning tests, so it holds only where somebody remembered to write one. And nothing in the stack speaks TLS: nginx listens on plain `80`, the API-to-Postgres URL has no `sslmode`, and no profile carries a certificate.

**Approach:** Make each surface mechanical. Build a pgaudit-capable Postgres image and start every profile with an explicit hardening flag list — `pgaudit.log=ddl,role`, DML statement/parameter logging pinned off, collector on, seven-file day-of-week rotation inside PGDATA. Turn the app-log ban into a closed keyword allowlist that a static AST lint enforces, a structlog processor backstops, and runtime capture tests prove on the two flows nothing covers yet. Verify — never re-grant — the `audit_event` grant posture from `information_schema`/`pg_catalog` against the fresh-migration DB. Add a `deploy/compose.prod.yaml` overlay carrying TLS at nginx and to Postgres, with a config-verification test standing in for the profile CI cannot boot.

## Boundaries & Constraints

**Always:**
- **pgaudit captures `ddl, role` and nothing else.** `pgaudit.log_parameter=off`; `log_statement=none`, `log_duration=off`, `log_min_duration_statement=-1`, `log_min_error_statement=panic`, `log_parameter_max_length=0`, `log_parameter_max_length_on_error=0` are set **explicitly** so that turning any of them into a PHI surface is a visible diff rather than a default nobody stated. Value-level history is AD-4's `audit_event`, which the 8.1 cascade can redact; a DB log line cannot be redacted by anything this system owns.
- **The pgaudit posture is applied in *every* profile** (dev, e2e, prod) by the *same* mechanism — a `command:` flag list on the postgres service — so drift cannot hide. TLS material is prod-only.
- **The DB log destination resolves inside PGDATA**, therefore inside the volume the deployment view encrypts (`log_directory=log`, relative). Never a bind mount, never stdout-only.
- **This story verifies grants; it does not create or widen them.** No `GRANT`/`REVOKE`/`CREATE ROLE` in the new migration. `audit_event` stays INSERT-only for `lineworker_app`, and `audit_redactor` keeps exactly SELECT, column-scoped UPDATE on `before`/`after`, and RLS-bounded DELETE (migration 0003, verified by 8.1).
- **The log allowlist has exactly one definition.** `logging_config.LOG_KEY_ALLOWLIST` is the single list; the lint imports it and the processor uses it. Two lists is how the weaker one becomes the one relied on.
- **Every new stderr-scanning test carries a named positive-control event** (`test_copilot_logging.py`'s rule: a search for absent strings passes identically against a process that logged nothing).
- **Every new static guard ships the three mandatory controls** — would-notice, leaves-innocent-alone, and exemption staleness — per `test_purge_ownership.py`.
- Migrations only, Alembic-only, chaining from `0049_purge_grants`. AD-3.
- Certs and keys come from the host secret store by bind mount; nothing cryptographic enters VCS (`.gitignore` already keeps `*.env` out).

**Block If:**
- `postgresql-18-pgaudit` cannot be installed into `pgvector/pgvector:pg18` from the PGDG repo the official image already configures. Without the extension nothing in AC 1 is achievable and no substitute posture is acceptable.
- Making `CREATE EXTENSION pgaudit` a migration would require the *runtime app role* to hold a new privilege. It must not: migrations run as the owner.

**Never:**
- No `pgaudit.log` value containing `read`, `write`, `function`, or `all`; no `pgaudit.log_parameter=on`.
- No log **shipping**, aggregation, dashboarding, or alerting — deferred by decision (Story 8.4 documents it).
- No new table, role, grant, or RLS policy. No change to `audit_event`'s schema or to what `services/audit` writes.
- No TLS in the dev or e2e profiles — Story 1.1's plain-HTTP decision stands, and the e2e suite drives `http://localhost:8081`.
- No health endpoints, GPU finalization, backup job, or CI-gate completion — 8.4. No `last_used_at`, no audit `at` index, no purge behaviour change — 8.1's recorded residuals stay recorded.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| DDL under pgaudit | `CREATE TABLE probe(...)` on the migrated DB | An `AUDIT: ... ,DDL,CREATE TABLE,...` line appears in `pg_read_file(pg_current_logfile())` | No error expected |
| DML under pgaudit | `INSERT` carrying a distinctive literal | The literal appears **nowhere** in the DB log; no `AUDIT` line is emitted for it | No error expected |
| Failing DML | A statement that raises `permission denied` | The error is raised to the client; the **statement text is absent** from the DB log (`log_min_error_statement=panic`) | Client-side error unchanged |
| Bare upgrade, unconfigured PG | `alembic upgrade head` against a Postgres without `shared_preload_libraries=pgaudit` | Migration `0050_pgaudit` fails loudly at `CREATE EXTENSION pgaudit` | Error surfaces; no silent skip |
| Allowlisted log call | `log.info("audit.recorded", entity_id="WC-20017", fields=[...])` | Lint passes; processor leaves every key intact; JSON line carries them | No error expected |
| Banned keyword | `log.info("x", body=note.text)` or `log.info("x", v=f"{claim.cause}")` | Lint reports the file and exits non-zero; pytest fails | Failure names file, line, keyword and the remedy |
| Non-allowlisted key at runtime | A call the lint has not yet seen reaches the pipeline | The **value** is replaced with a blocked marker; the key and the event name survive | Never raises — a log call must not crash a request |
| Grant drift | A future migration grants UPDATE on `audit_event` to `lineworker_app` | `test_audit_grants` fails against the fresh-migration DB | Failure names the grantee and privilege |
| asyncpg + `sslmode` | `DATABASE_URL=…?sslmode=verify-full` | `async_database_url` renders `ssl=verify-full`; `sync_*` keep `sslmode` | — |
| libpq file params in URL | `DATABASE_URL=…?sslrootcert=/x.crt` | `Settings` raises, naming `PGSSLROOTCERT` as the supported mechanism | Loud at construction, not at first connect |

</intent-contract>

## Code Map

- `lineworker/deploy/compose.yaml:187-201` — the dev postgres service: `pgvector/pgvector:pg18`, **no `command:`, no conf mount**, `pgdata:/var/lib/postgresql` (the *parent* of PG18's PGDATA, which is why a relative `log_directory` lands in the volume). `web` at `:25` publishes `8080:80`; `api` env at `:58-60` composes both DB URLs.
- `lineworker/deploy/compose.e2e.yaml:91-105` — the e2e postgres: identical image/healthcheck, no data volume, `./e2e-init:/docker-entrypoint-initdb.d:ro`. `web` publishes `8081:80`.
- `lineworker/deploy/compose.gpu.yaml` — prod **GPU** overlay, one service (`ollama`), header argues it holds only that delta. Leave it alone; TLS is not GPU.
- `lineworker/deploy/nginx/default.conf` — single `listen 80` server; `/api/` proxy block at `:21-38` carries the SSE settings (`proxy_buffering off`, `proxy_read_timeout 3600s`). **Baked into the web image** by `lineworker/web/Dockerfile:11`, not mounted.
- `lineworker/deploy/.env.example` — `# --- Section (Story N.N) ---` rules, commented-out defaults, prose above each knob; `# --- PHI purge cascade & retention (Story 8.1) ---` at `:220` is the newest and the style to match.
- `lineworker/server/data/versions/20260822_0049_purge_grants.py` — head (`0049_purge_grants`); the grants-only migration shape and the `REVOKE`-symmetric `downgrade()` to copy.
- `lineworker/server/data/versions/20260810_0003_core_schema.py:216-251` — the grants this story verifies: `GRANT SELECT, INSERT ON audit_event TO lineworker_app`; `GRANT SELECT`, `GRANT UPDATE ("before","after")`, `GRANT DELETE` to `audit_redactor`; five policies, `audit_redactor_delete USING (at < now() - interval 'N years')`.
- `lineworker/server/config.py:123-137` `_with_driver` — parses with `make_url`, sets `drivername`, re-renders; **query params survive but nothing translates them**. `:585-631` the four URL properties. `:141` `SettingsConfigDict`, `:143-164` the `mode="before"` validator to sit beside.
- `lineworker/server/logging_config.py:15-20` `_shared_processors` — the one chain both structlog and stdlib records pass through; the processor's insertion point is its end.
- `lineworker/server/services/audit/__init__.py:108-116` — `audit.recorded` with `action/entity/entity_id/actor_id/fields`; the positive control for the audited-edit runtime test.
- `lineworker/server/services/audit/purge.py:1170-1190` `_log_run(event, run, **subject)` — the tree's only `**` unpack into a log call and its only non-literal event name (with `agents/approval.py:780`'s `f"copilot.write_{outcome}"`).
- `lineworker/server/tests/test_purge_ownership.py:181-195, 275-420` — `SCAN_ROOTS`/`EXCLUDED`/`_sources()` with its `assert found` vacuity guard, the exemption-dict convention, and the three mandatory controls. The template for the new lint's tests.
- `lineworker/server/tests/test_layering.py:121-148` `_docstrings()` — restate rather than import (that file says why).
- `lineworker/server/tests/test_photo_migration.py:253-260` — the canonical `information_schema.role_table_grants` query and the deliberate `set(granted) == {...}`.
- `lineworker/server/tests/test_schema_seed.py:43-59, 144-186` — `engine`/`app_engine` fixtures and the `SET ROLE audit_redactor` / `rowcount` idiom.
- `lineworker/server/tests/test_ai_insights.py:431-460` — the precedent for asserting on compose files **as text**, with its stated reason (a YAML parser is a dependency this suite does not declare).
- `lineworker/server/tests/conftest.py:1-27` — the `docker run … pgvector/pgvector:pg18` recipe in the docstring, `MIGRATION_TEST_DATABASE_URL`, `requires_db`, `seeded_db_url`.
- `.github/workflows/ci.yaml:83-129` (`migrations` job — `services.postgres` uses the stock image), `:21-47` (`server` job), `:131-157` (`compose` job renders three profiles into `/tmp/{dev,gpu,e2e}.json` for `.github/scripts/check_model_ports.py`).
- `lineworker/e2e/fixtures/reset.ts:25-45` `psqlQuery` — runs SQL as the e2e SUPERUSER owner and returns `-tA` lines; the only way a spec sees the DB.

## Tasks & Acceptance

**Execution:**
- [x] `lineworker/deploy/postgres/Dockerfile` — new. `FROM pgvector/pgvector:pg18`; `apt-get update && apt-get install -y --no-install-recommends postgresql-18-pgaudit` and clean `/var/lib/apt/lists`. Nothing else — no `ENTRYPOINT`, no conf. Comment states the base image ships the PGDG repo and that the posture itself lives in the compose `command:` so it applies to volumes created before this image existed.
- [x] `lineworker/deploy/compose.yaml` — postgres gains `build: {context: ./postgres}` + `image: lineworker/postgres:pg18-pgaudit` and the hardening `command:` list (`postgres` followed by `-c` pairs): `shared_preload_libraries=pgaudit`, `pgaudit.log=ddl,role`, `pgaudit.log_catalog=off`, `pgaudit.log_parameter=off`, `pgaudit.log_relation=off`, `pgaudit.log_statement_once=on`, `log_statement=none`, `log_duration=off`, `log_min_duration_statement=-1`, `log_min_error_statement=panic`, `log_parameter_max_length=0`, `log_parameter_max_length_on_error=0`, `logging_collector=on`, `log_destination=stderr`, `log_directory=log`, `log_filename=postgresql-%a.log`, `log_rotation_age=1d`, `log_rotation_size=0`, `log_truncate_on_rotation=on`. A comment block above it states the AD-11 rule, the seven-day window (Design Note 2), and that `logging_collector=on` deliberately takes postgres logs off container stdout.
- [x] `lineworker/deploy/compose.e2e.yaml` — same `build`/`image`/`command` list, **byte-identical** to the base list, with a comment pointing at the test that pins the identity.
- [x] `lineworker/deploy/compose.prod.yaml` — new prod overlay (`name: lineworker`), used as `-f compose.yaml -f compose.gpu.yaml -f compose.prod.yaml`. Header explains it holds the non-GPU prod deltas and why it is not folded into `compose.gpu.yaml`. Contents: `web` — `ports: !override ["443:443", "80:80"]`, bind-mounts `./nginx/tls.conf:/etc/nginx/conf.d/default.conf:ro` and `${TLS_CERT_DIR:?}:/etc/nginx/tls:ro`; `postgres` — `command:` restating the base list **plus** `ssl=on`, `ssl_cert_file=/etc/postgresql/tls/server.crt`, `ssl_key_file=/etc/postgresql/tls/server.key`, and a `${PG_TLS_DIR:?}:/etc/postgresql/tls:ro` mount; `api` — `ENV: prod`, `DATABASE_URL`/`ALEMBIC_DATABASE_URL` carrying `?sslmode=verify-full`, and `PGSSLROOTCERT: /etc/postgresql/tls/ca.crt` with the same mount.
- [x] `lineworker/deploy/nginx/tls.conf` — new. A `listen 80` server that `return 308 https://$host$request_uri`, and a `listen 443 ssl` server whose `location` blocks are the current `default.conf` ones **unchanged** (the `/api` 308, the `/api/` proxy with its SSE settings, the SPA `try_files`), plus `ssl_certificate /etc/nginx/tls/server.crt`, `ssl_certificate_key /etc/nginx/tls/server.key`, `ssl_protocols TLSv1.2 TLSv1.3`, `ssl_session_cache`, and `absolute_redirect off`. Header states it is the prod twin of `default.conf` and that the two must be edited together.
- [x] `lineworker/deploy/.env.example` — append `# --- TLS at ingress and to Postgres (Story 8.2) ---` in house style: `TLS_CERT_DIR`, `PG_TLS_DIR` (host secret-store paths, required by the prod overlay's `:?` guards), and prose on `sslmode=verify-full`, why the CA arrives as `PGSSLROOTCERT` rather than a URL parameter (Design Note 3), and the two manual verification commands (`openssl s_client -connect …:443`, `SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()`).
- [x] `lineworker/server/data/versions/20260822_0050_pgaudit.py` — new revision `0050_pgaudit`, `down_revision = "0049_purge_grants"`. `upgrade()`: `op.execute("CREATE EXTENSION IF NOT EXISTS pgaudit")`. `downgrade()`: `DROP EXTENSION IF EXISTS pgaudit`. Module docstring states that pgaudit refuses to load unless `shared_preload_libraries` names it, so an upgrade against an unconfigured Postgres fails here **by design**, and points at the compose `command:` as the other half.
- [x] `lineworker/server/config.py` — in `_with_driver`, after setting the drivername, translate `sslmode` → `ssl` when `driver == "asyncpg"` (SQLAlchemy's asyncpg dialect passes query params straight to `asyncpg.connect`, which has no `sslmode` kwarg but accepts the same strings as `ssl`); leave `sslmode` untouched for `psycopg`. Add a `mode="before"` guard beside `_refuse_retired_settings` rejecting `sslrootcert`/`sslcert`/`sslkey`/`sslcrl`/`sslpassword` in `database_url`/`alembic_database_url`, naming `PGSSLROOTCERT` as the mechanism. No new settings field.
- [x] `lineworker/server/logging_config.py` — add `LOG_KEY_ALLOWLIST: frozenset[str]` (every keyword name in use today, grouped by comment: identifiers, counts, durations, enums/constants, exception-class names) and `_META_KEYS`; add a `_block_unallowlisted` processor appended to `_shared_processors` that replaces the **value** of any non-meta, non-allowlisted key with `BLOCKED_VALUE = "<blocked:not-on-log-allowlist>"`, iterating over `list(event_dict)` and never raising. Docstring: the lint is the gate, this is depth, and the value is replaced rather than the key dropped so the leak is visible in the line that would have carried it.
- [x] `lineworker/server/scripts/lint_log_phi.py` — new AST lint. `SCAN_ROOTS = ("api", "services", "rules", "agents", "data", "scripts")`, `EXCLUDED = ("data/versions",)`, `_sources()` ending in the `assert found` vacuity guard. Importable detectors `log_calls(tree)`, `offending_keywords(tree)` returning `(lineno, keyword)` pairs for (a) any keyword whose name is not in `LOG_KEY_ALLOWLIST` and (b) any keyword whose value is an `ast.JoinedStr`. Receiver names limited to `{"log", "logger"}` and methods to the structlog/stdlib level set. `STAR_KWARG_EXEMPTIONS: dict[str, str]` with one entry — `services/audit/purge.py`, reason quoting `_log_run`'s docstring. `main() -> int` prints `path:line: keyword` offenders and the remedy; `if __name__ == "__main__": raise SystemExit(main())`.
- [x] `lineworker/server/tests/test_log_phi_lint.py` — new. `test_no_log_call_passes_a_keyword_off_the_allowlist`; `test_the_log_guard_would_notice_a_leaking_call` (parametrized smells: `body=`, `prompt=`, `diagnosis=`, `notes=`, `content=`, an f-string value, a `**` unpack in an unexempted file); `test_the_log_guard_leaves_innocent_calls_alone` (parametrized: `claim_id=`, no kwargs, a non-logger receiver named `body`, `error=type(exc).__name__`); `test_every_star_kwarg_exemption_still_exists`; `test_every_allowlisted_key_is_still_used_by_some_log_call` (a dead entry is a permission nobody is exercising).
- [x] `lineworker/server/tests/test_logging.py` — extend: `test_a_key_off_the_allowlist_has_its_value_blocked` and `test_allowlisted_keys_and_the_event_name_survive_the_processor`, both through `configure_logging` + `capfd` in the file's existing style.
- [x] `lineworker/server/tests/test_log_phi_runtime.py` — new, `pytestmark = requires_db`. Capture stderr while exercising the two flows `test_copilot_logging.py` does not cover: an audited inline claim edit through its service command, and a retention job summary (`purge_expired_audit_events` / `purge_expired_checkpoints`). Assert no seeded worker name, ICD-10 code, wage figure or note text appears in any emitted line, each paired with its positive control (`audit.recorded`, `retention.*`) — the module docstring must state that rule and cite `test_copilot_logging.py`.
- [x] `lineworker/server/tests/test_pgaudit_posture.py` — new, `pytestmark = requires_db`. `test_the_server_is_started_with_pgaudit_preloaded` (`SHOW shared_preload_libraries`, failure message carries the `docker run` recipe); `test_pgaudit_captures_ddl_and_role_classes_only` (`SHOW pgaudit.log` == `ddl, role`, `log_parameter` off, and the six DML-silencing GUCs at their pinned values); `test_the_db_log_rotates_within_a_bounded_window` (collector on, `log_filename`, `log_rotation_age`, `log_truncate_on_rotation`, and `pg_current_logfile()` resolving under the data directory); `test_a_ddl_statement_is_logged_and_a_dml_literal_is_not` — run a `CREATE TABLE`/`DROP TABLE` probe and an INSERT carrying a distinctive literal, poll `pg_read_file(pg_current_logfile())` for up to ~5s (the collector writes asynchronously), assert the `AUDIT`/`DDL` line is present **and** the literal absent.
- [x] `lineworker/server/tests/test_audit_grants.py` — new, `pytestmark = requires_db`, driven off `seeded_db_url`. Assert from the catalog: `role_table_grants` for `audit_event` × `lineworker_app` `== {"SELECT", "INSERT"}`; for `audit_redactor` `== {"SELECT", "DELETE"}`; `role_column_grants` for `audit_redactor` `== {("before","UPDATE"), ("after","UPDATE")}` and no other column; the full grantee set on `audit_event` ⊆ `{lineworker_app, audit_redactor, <owner>}`; and the five `pg_policies` rows by name with `audit_redactor_delete`'s qualifier still carrying an `interval` floor. Docstring: this story verifies, 8.1 granted, and the `==` is deliberate so a widening migration fails here.
- [x] `lineworker/server/tests/test_deploy_tls_posture.py` — new, no DB. Text-matched over `deploy/` in `test_ai_insights.py:431`'s style: the pgaudit/logging flag list appears in `compose.yaml`, `compose.e2e.yaml` and `compose.prod.yaml` (driven off one tuple in the test, so a flag added to one file and not the others fails); `ssl=on`/`ssl_cert_file`/`ssl_key_file` appear **only** in `compose.prod.yaml`; `compose.prod.yaml` carries `sslmode=verify-full` and `PGSSLROOTCERT`; `nginx/tls.conf` carries `listen 443 ssl`, the 308 redirect, `ssl_certificate`, and the SSE proxy settings copied from `default.conf`; `default.conf` still has no `ssl` directive; `.env.example` documents `TLS_CERT_DIR` and `PG_TLS_DIR`.
- [x] `lineworker/server/tests/conftest.py` — replace the docstring's `docker run … pgvector/pgvector:pg18` recipe with `docker build -t lineworker/postgres:pg18-pgaudit deploy/postgres` followed by a `docker run … -c shared_preload_libraries=pgaudit …` line, and state that migration `0050` makes the plain image fail on `alembic upgrade` rather than skip.
- [x] `.github/workflows/ci.yaml` — `migrations` job: drop the `services.postgres` block (a service container cannot use a locally built image) and replace it with a `docker build` + `docker run -d` step against `deploy/postgres` carrying the same flag list, plus a bounded `pg_isready` wait; keep both env URLs unchanged. `server` job: add a `log PHI lint` step running `uv run python -m scripts.lint_log_phi` beside ruff/mypy. `compose` job: render the prod overlay to `/tmp/prod.json` and pass it to `check_model_ports.py` with the other three.
- [x] `lineworker/e2e/stories/8-2-database-audit-log-hardening.spec.ts` — new; describe title exactly `@story:8-2 @epic:8 Database audit & log hardening`. `@smoke` happy path: log in as the handler persona, open a seeded claim, make one audited inline edit, assert it persists — the console working over the hardened stack is the story's browser-observable claim. Then structural assertions through `psqlQuery`: the pgaudit and rotation GUCs read back at their pinned values, a DDL probe appears in `pg_read_file(pg_current_logfile())`, and the value just typed into the edit appears nowhere in it. Module docstring must state why it is ops-only and why TLS is not asserted here (prod-profile-only; covered by `test_deploy_tls_posture.py`).
- [x] `lineworker/README.md` — update the E2E/server sections: the prod invocation is now `-f deploy/compose.yaml -f deploy/compose.gpu.yaml -f deploy/compose.prod.yaml` with `TLS_CERT_DIR`/`PG_TLS_DIR` set, and the local test database must be the built pgaudit image.
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` — append a Story 8.2 section recording: the seven-day rotation window as a *time* bound rather than a byte bound (Design Note 2); `log_min_error_statement=panic` trading error-statement debuggability for the PHI surface; the blocked-value processor being a backstop whose firing is invisible until someone reads a log line; that pgaudit `role` class capture is not shipped anywhere (log shipping deferred, 8.4); and that CI still cannot boot the prod profile, so TLS is proven by configuration and by the two documented manual commands only.

**Acceptance Criteria:**
- Given the composed stack in any profile, when postgres starts, then `pgaudit` is preloaded, `pgaudit.log` is `ddl, role`, every DML-logging GUC is at its pinned off value, and the collector writes a bounded, rotating log set inside PGDATA.
- Given a fresh database, when `alembic upgrade head` runs, then `0050_pgaudit` applies clean on a configured Postgres and fails loudly on an unconfigured one.
- Given the server suite, when it runs against the fresh-migration DB, then the grant/RLS posture of `audit_event` is asserted from the catalog and any widening of it fails the suite.
- Given the whole scanned tree, when the log lint runs, then no log call passes a keyword off the allowlist or an f-string value, and the guard's own controls prove it would notice one.
- Given the prod profile, when its files are read, then TLS is present at the nginx ingress and on both API-to-Postgres URLs, and absent from the dev and e2e profiles.
- Given the composed e2e stack, when `8-2-database-audit-log-hardening.spec.ts` runs against a freshly reset DB, then it passes.

## Spec Change Log

## Review Triage Log

### 2026-08-22 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 22: (high 2, medium 12, low 8)
- defer: 8: (high 1, medium 5, low 2)
- reject: 5: (high 0, medium 2, low 3)
- addressed_findings:
  - `[high]` `[patch]` The prod overlay set `ENV: prod`, which `api/app.py:437` refuses unconditionally — Story 1.3 made it a boot refusal while persona login is the auth mechanism — so the api would crash-loop and `web`, which waits on `service_healthy`, would never come up. `test_deploy_tls_posture.py` asserted the setting's *presence*, so the suite enforced an unbootable profile, and CI never noticed because the compose job runs `config` and never `up`. The directive is gone, the assertion is inverted with the refusal quoted, the README says the profile is not bootable until the IdP decision lands, and 8.4 inherits it. This spec's own task list is what asked for `ENV: prod`; its text is corrected so spec and code agree.
  - `[high]` `[patch]` The `docker run` recipe printed by the posture suite's own failure message — and repeated in `README.md` and in this spec's Verification block — started a database that failed that suite. Verified: under the short flag list `pgaudit.log_catalog` is `on` (asserted `off`) and `log_parameter_max_length` is `-1` (asserted `"0"`), giving `1 failed, 3 passed`. All three recipes are now the full list `conftest.py` already carried, and `test_the_documented_recipe_would_actually_pass_this_suite` reads the module's own asserted GUC names out of its AST and requires each to appear in `RECIPE`.
  - `[medium]` `[patch]` The compose text assertions matched `pgaudit.log=ddl,role` as a substring, so `ddl,role,write` would have passed every one of them — and `compose.prod.yaml` is never booted, so for the prod profile the text test is the only check. The class list is now parsed and compared as a set.
  - `[medium]` `[patch]` Both DML-absence probes raced the collector they document: each polled until the `CREATE TABLE` line appeared, then asserted the *later* INSERT's literal was absent from that same snapshot. Both now poll for a trailing `DROP TABLE` issued after the DML, so the marker being waited on is flushed after the statement being probed.
  - `[medium]` `[patch]` The e2e spec asserted the edited value was absent from the DB log with no positive control, so it passed identically against a NULL `pg_current_logfile()` or an unflushed log. A `%AUDIT:%` count is now asserted non-empty first.
  - `[medium]` `[patch]` The e2e probe ran `INSERT` under `ON_ERROR_STOP=1` with the `DROP TABLE` after it, so a throwing insert leaked a probe table into every later spec. Now `try`/`finally`.
  - `[medium]` `[patch]` `test_no_log_call_in_the_edit_path_uses_a_non_allowlisted_key` never called `update_claim_fields` despite its name. It does now, against a fresh value (the module-scoped DB meant reusing the earlier one made the command a no-op that logs nothing) with an assertion that the field name was emitted, so a future no-op fails rather than passing silently.
  - `[medium]` `[patch]` The lint treated only f-strings as interpolation, so `error=str(exc)` — an exception message routinely quoting the value its statement carried — passed under an allowlisted key. `str`/`repr`/`.format`/`%`-on-a-literal are now flagged under any keyword, narrowly enough that `sorted(set(failures))` stays innocent.
  - `[medium]` `[patch]` The URL guard denylisted five libpq file parameters, but SQLAlchemy's asyncpg dialect forwards *every* query parameter as a keyword, so `gssencmode`, `channel_binding`, `sslnegotiation` and friends still reached `asyncpg.connect()` and failed at first connect in prod — the exact failure the guard exists to prevent. Inverted to an allowlist, with twelve parametrized refusals and two negative controls where there had been no coverage at all.
  - `[medium]` `[patch]` The lint checked `LOG_KEY_ALLOWLIST` but not `_META_KEYS`, so `log.error("x", exc_info=True)` was reported as a leak with a remedy that told the author to allowlist it — which would then fail the allowlist's own staleness test. Two constants that must agree with the gate reading one, which is the failure `logging_config`'s docstring claims to have avoided. It now reads both.
  - `[medium]` `[patch]` The lint was blind to `log.bind(body=…)` and `bind_contextvars(body=…)`, neither of which is a level call. Both are now recognised, with dotted-receiver resolution so a chained `log.bind(…).info(…)` is caught at the `bind`.
  - `[medium]` `[patch]` The prod overlay mounted the whole `${PG_TLS_DIR}` — server certificate, **private key** and CA — into the api container, which needs only `ca.crt` for `PGSSLROOTCERT`. A story about least-privilege audit posture was handing the application the database's signing key. Narrowed to the single file and asserted.
  - `[medium]` `[patch]` Nothing said that `sslmode=verify-full` against host `postgres` requires that name in the server certificate's SAN — so the ingress certificate (public hostname) and the database certificate are two different certificates, and getting it wrong fails at first connect in prod. Now stated at the knob and in the overlay header, and asserted.
  - `[medium]` `[patch]` CI's readiness probe used `docker exec pg_isready`, which answers over the unix socket that the image's initdb-time temporary server listens on with TCP disabled — while the next step connects from the runner over TCP. Now probes `pg_isready -h 127.0.0.1 -p 5432` from the runner.
  - `[low]` `[patch]` The lint scanned six package roots but not `server/*.py`, so a log call in `config.py` or any future top-level module was unchecked. Latent (neither logs today); closed.
  - `[low]` `[patch]` The lint's vacuity guard was an `assert` in a `python -m` entry point, so under `-O` it would vanish and the lint would exit 0 over an empty tree — indistinguishable from a clean one, which its own docstring says must never happen. Raises now.
  - `[low]` `[patch]` `assert "ssl" not in default` was a bare substring over the whole of `default.conf`, and `tls.conf` instructs future editors to keep the two in step — so the first comment naming `ssl_certificate` would have failed the suite. Anchored to directives.
  - `[low]` `[patch]` Nothing asserted that the rendered prod profile stops publishing 8080; `ports: !override` was checked only as text. The compose CI job now asserts the rendered `web` service publishes exactly 443 and 80.
  - `[low]` `[patch]` Migration 0050's docstring promised a loud refusal unconditionally while arguing three paragraphs later that `IF NOT EXISTS` makes it a no-op where the extension outlived its preload. The guarantee is now stated conditionally and points at the runtime posture assertions for the case it does not cover.
  - `[low]` `[patch]` The TLS server block shipped no HSTS, so the 308 redirect is stripped by any active MITM on the first plaintext request — while the posture test called it "moved anybody to TLS". Added, with the argument, and asserted.
  - `[low]` `[patch]` Four `# noqa: S608` comments suppressed flake8-bandit, which this project does not enable. Removed.
  - `[low]` `[patch]` `deploy/.env.example` wrote its two *required*, `${…:?}`-guarded variables as commented-out defaults in the same style as every optional knob, so copying the template produced a file whose mandatory settings were inert. Written live.

## Design Notes

**1. Why a `command:` flag list and not a mounted `postgresql.conf`.** Three mechanisms were available. Baking settings into `/usr/share/postgresql/postgresql.conf.sample` in the image applies only at `initdb`, so the existing `pgdata` volume would keep the stock posture and the drift would be invisible — the exact failure the story's "identical mechanism in dev and prod so drift can't hide" is aimed at. `config_file=` requires shipping a complete `postgresql.conf` and thereby owning every default in it. `-c` flags apply on every start regardless of when the volume was created, are greppable as text (which is how `test_deploy_tls_posture.py` and `test_ai_insights.py:431` both work), and make a weakened setting a reviewable one-line diff. The cost is that the list is restated in three files; that cost is paid by a test that fails when they disagree, not by a comment asking people to remember.

**2. Bounded rotation is bounded in *days*.** `log_filename=postgresql-%a.log` + `log_truncate_on_rotation=on` + `log_rotation_age=1d` + `log_rotation_size=0` is Postgres's own documented recipe for "keep 7 days and no more": seven files, one per weekday, each truncated when its day comes round. Setting `log_rotation_size` to a byte cap instead would rotate *within* a day back onto the same `%a` filename and silently destroy that morning's lines — a worse property for an audit-adjacent log than a size bound is worth. What makes the time bound sufficient here is the posture itself: with `pgaudit.log=ddl,role` and every DML class off, a day's log is connection lines and schema changes, not traffic. Recorded in `deferred-work.md` because "bounded" is being read as time, not bytes.

**3. `sslmode` reaches asyncpg as `ssl`, and the CA reaches it through the environment.** SQLAlchemy's asyncpg dialect does `opts.update(url.query)` and hands the result to `asyncpg.connect()`, which has no `sslmode` parameter — so `DATABASE_URL=…?sslmode=verify-full` would boot the sync engines fine and fail the async one at first connect, in prod only, which is the worst place to find out. `asyncpg` does accept the identical libpq strings under the name `ssl`, so `_with_driver` renames the parameter for that driver and leaves it alone for psycopg (which speaks libpq natively, and which is also what the copilot's `AsyncPostgresSaver` pool uses). The file parameters have no such equivalent — `asyncpg` reads `sslrootcert` only from a DSN string or from `PGSSLROOTCERT`, never from a keyword — so rather than silently dropping them the settings validator refuses them in the URL and the prod overlay sets `PGSSLROOTCERT`, which **both** drivers honour. One mechanism, stated once.

**4. A new prod overlay rather than more `compose.gpu.yaml`.** That file's header argues at length that it holds exactly the GPU delta and that anything else in it becomes drift. TLS is not a GPU concern, and the story that finishes the prod profile is 8.4 — so `compose.prod.yaml` gives 8.4 an obvious file to complete and keeps `compose.gpu.yaml`'s argument true. The `ports: !override` on `web` is load-bearing: compose *appends* port lists across files, so without it prod would keep publishing `8080:80` beside `443`.

**5. The processor blanks the value, it does not drop the key.** Dropping a non-allowlisted key would make a leaking call look like a clean one and delete the operational signal at the same time. Replacing the value keeps the event name, the line and the offending key visible while removing the content — so the backstop firing is diagnosable, and the lint (which is the actual gate) has something to be pointed at.

## Verification

**Commands:**
- `cd lineworker && docker build -t lineworker/postgres:pg18-pgaudit deploy/postgres` — expected: the image builds and `postgresql-18-pgaudit` installs.
- `docker run -d --name lw-pg -e POSTGRES_USER=lineworker -e POSTGRES_PASSWORD=test -e POSTGRES_DB=lineworker -p 55432:5432 lineworker/postgres:pg18-pgaudit postgres -c shared_preload_libraries=pgaudit -c pgaudit.log=ddl,role -c pgaudit.log_catalog=off -c pgaudit.log_parameter=off -c pgaudit.log_relation=off -c pgaudit.log_statement_once=on -c log_statement=none -c log_duration=off -c log_min_duration_statement=-1 -c log_min_error_statement=panic -c log_parameter_max_length=0 -c log_parameter_max_length_on_error=0 -c logging_collector=on -c log_destination=stderr -c log_directory=log -c log_filename=postgresql-%a.log -c log_rotation_age=1d -c log_rotation_size=0 -c log_truncate_on_rotation=on` — expected: healthy; this is the DB the suite below runs against. The **whole** flag list, identical to `deploy/compose.yaml`'s `command:` and to `server/tests/conftest.py`'s: `pgaudit.log_catalog` defaults to `on` and `log_parameter_max_length` to `-1`, so an abbreviated recipe starts a database that fails `tests/test_pgaudit_posture.py` on its own posture.
- `cd lineworker/server && uv run ruff check . ../deploy && uv run ruff format --check . ../deploy` — expected: clean.
- `cd lineworker/server && uv run mypy .` — expected: no errors.
- `cd lineworker/server && uv run python -m scripts.lint_log_phi` — expected: exit 0, no offenders.
- `cd lineworker/server && uv run alembic upgrade head` — expected: `0050_pgaudit` applies clean against the fresh database.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` — expected: all pass, including the five new modules and the extended `test_logging.py`. No DB test skips.
- `cd lineworker && docker compose -f deploy/compose.yaml -f deploy/compose.gpu.yaml -f deploy/compose.prod.yaml config >/dev/null` — expected: renders with `TLS_CERT_DIR`/`PG_TLS_DIR` set; the `:?` guards fail loudly without them.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml up -d --build --wait && cd e2e && npm run typecheck && npx playwright test --grep "@story:8-2\b"` — expected: the story spec passes against the freshly reset stack.

**Manual checks (if no CLI):**
- TLS at ingress and to Postgres cannot be booted by CI (no certificates in VCS). Verified by `openssl s_client -connect <host>:443 -servername <host>` showing the chain, and `SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()` from the api container returning `t` — both commands documented in `deploy/.env.example`.

## Auto Run Result

Status: done

### What was implemented

Story 8.2 hardens the three observability surfaces that Epic 8 could not distribute into feature work, and turns each from a convention into a mechanism.

**pgaudit, in every profile, by one mechanism.** A derived image (`deploy/postgres/Dockerfile`) adds `postgresql-18-pgaudit` to `pgvector/pgvector:pg18`, which does not ship it, and a twenty-flag `command:` list starts every profile with `pgaudit.log=ddl,role` and every DML-logging GUC pinned explicitly off — including `log_min_error_statement=panic`, because the default logs the text of a failing statement and that text carries its literals. `-c` flags rather than a mounted or baked configuration, because a `postgresql.conf.sample` applies only at `initdb` and the existing `pgdata` volume would have kept the stock posture invisibly. The list is byte-identical in dev and e2e and restated with three TLS additions in prod; `test_deploy_tls_posture.py` fails when they disagree, which is what pays for the restatement. The DB log lands in `log_directory=log` — relative, therefore inside PGDATA, therefore inside the volume the deployment view encrypts — on a seven-file weekday rotation. Migration `0050_pgaudit` installs the extension and, on a database that does not yet have it, refuses loudly against a server that never preloaded the library.

**The app-log PHI ban as a closed vocabulary.** `LOG_KEY_ALLOWLIST` in `logging_config.py` is the single definition of what a log call may pass; `scripts/lint_log_phi.py` is the gate that fails the build, and `_block_unallowlisted` is the runtime backstop that blanks a value the lint could not see — a keyword name computed at run time, or one arriving through the one `**` unpack the lint exempts. The value is replaced rather than the key dropped, so a leaking call does not render identically to a clean one. Runtime capture tests cover the two flows nothing covered before (an audited claim edit, a retention sweep), each paired with a named positive-control event, because a search for absent strings passes identically against a process that logged nothing.

**Grants verified, never re-granted.** `test_audit_grants.py` reads the `audit_event` posture off the catalog against the fresh-migration database: INSERT-only for `lineworker_app`, `audit_redactor` holding SELECT, column-scoped UPDATE on `before`/`after` and RLS-bounded DELETE, no other grantee holding writes, and the five policies still in place. Set equality, deliberately, so a widening migration fails here.

**TLS in a new prod overlay.** `deploy/compose.prod.yaml` carries the non-GPU prod deltas — nginx on 443 with HSTS and an 80→443 redirect, `ports: !override` so compose replaces the dev mapping instead of appending to it, `ssl=on` on Postgres, and `sslmode=verify-full` on both API URLs. `_with_driver` renames that parameter to `ssl` for asyncpg, whose `connect()` has no `sslmode` keyword and would otherwise have started the two psycopg engines perfectly and failed the async one at its first connection in prod; the settings validator refuses every other libpq-only query parameter with a message naming `PGSSLROOTCERT`.

### Files changed

**New**
- `lineworker/deploy/postgres/Dockerfile` — pgvector:pg18 plus the pgaudit package, nothing else.
- `lineworker/deploy/compose.prod.yaml` — the prod overlay: nginx TLS, Postgres TLS, `verify-full` on both URLs, a CA-only mount for the api.
- `lineworker/deploy/nginx/tls.conf` — the prod twin of `default.conf`: 308 redirect, TLS server, HSTS, the SSE proxy settings unchanged.
- `lineworker/server/data/versions/20260822_0050_pgaudit.py` — `CREATE EXTENSION pgaudit`, chaining from `0049_purge_grants`.
- `lineworker/server/scripts/lint_log_phi.py` — the AST gate over the log vocabulary.
- `lineworker/server/tests/test_pgaudit_posture.py` — the running server's settings, and a DDL/DML log probe.
- `lineworker/server/tests/test_audit_grants.py` — the catalog-level grant and policy assertions.
- `lineworker/server/tests/test_log_phi_lint.py` — the lint's rule and its three mandatory controls.
- `lineworker/server/tests/test_log_phi_runtime.py` — stderr capture over the edit and retention flows.
- `lineworker/server/tests/test_deploy_tls_posture.py` — the config-verification check standing in for the profile CI cannot boot.
- `lineworker/e2e/stories/8-2-database-audit-log-hardening.spec.ts` — the AD-15 story gate.

**Modified**
- `lineworker/deploy/compose.yaml`, `compose.e2e.yaml` — the pgaudit image and the hardening flag list.
- `lineworker/deploy/.env.example` — the Story 8.2 TLS section, with the two required variables written live.
- `lineworker/server/config.py` — the asyncpg `sslmode` translation and the URL query-parameter allowlist.
- `lineworker/server/logging_config.py` — the allowlist, the meta-key set, and the blocking processor.
- `lineworker/server/tests/conftest.py`, `test_logging.py`, `test_config.py` — the hardened test-DB recipe and the new coverage.
- `.github/workflows/ci.yaml` — the migrations job builds and starts the pgaudit image (a service container cannot use a locally built one), the server job runs the lint, the compose job renders and checks the prod profile.
- `.github/scripts/check_model_ports.py` — a prod-profile row.
- `lineworker/README.md`, `_bmad-output/implementation-artifacts/deferred-work.md`.

### Review findings

Twenty-two patches applied (2 high, 12 medium, 8 low), eight deferred, five rejected. The full triage is above.

The two high findings were both cases of a check that could not have failed. The prod overlay set `ENV: prod`, which `api/app.py` refuses unconditionally as Story 1.3's deliberate boot refusal, so the api would have crash-looped and taken `web` with it — and `test_deploy_tls_posture.py` asserted the setting's *presence*, so the suite enforced the unbootable profile. And the `docker run` recipe printed by the posture suite's own failure message started a database that failed that suite: `pgaudit.log_catalog` defaults to `on` and `log_parameter_max_length` to `-1`, both asserted otherwise. Both were reproduced before being accepted.

Five findings were rejected, two of them after being tested rather than argued. Both reviewers independently claimed that `pgaudit.log=role` would write `ALTER ROLE … PASSWORD '<literal>'` — issued on every api boot by `data/roles.py` — into the database log in cleartext, and called it critical. PostgreSQL redacts password literals in role-command logging itself: the log reads `ALTER ROLE probe3 WITH LOGIN PASSWORD <REDACTED>`, and the same for `CREATE ROLE … PASSWORD`, with zero occurrences of the probe secrets. Acting on that finding would have removed the `role` class and with it the privilege-escalation capture AD-11 asks for, to close a leak that does not exist.

### Verification performed

Independently re-run after the patch pass, against a container built from `deploy/postgres` and started with the full flag list:

- `uv run ruff check . ../deploy` and `ruff format --check` — clean, 310 files.
- `uv run mypy .` — no issues, 296 source files.
- `uv run python -m scripts.lint_log_phi` — exit 0.
- `pytest` over the story's seven modules — 111 passed; over the regression slice most exposed to the logging processor and the new migration (`test_schema_seed`, `test_photo_migration`, `test_copilot_logging`, `test_layering`, `test_purge_ownership`, `test_purge_cascade`, `test_ai_insights`) — 126 passed. Full suite during the patch pass: 3396 passed, 1 skipped (a pre-existing data-conditional skip in `test_claim_detail.py`).
- `alembic upgrade head` — applies through `0050_pgaudit` against a fresh hardened database; against stock `pgvector/pgvector:pg18` it fails at `CREATE EXTENSION pgaudit`, as designed.
- The `RECIPE` constant extracted programmatically and used verbatim to start a database — `test_pgaudit_posture.py`, 5 passed. Before the patch, 1 failed.
- `docker compose config` over the three prod files — renders; `web` publishes 443 and 80 only, the api mounts `ca.crt` and not the private key, and `ENV` resolves to `dev`.
- e2e: `@story:8-2` 4 passed, `@smoke` 39 passed. The full suite showed one failure in `7-2-trend-cohort-analytics.spec.ts:425`, reproduced identically at the baseline commit with this change stashed — pre-existing, not from this story.

### Residual risks

The prod profile is configured and not proven: no certificates exist in version control, so no job boots it, and TLS rests on `docker compose config` plus two documented manual commands. It is also not startable at all until the Deferred IdP decision lands, since `ENV=prod` is a boot refusal — so the overlay renders `ENV: dev`, which is a profile telling two stories. TLS to Postgres is permitted rather than enforced, because there is no `hostssl` `pg_hba` rule. The seven-day log bound is a time bound that a mid-week restart weakens. The postgres image is now a locally built tag with no registry behind it. And the app-log ban covers this project's own calls and the pipeline's shape, not the message body of a third-party record arriving through `foreign_pre_chain`. All eight are recorded in `deferred-work.md` with what would close them.
