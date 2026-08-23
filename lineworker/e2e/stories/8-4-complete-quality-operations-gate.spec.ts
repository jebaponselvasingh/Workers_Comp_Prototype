import { execFileSync } from "node:child_process";

import { PERSONAS, loginAs } from "../fixtures/login";
import { COMPOSE_FILE } from "../fixtures/reset";
import { byRole, byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 8.4 — Complete Quality & Operations Gate.
 *
 * **An ops/CI story, and the honest browser-observable claim is a small one.**
 * What this story ships is a bijection lint, a required-checks manifest, a
 * finished prod compose overlay and a boot runbook — none of which a Playwright
 * run can see. CI wiring cannot be asserted from inside a browser (the browser
 * is downstream of it), and the prod profile cannot be booted by anything
 * automated, because there are no certificates in version control.
 *
 * So this spec asserts the one invariant the story genuinely owns *and* a
 * browser can reach: **the composed stack is healthy end to end.** That is the
 * same invariant `deploy/DEPLOYMENT.md § 5` has an operator verify by hand on a
 * clean host, checked here against the stack CI actually starts — which makes
 * the runbook's central claim a thing that runs on every merge rather than a
 * thing somebody did once and wrote down.
 *
 * **Two tests, from the two ends of the same stack.** The `@smoke` path is the
 * outside view: a browser reaches nginx, nginx serves the built SPA, the SPA's
 * `/api/healthz` call reaches the api *through the proxy*, the api reaches the
 * database, and a persona logs in to a rendered workspace. Every hop in the
 * ingress chain is exercised by that one path, and each of them is a hop the
 * prod profile re-wires — a `depends_on` condition, a mounted `tls.conf`, a
 * `ports: !override`. The second test is the inside view: `docker compose ps`
 * says every long-running service reached `healthy`, which is AC 2's "every
 * container exposes a health endpoint" as a fact about a running stack rather
 * than as a grep over a compose file. `server/tests/test_deploy_ops_posture.py`
 * owns the file-text half; only this file can say the healthchecks pass.
 *
 * **Why this spec is not the gate it describes.** The bijection lint's own
 * proof lives in `server/tests/test_story_spec_lint.py`, the required-checks
 * manifest's in `server/tests/test_required_checks.py`, and the SSE vocabulary's
 * in `server/tests/test_stream_protocol_contract.py`. This file exists because
 * AD-15 requires every non-backlog story to have exactly one spec — and,
 * pointedly, because `scripts/lint_story_specs` counts `8-4` as an orphan story
 * key until this file exists. The lint this story adds fails on this story until
 * this file is written, which is the shortest possible demonstration that it is
 * not passing vacuously.
 */

const KAYA = PERSONAS.handler;

/**
 * The long-running services `deploy/compose.e2e.yaml` starts, and the list
 * `DEPLOYMENT.md § 5` has an operator read out of `docker compose ps`.
 *
 * Spelled out rather than derived from the compose file, deliberately: deriving
 * it would make this test agree with whatever the file happens to say, and the
 * failure worth catching is a service that stopped being started at all. A new
 * service arriving without a line here is caught on the other side, by
 * `server/tests/test_deploy_ops_posture.py`, which derives its service list from
 * the file precisely so a new one cannot slip in unchecked.
 *
 * `backup` is absent because it is profile-gated in this profile (Story 8.3) and
 * CI's `up -d --build --wait` does not pass `--profile backup`; it runs as a
 * one-off `compose run --rm` from `8-3`'s spec and is gone by the time anything
 * here looks. `model-stub` stands in for `ollama`, which this profile must not
 * declare at all (AD-5, `check_model_ports.py`).
 */
const REQUIRED_SERVICES = ["postgres", "api", "web", "model-stub"] as const;

/** One `ps` row, of the fields this file reads. */
interface ComposePs {
  Service?: string;
  State?: string;
  Health?: string;
}

/**
 * `docker compose ps --format json`, parsed, with the output bounded.
 *
 * **Bounded on purpose.** `8-2-database-audit-log-hardening.spec.ts:84–92`
 * records a real `ENOBUFS` from reading a whole container log back through
 * `execFileSync`'s one-megabyte stdout buffer, and the rule it left behind is
 * that a spec bounds anything it reads. A `ps` of five services is a few
 * kilobytes, so the cap here is not close to firing — which is the point: it is
 * declared rather than inherited, so the day somebody reaches for `logs` in this
 * file the limit is already in front of them.
 *
 * `fixtures/reset.ts::compose` is not reused because it inherits stdio and
 * returns `void` — correct for the commands that only need to succeed, useless
 * for one whose output *is* the assertion.
 *
 * **Both output shapes are accepted.** Compose emitted newline-delimited
 * objects from `ps --format json` before v2.21 and a JSON array after it, and
 * which one a runner has is not something this spec should depend on. The
 * fallback is a real fallback rather than politeness: on a runner with the older
 * compose, `JSON.parse` of the whole buffer throws, and a spec that let it throw
 * would report a CI infrastructure difference as a broken stack.
 */
function composePs(): ComposePs[] {
  const out = execFileSync("docker", ["compose", "-f", COMPOSE_FILE, "ps", "--format", "json"], {
    stdio: ["ignore", "pipe", "inherit"],
    maxBuffer: 1024 * 1024,
    encoding: "utf8",
  });

  const text = out.trim();
  if (text === "") return [];
  try {
    const parsed: unknown = JSON.parse(text);
    if (Array.isArray(parsed)) return parsed as ComposePs[];
    return [parsed as ComposePs];
  } catch {
    // NDJSON, one object per line. A line that is not JSON — a docker warning on
    // stdout, a progress fragment — used to throw a raw SyntaxError out of the
    // fixture, which reads as a broken stack rather than as output this helper
    // could not parse. Skipped here; the caller's non-empty guard is what turns
    // "nothing parsed" into a failure with a reason.
    const rows = text
      .split("\n")
      .filter((line) => line.trim() !== "")
      .flatMap((line) => {
        try {
          return [JSON.parse(line) as ComposePs];
        } catch {
          return [];
        }
      });
    if (rows.length === 0) {
      throw new Error(
        `docker compose ps returned output this spec could not parse as JSON or NDJSON: ${text.slice(0, 200)}`,
      );
    }
    return rows;
  }
}

test.describe("@story:8-4 @epic:8 complete quality & operations gate", () => {
  test("@smoke the composed stack serves the console and answers healthy through the proxy", async ({
    page,
  }) => {
    // --- the ingress serves the SPA ------------------------------------------
    // nginx is the only published port in every profile, so reaching this
    // heading means the image was built, the SPA bundle was copied into it, and
    // the container's own `wget http://127.0.0.1:80/` healthcheck was telling
    // the truth.
    await page.goto("/");
    await expect(byRole(page, "heading", "LINEWORKER")).toBeVisible();
    await expect(byRole(page, "combobox", "Log in as")).toBeVisible();

    // --- the api answers through that same ingress ---------------------------
    // Re-asserted here rather than left to `1-1-running-project-skeleton
    // .spec.ts` because the two specs are making different claims with the same
    // request. 1.1 owns "the skeleton is wired". This story owns "the stack, as
    // composed, is healthy" — and `/api/healthz` is the one call that crosses
    // every hop the prod overlay re-wires: browser → nginx `location /api/` →
    // api → postgres. `db: "ok"` is the half that cannot be faked by a proxy
    // returning a cached page.
    const health = await page.request.get("/api/healthz");
    expect(health.status()).toBe(200);
    expect(await health.json()).toEqual({ status: "ok", db: "ok" });

    // --- and a real persona reaches a rendered workspace ----------------------
    // The stack being healthy is not the same as the stack being usable: the api
    // can answer `/healthz` from a pool that has no rows, and nginx can serve an
    // SPA whose bundle cannot talk to it. Logging in drives the seeded database,
    // the session cookie and the queue read in one move, which is what "the
    // console serves the queue" means in AC 2.
    await loginAs(page, KAYA);
    await expect(page.locator('[data-testid="queue-card"]').first()).toBeVisible();

    const claimId = await byTestId(page, "queue-card-id").first().textContent();
    expect(claimId, "a queue card rendered with no claim id").toBeTruthy();
  });

  test("every long-running service in the composed stack reports itself healthy", () => {
    // AC 2's "every container exposes a health endpoint", asserted against a
    // stack that is actually running. `server/tests/test_deploy_ops_posture.py`
    // proves every service *declares* a healthcheck; only something running
    // beside the stack can prove the healthchecks pass — and CI's `up -d --wait`
    // would already have failed if they did not, which is exactly why this is
    // worth stating: a service that quietly lost its `healthcheck:` block
    // satisfies `--wait` by having nothing to wait for.
    const rows = composePs();

    // The vacuity guard, and it is the first assertion for the reason AD-15
    // gives: `ps` against the wrong project name, or a compose that changed its
    // output shape, returns `[]` — and every per-service check below then
    // iterates over nothing and passes.
    expect(
      rows.length,
      "`docker compose ps` reported no containers at all — this spec is looking at the wrong stack",
    ).toBeGreaterThanOrEqual(REQUIRED_SERVICES.length);

    const byService = new Map(rows.filter((row) => row.Service).map((row) => [row.Service, row]));
    for (const service of REQUIRED_SERVICES) {
      const row = byService.get(service);
      expect(row, `no ${service} container in the running stack`).toBeDefined();
      expect(row?.State, `${service} is not running`).toBe("running");
      // `Health` is empty for a service with no healthcheck, so `toBe("healthy")`
      // fails on both of the things that matter — a container that is unhealthy,
      // and a container nobody is checking.
      expect(row?.Health, `${service} reports health ${JSON.stringify(row?.Health)}`).toBe(
        "healthy",
      );
    }
  });
});
