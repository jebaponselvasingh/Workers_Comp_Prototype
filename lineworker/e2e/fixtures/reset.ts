import { execFileSync } from "node:child_process";
import path from "node:path";

export const COMPOSE_FILE = path.resolve(import.meta.dirname, "../../deploy/compose.e2e.yaml");

const E2E_OWNER_URL = "postgresql://lineworker_e2e_owner:lineworker_e2e@postgres:5432/lineworker";
const E2E_OWNER_LOCAL_URL = E2E_OWNER_URL.replace("@postgres:", "@localhost:");

/**
 * Run one `docker compose` subcommand against the e2e profile.
 *
 * **Exported since Story 6.6**, which needs to stop and start the `model-stub`
 * container from inside a spec (AD-15 names that as *the* degradation
 * technique). Exported rather than copied: a second `execFileSync("docker",
 * ["compose", "-f", …])` in another fixture would be a second place the compose
 * file is located, and the day the profile moves one of them would keep
 * working against a stale path.
 */
export function compose(...args: string[]): void {
  execFileSync("docker", ["compose", "-f", COMPOSE_FILE, ...args], {
    stdio: ["ignore", "inherit", "inherit"],
  });
}

/** Run SQL as the e2e owner role; returns unaligned tuple output lines. */
export function psqlQuery(sql: string): string[] {
  const out = execFileSync(
    "docker",
    [
      "compose",
      "-f",
      COMPOSE_FILE,
      "exec",
      "-T",
      "postgres",
      "psql",
      E2E_OWNER_LOCAL_URL,
      "-v",
      "ON_ERROR_STOP=1",
      "-tA",
      "-c",
      sql,
    ],
    { stdio: ["ignore", "pipe", "inherit"] },
  );
  return out.toString().trim().split("\n").filter(Boolean);
}

/**
 * Reset = drop schema → Alembic migrate → seed (AD-15).
 *
 * Every step runs under the e2e-profile-only owner role created by
 * deploy/e2e-init/01-e2e-owner.sql — including the migrate, so schema
 * objects are owned by the e2e owner, not the runtime app role (which
 * has no DDL rights per AD-4 grants, Story 1.2). The DB port is never
 * published, so all steps exec inside the compose network.
 *
 * Seed: the dev seed migration (0004) runs inside `alembic upgrade head`,
 * so the migrate step IS the seed step from Story 1.2 on.
 */
export function resetDb(): void {
  compose(
    "exec",
    "-T",
    "postgres",
    "psql",
    E2E_OWNER_LOCAL_URL,
    "-v",
    "ON_ERROR_STOP=1",
    "-c",
    "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO lineworker;",
  );
  compose(
    "exec",
    "-T",
    "-e",
    `ALEMBIC_DATABASE_URL=${E2E_OWNER_URL}`,
    "api",
    "uv",
    "run",
    "--no-dev",
    "alembic",
    "upgrade",
    "head",
  );
  recycleApiPool();
}

/**
 * Purge one claim's PHI through the shipped management command (Story 8.1).
 *
 * `python -m scripts.purge_claim WC-nnnn`, run inside the api container — the
 * same `compose exec -T api uv run --no-dev …` shape `resetDb` uses for the
 * migration, and for the same reason: the database port is never published, so
 * anything that needs a connection has to run on the compose network.
 *
 * **A named helper rather than a second `execFileSync` at the call site.** The
 * spec that needs this is an ops-shaped one — there is no purge UI and there is
 * deliberately not going to be one while the IdP decision is deferred — so the
 * temptation is to inline the invocation once and move on. That is how a second
 * place that knows the module path, the container name and the `--no-dev` flag
 * comes to exist, and `compose()` above carries the same argument about the
 * compose file's location.
 *
 * The command exits non-zero on an unknown claim, on a missing redactor
 * connection and on a claim with binaries and no store, and `compose()` lets a
 * non-zero exit throw — so a spec that calls this and carries on is a spec whose
 * purge really happened.
 */
export function purgeClaim(businessId: string): void {
  compose(
    "exec",
    "-T",
    "api",
    "uv",
    "run",
    "--no-dev",
    "python",
    "-m",
    "scripts.purge_claim",
    businessId,
  );
}

/**
 * Kill the api's pooled connections so nothing serves cached state from the
 * dropped schema; the engine runs pool_pre_ping and reconnects transparently.
 *
 * Runs as the app role itself over the container-local socket: same-role
 * termination needs no extra privilege, and (unlike pg_signal_backend) it
 * also works while the bootstrap app role is still a superuser pre-1.2.
 */
function recycleApiPool(): void {
  compose(
    "exec",
    "-T",
    "postgres",
    "psql",
    "-U",
    "lineworker",
    "-d",
    "lineworker",
    "-v",
    "ON_ERROR_STOP=1",
    "-c",
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity " +
      "WHERE usename = 'lineworker_app' AND pid <> pg_backend_pid();",
  );
}
