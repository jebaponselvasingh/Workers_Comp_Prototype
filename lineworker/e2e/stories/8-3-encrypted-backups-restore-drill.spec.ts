import { closeSync, openSync, readFileSync, readSync, readdirSync } from "node:fs";
import path from "node:path";

import { PERSONAS, loginAs } from "../fixtures/login";
import { COMPOSE_FILE, compose } from "../fixtures/reset";
import { firstClaimInStage } from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 8.3 — Encrypted Backups & Restore Drill.
 *
 * **An ops story with no UI, and the honest browser-observable claim is that
 * the console is unaffected by it.** Nothing here adds a screen, a control or a
 * field. What the story adds is a container that opens a replication connection
 * to the database the whole console runs on, holds a slot, takes a base backup
 * and dumps every table — and the way that goes wrong is not a rendering bug,
 * it is a handler finding the queue slow or the stack refusing to come up. So
 * the `@smoke` path drives a handler's real work either side of a real backup
 * run and asserts it is unchanged.
 *
 * **What only this file can check.** `server/tests/test_backup_restore.py` runs
 * the same image against whatever database `MIGRATION_TEST_DATABASE_URL` points
 * at, with an environment that test constructs. This runs it against the
 * database `deploy/compose.e2e.yaml` starts, with the environment that file
 * sets, through `docker compose run --rm backup once` — the exact invocation
 * `deploy/RESTORE-DRILL.md` tells an operator to use. A variable the compose
 * file forgot, a mount at the wrong path, a `pg_hba.conf` that is not read,
 * `depends_on` pointing at a service that is not healthy: all of those fail
 * here and nowhere else. It is the same argument
 * `8-1-phi-purge-cascade-retention.spec.ts` makes about running the shipped
 * management command rather than the Python function behind it.
 *
 * **Nothing is decrypted, and that is deliberate.** The recipient in
 * `compose.e2e.yaml` is a real `age` public key for which **no identity exists**
 * — in this repository, on any developer's machine, or anywhere else. So the
 * assertion available here is the one AD-11 actually cares about: what reached
 * the off-host destination is `age` ciphertext and carries no plaintext
 * `PGDMP` header. The round trip through decrypt and `pg_restore` needs an
 * identity and lives in the pytest module, which mints a throwaway pair per run.
 *
 * **WAL is off in this profile** (`BACKUP_WAL=off`), so this spec exercises the
 * dump, the encrypt and the copy, not the receiver. A replication slot against
 * a database whose schema is dropped before every spec file would be an
 * abandoned slot by construction; the WAL half is covered by
 * `test_backup_posture.py` as configuration and by the executed drill as
 * behaviour.
 */

const KAYA = PERSONAS.handler;

/** Where `compose.e2e.yaml` bind-mounts the backup container's state. */
const BACKUP_DIR = path.resolve(path.dirname(COMPOSE_FILE), "../e2e/.tmp/backup");
const OFFHOST_DIR = path.join(BACKUP_DIR, "offhost");
const STATUS_FILE = path.join(BACKUP_DIR, "status.json");

/** `age`'s file header — the first line of every ciphertext it writes. */
const AGE_HEADER = "age-encryption.org/v1";

/** `pg_dump -Fc`'s magic. Finding this off-host is the one forbidden outcome. */
const PGDUMP_MAGIC = "PGDMP";

/** The status file's shape, as `deploy/RESTORE-DRILL.md` documents it. */
interface BackupStatus {
  result: string;
  stage: string;
  run_id: string;
  exit_code: number;
  error: string | null;
  artifacts: Array<{ name: string; bytes: number }>;
}

/**
 * The first `n` bytes of a file, and only those.
 *
 * `readFileSync(...).subarray(0, 64)` is one line shorter and reads the whole
 * artifact into this process first — which for `base.tar.gz.age` is several
 * megabytes of ciphertext nothing is going to look at.
 * `8-2-database-audit-log-hardening.spec.ts` records the sharper version of the
 * same lesson (`spawnSync docker ENOBUFS`, from reading a whole log through
 * `execFileSync`'s one-megabyte stdout buffer), and the rule it left behind is
 * to bound anything a spec reads back. An `age` header is 21 bytes; 64 is
 * generous.
 */
