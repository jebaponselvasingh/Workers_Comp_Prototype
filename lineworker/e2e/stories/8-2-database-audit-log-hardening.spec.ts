import { PERSONAS, loginAs } from "../fixtures/login";
import { psqlQuery } from "../fixtures/reset";
import { firstClaimInStage, seededField } from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 8.2 — Database Audit & Log Hardening.
 *
 * **An ops story with no UI, and its browser-observable claim is precisely
 * that.** Nothing here adds a screen, a control or a field. What the story
 * changes is the database the whole console runs on: pgaudit preloaded, DDL and
 * role capture on, every DML-logging setting pinned off, and the postmaster's
 * log going to a rotating file inside PGDATA instead of to container stdout.
 * The honest end-to-end proof of that is a handler doing a handler's job over
 * the hardened stack and it working exactly as before — so the `@smoke` path
 * logs in, opens a seeded claim, makes one audited inline edit and asserts it
 * persisted. A stack that failed to start under `shared_preload_libraries=
 * pgaudit`, or a migration that could not `CREATE EXTENSION`, never gets that
 * far: the e2e reset runs `alembic upgrade head` through `0050_pgaudit` before
 * any test in this file runs.
 *
 * **What only this file can check.** `server/tests/test_pgaudit_posture.py`
 * asserts the same GUCs against whatever database `MIGRATION_TEST_DATABASE_URL`
 * points at, which a developer starts by hand from a docstring. This asserts
 * them against the database `deploy/compose.e2e.yaml` starts — so a flag
 * dropped from that file's `command:` list, or a profile still pulling the
 * stock `pgvector/pgvector:pg18`, fails here and nowhere else.
 *
 * **TLS is deliberately not asserted here.** It is prod-profile-only: there is
 * no certificate in version control to mount, the e2e stack has none, and the
 * suite drives `http://localhost:8081` by Story 1.1's standing decision. It is
 * covered by `server/tests/test_deploy_tls_posture.py` reading
 * `compose.prod.yaml` and `nginx/tls.conf`, plus the two manual commands
 * `deploy/.env.example` documents. Asserting it from a spec would mean giving
 * the e2e profile a certificate, which is the one thing this story refuses.
 *
 * The database is reached through `psqlQuery` because nothing in the product
 * exposes a GUC or a log file, and inventing an endpoint so a spec could look
 * would be a test dictating a product surface (Story 2.3's ruling).
 */

const KAYA = PERSONAS.handler;

/**
 * What the smoke path types into the edited field.
 *
 * A fixed, distinctive token rather than `${seeded} (8-2)`, for two reasons
 * that pull the same way: it has to be searched for in the database log, so it
 * must contain no `%` or `_` (SQL `LIKE` wildcards) and no quote; and it has to
 * be unmistakable, because "the seeded value with a suffix" shares every word
 * it has with the seed itself and would make the absence assertion weak.
 */
const EDITED_INJURY_TYPE = "ZZ82 distinctive injury value no db log may carry ZZ";

/** Every pgaudit and rotation setting the compose profile must have produced. */
const PINNED_SETTINGS: ReadonlyArray<readonly [string, string]> = [
  ["pgaudit.log", "ddl,role"],
  ["pgaudit.log_parameter", "off"],
  ["pgaudit.log_relation", "off"],
  ["pgaudit.log_catalog", "off"],
  ["log_statement", "none"],
  ["log_duration", "off"],
  ["log_min_duration_statement", "-1"],
  ["log_min_error_statement", "panic"],
  ["log_parameter_max_length", "0"],
  ["log_parameter_max_length_on_error", "0"],
  ["logging_collector", "on"],
  ["log_filename", "postgresql-%a.log"],
  ["log_rotation_age", "1d"],
  ["log_rotation_size", "0"],
  ["log_truncate_on_rotation", "on"],
];

function setting(name: string): string {
  return psqlQuery(`SELECT current_setting('${name}')`)[0];
}

/**
 * Lines of the postmaster's current log file matching a SQL `LIKE` pattern.
 *
 * **Filtered in the database, deliberately, and it is not an optimisation.**
 * The obvious spelling is `SELECT pg_read_file(pg_current_logfile())` and a
 * `String.includes` on the result — which is what this did first, and it fails
 * with `spawnSync docker ENOBUFS` partway through a full suite run. Every spec
 * file resets the database, every reset replays fifty migrations, and pgaudit
 * dutifully records each one as a DDL line: the log passes `execFileSync`'s
 * one-megabyte default buffer after a few dozen files, so the spec would pass
 * alone and fail in CI, which is the worst way for a gate to be wrong.
 *
 * Splitting server-side keeps the output bounded by the number of *matching*
 * lines instead of by the size of the log. Every caller below matches on a
 * per-run unique token, so the result is a handful of lines or none.
 *
 * The patterns are built from literals and timestamps in this file — no user
 * input reaches them — but they must still avoid `%` and `_`, which are LIKE
 * wildcards, or an "absent" assertion would quietly match more than it says.
 */
function logLines(likePattern: string): string[] {
  return psqlQuery(
    "SELECT line FROM regexp_split_to_table(pg_read_file(pg_current_logfile()), E'\\n') " +
      `AS line WHERE line LIKE '${likePattern}'`,
  );
}

/**
 * How many lines of the current log file match `likePattern`.
 *
 * The positive-control twin of `logLines`, and it counts rather than returning
 * because the control's pattern is deliberately broad. `logLines("%AUDIT:%")`
 * would hand back every pgaudit line in the file — fifty migrations' worth of
 * DDL after every spec file's reset — which is the `spawnSync docker ENOBUFS`
 * failure `logLines`' own comment describes, reintroduced by the assertion
 * meant to make the file trustworthy. `count(*)` is one row whatever the log
 * holds.
 */
function countLogLines(likePattern: string): number {
  return Number(
    psqlQuery(
      "SELECT count(*) FROM regexp_split_to_table(pg_read_file(pg_current_logfile()), E'\\n') " +
        `AS line WHERE line LIKE '${likePattern}'`,
    )[0],
  );
}

/**
 * Poll for a log line matching `likePattern`, or give up after ~5s.
 *
 * `logging_collector` hands lines to a separate process, so reading
 * immediately after the statement commits genuinely races it. Bounded rather
 * than unbounded so a broken posture fails in five seconds instead of hanging
 * the suite — `deploy/compose.yaml`'s ollama entrypoint makes the same argument
 * about the same hazard.
 */
async function logLinesEventually(likePattern: string): Promise<string[]> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const lines = logLines(likePattern);
    if (lines.length > 0) return lines;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return [];
}

