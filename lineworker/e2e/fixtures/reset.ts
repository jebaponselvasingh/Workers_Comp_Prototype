import { execFileSync } from "node:child_process";
import path from "node:path";

const COMPOSE_FILE = path.resolve(import.meta.dirname, "../../deploy/compose.e2e.yaml");

const E2E_OWNER_URL = "postgresql://lineworker_e2e_owner:lineworker_e2e@postgres:5432/lineworker";
const E2E_OWNER_LOCAL_URL = E2E_OWNER_URL.replace("@postgres:", "@localhost:");

function compose(...args: string[]): void {
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
