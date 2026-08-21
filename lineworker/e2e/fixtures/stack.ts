import { execFileSync } from "node:child_process";

import { expect, request as playwrightRequest, type APIRequestContext } from "@playwright/test";

import { COMPOSE_FILE, compose } from "./reset";

/**
 * Container control for the e2e profile — stop and start the `model-stub`
 * (AD-15, Story 6.6).
 *
 * AD-15 names this technique in as many words: "copilot degradation specs stop
 * the stub and assert the `ai_unavailable` path". There is no other way to
 * produce a genuine outage in this stack — the stub has no control endpoint by
 * deliberate design (`deploy/model-stub/app.py` argues that its determinism
 * comes from holding no state), and pointing the api at a different URL would
 * mean restarting the api, which is a different test.
 *
 * ## Why stopping a container from a spec is safe here, and only here
 *
 * `playwright.config.ts` sets `workers: 1` and `fullyParallel: false`, and
 * `fixtures/test.ts` **asserts** the first of those rather than trusting it. So
 * no sibling spec is running while the stub is down. The risk this file has to
 * manage is not concurrency — it is the tail: a spec that stops the stub and
 * then fails leaves every later spec file running against a broken model, and
 * the failures would be attributed to those files.
 *
 * Hence the contract callers must keep: stop in `beforeAll`, restart in
 * `afterAll` under `try/finally`, and `await startModelStub()`, which does not
 * resolve until the whole stack — container *and* api — agrees the model is
 * back.
 *
 * ## Two things have to be true again, not one
 *
 * **The container.** `docker compose start`, unlike `up --wait`, returns as
 * soon as the container process has been launched — before the stub's HTTP
 * server is listening and before compose has run a single healthcheck. So
 * `startModelStub` polls the container's own health state until compose reports
 * `healthy`, using the healthcheck already declared in `compose.e2e.yaml`
 * (`GET /api/version` on 127.0.0.1:11434) rather than a second definition of
 * "ready" invented here.
 *
 * **And the api's opinion of it.** This is the half the first cut of this file
 * named and then dismissed with "nothing runs after this file today" — true
 * only because `6-6` happens to sort last, which is a property of a filename
 * and not a decision anybody made. `ModelAvailabilityProbe` caches its answer
 * for `ai_health_probe_cache_seconds`, so a container that is healthy again is
 * still reported as `available: false` by `GET /copilot/availability` for up to
 * that long: the next spec file anyone adds would inherit a copilot pane that
 * renders degraded for no reason its own code could explain, intermittently,
 * depending on how fast the file before it ran. So the restore polls the
 * shipped endpoint until it says `true` — `awaitModelAvailability` below, which
 * is `6-6-honest-degradation.spec.ts`'s outage wait run in reverse, and the
 * reason that helper's shape is worth having twice.
 *
 * `api` depends on `model-stub` with `service_healthy`, which is **startup
 * ordering only** — there is no `restart:` policy on any service in the
 * profile, so stopping the stub does not bounce the api. That is what makes AC
 * 3 assertable at all: the console keeps serving claim screens while its model
 * is gone.
 */

/** The service name in `deploy/compose.e2e.yaml`. Spelled once. */
const MODEL_STUB = "model-stub";

/**
 * How long `startModelStub` waits for the stub to report healthy.
 *
 * Thirty seconds against a container whose own `start_period` is ten and whose
 * healthcheck interval is five: the stub holds no models and computes its
 * vectors from a hash, so it starts in under a second, and a stub that is not
 * healthy in half a minute is broken rather than busy — which is the same
 * judgement `compose.e2e.yaml` records for its own `start_period`.
 */
const HEALTHY_TIMEOUT_MS = 30_000;

/**
 * How long the api is given to agree.
 *
 * Comfortably more than the default `ai_health_probe_cache_seconds` (ten), so
 * the wait covers a cache entry taken the instant before the container came
 * back, plus the poll that replaces it. Longer than the container wait above
 * because it is bounded by a knob a deployment may retune, where that one is
 * bounded by how fast a process starts.
 */