/** `logLinesEventually` for a pattern too broad to return — see `countLogLines`. */
async function countLogLinesEventually(likePattern: string): Promise<number> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const count = countLogLines(likePattern);
    if (count > 0) return count;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return 0;
}

test.describe("@story:8-2 @epic:8 Database audit & log hardening", () => {
  test("@smoke the console works unchanged over the hardened database", async ({ page }) => {
    // The whole story, from a handler's side: the stack came up with pgaudit
    // preloaded, the migrations ran through `0050_pgaudit`, and an audited
    // write still lands. Every structural assertion below is worthless if this
    // one fails, because it would mean the console did not run at all.
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");
    const seeded = seededField(claimId, "injury_type");

    await loginAs(page, KAYA);
    await page.goto(`/workspace?claim=${claimId}`);
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(claimId);

    const input = byTestId(page, "edit-injuryType");
    await expect(input).toHaveValue(seeded);
    await input.fill(EDITED_INJURY_TYPE);
    await input.press("Enter");

    // Persisted, not merely rendered — a reload is what separates the two.
    await expect(byTestId(page, "case-header-injury")).toContainText(EDITED_INJURY_TYPE);
    await page.reload();
    await expect(byTestId(page, "edit-injuryType")).toHaveValue(EDITED_INJURY_TYPE);

    // …and the audit row is there, which is the thing pgaudit is deliberately
    // NOT duplicating: AD-4 keeps value-level history here, where the Story 8.1
    // cascade can redact it.
    const audited = psqlQuery(
      "SELECT count(*) FROM audit_event WHERE action = 'update_claim_fields' " +
        `AND entity_id = '${claimId}'`,
    );
    expect(Number(audited[0])).toBeGreaterThan(0);

    // The edited value must not be in the database's own log. Asserted here,
    // in the same test that put it there, because this is the only place in
    // the suite where a real value typed by a real browser meets the real
    // postmaster — the pytest version writes its literal over a direct
    // connection and cannot see what the API's statements produced.
    //
    // The positive control comes first, and without it the line below is the
    // failure `server/tests/test_copilot_logging.py`'s module docstring names
    // as this repository's rule: a search for an absent string passes
    // identically against a log that is empty, unreadable or not being written
    // at all. `pg_current_logfile()` returning NULL would make `logLines`
    // return nothing for every pattern for ever, and every assertion in this
    // file would stay green while proving nothing. So: the log is readable and
    // pgaudit is writing to it — the reset alone replays fifty migrations of
    // DDL — and only then is "the value is not in it" a claim about the value.
    expect(
      await countLogLinesEventually("%AUDIT:%"),
      "no pgaudit AUDIT line exists at all, so the absence assertion below is vacuous",
    ).toBeGreaterThan(0);
    expect(logLines(`%${EDITED_INJURY_TYPE}%`)).toEqual([]);
  });

  test("postgres started with the profile's pgaudit and rotation settings", () => {
    // The premise: without the preload every other setting below is inert and
    // `CREATE EXTENSION pgaudit` would have failed the reset.
    expect(setting("shared_preload_libraries").split(",")).toContain("pgaudit");

    for (const [name, expected] of PINNED_SETTINGS) {
      expect(setting(name), `${name} is not at its pinned value`).toBe(expected);
    }

    // The log lands inside PGDATA — a relative `log_directory`, therefore
    // inside the volume the deployment view encrypts at rest (AD-11). An
    // absolute path here would mean the DDL record was written somewhere
    // nobody had decided to encrypt.
    const current = psqlQuery("SELECT pg_current_logfile()")[0];
    expect(current).toBeTruthy();
    expect(current.startsWith("/")).toBe(false);
    expect(current.startsWith("log/")).toBe(true);
  });

  test("a DDL statement reaches the DB log and a DML literal does not", async () => {
    // The posture as behaviour rather than as configuration. The DDL half is
    // the positive control for the DML half: if no AUDIT line appears then
    // pgaudit wrote nothing at all, and "the literal is absent" would be true
    // of an empty file.
    //
    // No underscores in either token: they are `LIKE` wildcards, and a probe
    // name containing one would make the absence assertions below looser than
    // they read.
    //
    // try/finally around the probe, matching the pytest twin: `psqlQuery` runs
    // with ON_ERROR_STOP=1, so an INSERT that throws would skip the DROP and
    // leave the table behind in a database every later spec file shares. The
    // reset drops the *schema*, so it would not survive the suite — but it
    // would survive the rest of this file, and a stray relation in the search
    // path is the kind of state that makes the next failure look like
    // something else.
    const stamp = `ZZE2E82${Date.now()}ZZ`;
    const table = `pgauditprobe${Date.now()}`;
    try {
      psqlQuery(`CREATE TABLE ${table} (note text)`);
      psqlQuery(`INSERT INTO ${table} (note) VALUES ('${stamp}')`);
    } finally {
      psqlQuery(`DROP TABLE IF EXISTS ${table}`);
    }

    // **Poll for the DROP, which ran last, and not for the CREATE.** The
    // collector writes asynchronously — that is why there is a poll at all —
    // and a snapshot taken the moment the CREATE line lands says nothing about
    // whether a leaked INSERT line had been flushed yet, because the INSERT
    // came after it. Waiting for the trailing DDL means everything the session
    // issued is in the file, so "absent" is absent rather than not-yet.
    const flushed = await logLinesEventually(`%AUDIT:%DROP TABLE IF EXISTS ${table}%`);
    expect(flushed.length, "the DB log never caught up with this test's statements").toBeGreaterThan(
      0,
    );

    const ddl = logLines(`%AUDIT:%CREATE TABLE ${table}%`);
    expect(ddl.length, "no pgaudit AUDIT line for the DDL probe").toBeGreaterThan(0);
    expect(ddl[0]).toContain(",DDL,");

    expect(logLines(`%${stamp}%`), "an INSERT's literal reached the database log").toEqual([]);
    expect(logLines(`%INSERT INTO ${table}%`)).toEqual([]);
  });
});