function firstBytes(file: string, n: number): string {
  const fd = openSync(file, "r");
  try {
    const buffer = Buffer.alloc(n);
    const read = readSync(fd, buffer, 0, n, 0);
    return buffer.subarray(0, read).toString("latin1");
  } finally {
    closeSync(fd);
  }
}

function readStatus(): BackupStatus {
  return JSON.parse(readFileSync(STATUS_FILE, "utf8")) as BackupStatus;
}

/** One backup run, through the shipped invocation. */
function runBackupOnce(): void {
  // `--build`, and it is not belt-and-braces. The backup service is
  // profile-gated, so CI's `up -d --build --wait` skips it entirely and
  // `compose run` builds only when the tag is *absent* — meaning a developer
  // who edits `deploy/backup/backup.sh` and re-runs this spec would otherwise
  // be testing whatever image was built last week, and passing.
  compose("run", "--rm", "--build", "backup", "once");
}

test.describe("@story:8-3 @epic:8 encrypted backups & restore drill", () => {
  // Serial: the two tests after the smoke path read the status file and the
  // artifacts that the smoke path's run produces, and `beforeAll` clears the
  // scratch directory unconditionally. Without this, `--grep` on anything
  // narrower than the story tag — or a failure in the first test — reports an
  // ENOENT stack trace from the second instead of the real cause.
  test.describe.configure({ mode: "serial" });

  /**
   * Wipe the previous run's state, **through the container rather than from the
   * host.**
   *
   * The obvious spelling is `rmSync(BACKUP_DIR, { recursive: true, force: true })`.
   * It works on a developer's Mac and fails on Linux CI: the backup container
   * runs as root (deliberately — see `deploy/backup/Dockerfile`), so on a Linux
   * bind mount every file it wrote is owned by root and the Playwright process
   * is not. Doing it through a container means the deletion runs with the same
   * privileges as the writes did, on every platform.
   *
   * Unconditional, and that is the point: a stale `status.json` from a previous
   * green run would make every assertion below pass without a backup having
   * happened at all.
   */
  test.beforeAll(() => {
    compose(
      "run",
      "--rm",
      "--no-deps",
      "--entrypoint",
      "sh",
      "backup",
      "-c",
      "rm -rf /var/lib/lineworker/backup/artifacts /var/lib/lineworker/backup/offhost " +
        "/var/lib/lineworker/backup/status.json /var/lib/lineworker/backup/wal",
    );
  });

  test("@smoke a backup runs against the live stack and ships only ciphertext", async ({
    page,
  }) => {
    // --- the console works, before ------------------------------------------
    // The premise. Every assertion below is about a backup taken from a running
    // system, and "the artifact is encrypted" says nothing if the system it was
    // taken from was not serving.
    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "investigation");

    await loginAs(page, KAYA);
    await expect(page.locator('[data-testid="queue-card"]').first()).toBeVisible();
    await page.goto(`/workspace?claim=${claimId}`);
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(claimId);

    // --- one real backup run -------------------------------------------------
    // `compose()` throws on a non-zero exit, so reaching the next line means the
    // run's own exit code was 0 — which is the first half of AC 3 (a failed run
    // does not report success) and is asserted here by construction rather than
    // by reading anything.
    runBackupOnce();

    const status = readStatus();
    expect(status.result, `the run failed at stage ${status.stage}: ${status.error}`).toBe(
      "success",
    );
    expect(status.stage).toBe("done");
    expect(status.exit_code).toBe(0);
    expect(status.error).toBeNull();

    // Named artifacts with real byte counts. Without this, every "the artifact
    // is not plaintext" assertion below would pass identically against a run
    // that produced an empty directory.
    const sizes = new Map(status.artifacts.map((a) => [a.name, a.bytes]));
    for (const name of [
      "dump.age",
      "globals.sql.age",
      "base.tar.gz.age",
      "pg_wal.tar.gz.age",
      "blobs.tar.age",
    ]) {
      expect(sizes.has(name), `${name} is missing from ${[...sizes.keys()].join(", ")}`).toBe(true);
      expect(sizes.get(name), `${name} is zero bytes — a backup of nothing`).toBeGreaterThan(0);
    }

    // --- AD-11, checked where it matters: the destination ---------------------
    const shipped = readdirSync(OFFHOST_DIR);
    expect(shipped, "the run did not reach the off-host destination").toEqual([status.run_id]);

    const head = firstBytes(path.join(OFFHOST_DIR, status.run_id, "dump.age"), 64);
    expect(head.startsWith(AGE_HEADER), `shipped dump begins with ${JSON.stringify(head)}`).toBe(
      true,
    );
    expect(
      head.includes(PGDUMP_MAGIC),
      "a plaintext pg_dump header reached the off-host destination — the one outcome AD-11 forbids",
    ).toBe(false);

    // --- the console still works, after -------------------------------------
    // `pg_basebackup` issues a fast checkpoint and reads the whole data
    // directory over a replication connection. This is the assertion that a
    // backup taken from the live database leaves the live database serving.
    await page.goto("/workspace");
    await expect(page.locator('[data-testid="queue-card"]').first()).toBeVisible();
    await page.goto(`/workspace?claim=${claimId}`);
    await expect(byTestId(page, "case-header-claim-id")).toHaveText(claimId);
  });

  test("every artifact at the off-host destination is ciphertext, not just the dump", () => {
    // The smoke path checks `dump.age`, which is the artifact that would hurt
    // most. It is not the only one that would hurt: `base.tar.gz` is a copy of
    // the whole data directory, and `blobs.tar` is every document and photo
    // byte. A pipeline that encrypted the dump and shipped the base in the
    // clear would pass every assertion above.
    //
    // The manifest is the deliberate exception and is asserted to be readable:
    // it carries names, byte counts and sha256 sums of *ciphertext*, so a copy
    // at the destination can be verified for integrity by somebody who cannot
    // read a byte of what they are verifying (AD-11 permits names and sizes;
    // that is the whole design).
    const status = readStatus();
    const runDir = path.join(OFFHOST_DIR, status.run_id);
    const files = readdirSync(runDir).filter((name) => name.endsWith(".age"));
    expect(files.length, "no encrypted artifacts at the destination").toBeGreaterThan(2);

    for (const name of files) {
      const head = firstBytes(path.join(runDir, name), 64);
      expect(head.startsWith(AGE_HEADER), `${name} is not age ciphertext`).toBe(true);
      expect(head.includes(PGDUMP_MAGIC), `${name} carries a plaintext dump header`).toBe(false);
    }

    const manifest = JSON.parse(readFileSync(path.join(runDir, "manifest.json"), "utf8")) as {
      run_id: string;
      artifacts: Array<{ name: string; bytes: number; sha256: string }>;
    };
    expect(manifest.run_id).toBe(status.run_id);
    for (const entry of manifest.artifacts) {
      expect(entry.sha256, `${entry.name} has no checksum`).toMatch(/^[0-9a-f]{64}$/);
      expect(entry.bytes).toBeGreaterThan(0);
    }
  });

  test("the backup container reports its own health from its own last run", () => {
    // AC 3's binding surface, exercised rather than read. There is no monitoring
    // stack and no alerting — both deferred by decision — so `healthcheck.sh`
    // reading `status.json` is the entire mechanism by which anybody ever learns
    // that a nightly run failed.
    //
    // Only the healthy direction is provable here: the previous test left a
    // successful run behind, and making this container report unhealthy would
    // mean deliberately breaking a run in the middle of a shared e2e stack.
    // `server/tests/test_backup_restore.py::
    // test_a_failed_off_host_copy_is_recorded_and_makes_the_container_unhealthy`
    // owns the failing direction, where a broken destination costs nothing.
    compose("run", "--rm", "--no-deps", "--entrypoint", "/usr/local/bin/healthcheck.sh", "backup");
  });
});