const AVAILABLE_TIMEOUT_MS = 30_000;

/** How often the health state and the endpoint are re-read while waiting. */
const HEALTH_POLL_MS = 500;

/**
 * The console's origin, resolved exactly as `playwright.config.ts` resolves it.
 *
 * Spelled again rather than imported, because the request context built below
 * is created outside a test — no `baseURL` from `use` is in scope there — and
 * an origin that silently differed from the suite's would poll a stack the
 * tests are not running against.
 */
const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:8081";

/**
 * Stop the model container. Everything that needs a model now fails honestly.
 *
 * `stop` rather than `kill`, so the container shuts down the way it would in an
 * operator's hands; `stop` rather than `down`, so the container and its network
 * attachment survive and `start` can bring the same one back.
 */
export function stopModelStub(): void {
  compose("stop", MODEL_STUB);
}

/**
 * Start it again and **wait until the whole stack agrees it is back**.
 *
 * The wait is the point — see the module docstring — and it is now two waits,
 * because there are two things that were made false and only one of them is a
 * container. Throws rather than returning a boolean: a caller in an `afterAll`
 * has nothing useful to do with `false`, and a silent failure here is a broken
 * stack handed to every later spec file with no line in the log saying who
 * broke it.
 *
 * Asynchronous, unlike the rest of `fixtures/`, and that is a deliberate
 * reversal. The earlier version was synchronous so that a hook could not forget
 * an `await` — a real hazard, addressed here by having nothing useful to
 * return, so a forgotten `await` surfaces as an unhandled rejection rather than
 * as silence. Polling an HTTP endpoint from a synchronous function would mean
 * blocking the event loop the request itself needs, which is not a trade this
 * file gets to make.
 */
export async function startModelStub(): Promise<void> {
  compose("start", MODEL_STUB);
  const deadline = Date.now() + HEALTHY_TIMEOUT_MS;
  for (;;) {
    const health = modelStubHealth();
    if (health === "healthy") break;
    if (Date.now() > deadline) {
      // **Which of the two it saw**, because they send a reader to different
      // places. A state compose reported means the container is genuinely not
      // well; an empty one means the health could not be read at all — a
      // service with no healthcheck, or a `ps --format json` shape this file
      // cannot parse — and blaming the container for that cost thirty seconds
      // and a wrong diagnosis before this message said so.
      throw new Error(
        health === ""
          ? `could not read ${MODEL_STUB}'s health from \`docker compose ps\` within ` +
            `${HEALTHY_TIMEOUT_MS}ms; the container may be fine and the output unparseable`
          : `${MODEL_STUB} was "${health}" rather than "healthy" after ` +
            `${HEALTHY_TIMEOUT_MS}ms; later spec files would run against a model ` +
            "that is up and not answering",
      );
    }
    await sleep(HEALTH_POLL_MS);
  }
  await awaitModelAvailability(true, AVAILABLE_TIMEOUT_MS);
}

/**
 * Block until `GET /copilot/availability` reports `expected`.
 *
 * The api's own opinion, read through the shipped endpoint rather than guessed
 * at with a sleep — so the wait ends the moment the fact is true, and it keeps
 * working if `ai_health_probe_cache_seconds` is ever retuned.
 *
 * Exported because the outage direction is worth the same treatment: a spec
 * that has just stopped the container and wants to assert on a disabled control
 * has to wait for the api to notice, for the mirror-image reason. Neither
 * direction is a property of a container; both are properties of a cache.
 *
 * The endpoint is authenticated, so this logs in over the API — the one place
 * in `e2e/` that does, and it is not a shortcut past `fixtures/login.ts`'s
 * rule that specs drive the real login screen: this is not a spec and there is
 * no screen in an `afterAll`. It is a poll on infrastructure, and it asserts
 * nothing about the console.
 */
export async function awaitModelAvailability(
  expected: boolean,
  timeoutMs: number = AVAILABLE_TIMEOUT_MS,
): Promise<void> {
  const context = await signedInContext();
  try {
    await expect
      .poll(
        async () => {
          const response = await context.get("/api/copilot/availability");
          expect(response.status(), await response.text()).toBe(200);
          return ((await response.json()) as { available: boolean }).available;
        },
        {
          message: `the api never reported the model as available: ${expected}`,
          timeout: timeoutMs,
          intervals: [HEALTH_POLL_MS],
        },
      )
      .toBe(expected);
  } finally {
    await context.dispose();
  }
}

/**
 * An API request context with a session cookie, as any seeded persona.
 *
 * The first persona the login screen would offer, because which one is
 * irrelevant: the model server is up or down for everybody, and
 * `GET /copilot/availability` reads no claim and branches on no role. Picking
 * by position rather than by name keeps this out of the business of knowing
 * what the seed contains.
 */
async function signedInContext(): Promise<APIRequestContext> {
  const context = await playwrightRequest.newContext({ baseURL: BASE_URL });
  const listed = await context.get("/api/personas");
  expect(listed.status(), await listed.text()).toBe(200);
  const personas = (await listed.json()) as { items: { id: number }[] };
  expect(personas.items.length, "the seed offers no personas to log in as").toBeGreaterThan(0);

  const session = await context.post("/api/auth/login", {
    data: { personaId: personas.items[0]!.id },
  });
  expect(session.status(), await session.text()).toBe(200);
  return context;
}

/**
 * The container's health state, as compose reports it, or `""` if it cannot be
 * read at all.
 *
 * Read through `compose ps --format json` rather than `docker inspect`, so this
 * file addresses the service by its compose name exactly as `reset.ts` does and
 * never has to know the generated container name (`compose.e2e.yaml` sets no
 * `container_name:`, deliberately).
 *
 * ## Both shapes, because compose has emitted both
 *
 * `ps --format json` prints one JSON object per line on current Docker Compose
 * v2 builds and a single JSON **array** on others — older v2 releases and some
 * distro packages. The line-wise parse alone handled the array fine (one line,
 * valid JSON) and then found no `Health` on it, because the health lives on the
 * objects *inside* it. The caller then spun for thirty seconds and threw "did
 * not become healthy", blaming a container that was healthy the whole time for
 * a parser mismatch. So an array is flattened, and an unparseable line is
 * skipped rather than allowed to abort the read: `compose` writes progress
 * chatter to stdout on some builds, and one such line should not turn a health
 * check into a `SyntaxError`.
 *
 * `""` means **"health could not be read"** and is deliberately distinct from
 * a container compose calls unhealthy — a service with no healthcheck reports
 * an empty `Health` too, and the caller's error message says which of the two
 * it saw so that the next reader is not sent looking for a broken container
 * when the answer is that nothing answered the question.
 */
function modelStubHealth(): string {
  const out = execFileSync(
    "docker",
    ["compose", "-f", COMPOSE_FILE, "ps", "--format", "json", "--all", MODEL_STUB],
    { stdio: ["ignore", "pipe", "inherit"] },
  ).toString();
  for (const line of out.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      // Not a row. Compose writes other things to stdout on some builds, and
      // one of them must not look like a broken container.
      continue;
    }
    for (const row of Array.isArray(parsed) ? parsed : [parsed]) {
      const health = (row as { Health?: unknown }).Health;
      if (typeof health === "string" && health !== "") return health;
    }
  }
  return "";
}

/**
 * Wait `ms`. Asynchronous, unlike everything else in `fixtures/`.
 *
 * `reset.ts` is synchronous throughout (`execFileSync`), and this file used to
 * match it with an `Atomics.wait` on a throwaway shared buffer. That is a fine
 * way to sleep in a synchronous function and the wrong thing to do in this one:
 * `startModelStub` now awaits an HTTP poll, so blocking the event loop between
 * container-health reads would block the request it is about to make.
 */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
