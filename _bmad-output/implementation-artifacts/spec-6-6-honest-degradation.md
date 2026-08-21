---
title: 'Story 6.6 — Honest Degradation'
type: 'feature'
created: '2026-08-21'
baseline_revision: 'dea454090d4f5a4e1158eece11e1d5d39c1573b6'
final_revision: '95bcad631ecf18ad7622a7c1447da079c434a5a3'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true # twenty-one findings were patched into the diff, four of them high and all four on the paths the story exists to make honest — a catch-all that reported any graph defect as a model outage, an entry point where `ai_limit` could not fire at all, a truncation flag that failed runs which had completed, and a manual retry defeated by the server's own probe cache. Six were found independently by both reviewers, which is the signal that the model wrapper and the availability latch are dense rather than that the reviewers were thorough. The error vocabulary and the false-path guarantee are settled; what a second pass should read is the wrapper's `_astream`/`_agenerate` parity under the vendor's callback contract, the per-completion truncation reset across a real tool loop, and the frontend's outage latch across query failure, forced re-check and recovery
context:
  - '{project-root}/_bmad-output/implementation-artifacts/6-6-honest-degradation.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-6-5-human-gated-writes-the-rtw-letter.md'
  - '{project-root}/docs/Architecture-LINEWORKER.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Every promise AD-14 makes about an Ollama outage is prose. `agents/chat_model.py` translates no exception, so an unreachable model server arrives at the runs endpoint as an anonymous vendor exception and is reported as `/problems/copilot-run-failed` — byte-identical to a bug in the graph. `ai_unavailable` and `ai_limit` exist nowhere in code; `_problem_frame` has no `code` member to carry them. `num_predict` is passed to the model and nothing ever notices it was hit. The `requires_llm` flag is declared on the server, surfaced to no client, and enforced by a test rather than a type. And the SPA has no way to learn the model is down other than by sending a message and failing.

**Approach:** Give the stream-error frame a closed `code` vocabulary and translate the model boundary into it, so a model outage is a typed, distinguishable terminal event rather than an anonymous 503. Bound the retry at that same seam. Detect a length-capped completion and end the run `ai_limit`. Publish one authenticated `GET /copilot/availability` carrying both model reachability and each quick-action's `requiresLlm`, so the panel disables exactly the affected inputs from server truth rather than a client-side mirror. Make the false path structurally model-free by not handing a model to builders that declare they need none. Prove the rest with a Playwright spec that stops the `model-stub` container.

## Boundaries & Constraints

**Always:**
- **There is no fallback.** No cloud code path, not behind a flag, not as a degraded mode (AD-5). No pre-authored text is ever presented as model output — the prototype's `offlineAnswer` has no successor. An outage is a typed `error` frame, never assistant-styled prose (AD-14).
- **Exactly one terminal event per run** survives unchanged. `_run_frames` still names no terminal event; `_stream` still has one terminal `yield`; `_terminal_for` remains the single classification site. New codes are new branches inside `_terminal_for`, never a second terminal yield.
- **Every `error` frame carries a `code`** drawn from one closed `Literal`, declared once server-side and mirrored once client-side. `type` and `status` keep their present meanings and present values; `code` is an RFC 9457 extension member added alongside them, never a replacement (`api/errors.py::problem_response` already merges extensions flat).
- **After any terminal error the thread is accepting again.** The advisory lock releases through the existing single `_release` helper, and no `pending_approval` is left set without a live interrupt task. Asserted for `ai_unavailable` and for both `ai_limit` shapes.
- **Bounded retry, at one seam.** A small fixed attempt count with backoff, applied only to a call that failed *before its first streamed chunk*. Never queue-and-wait, never an unbounded loop, never a mid-stream retry that would duplicate tokens. The attempt count is a config knob and a test asserts it.
- **Cancellation is never an outage.** The wrapper's catch-all must re-raise `asyncio.CancelledError` untouched and must not translate the run-level `asyncio.timeout` firing mid-call. A run that ran out of wall clock is `ai_limit`; only a failure to reach or be answered by the model server is `ai_unavailable`. The per-HTTP-request timeout (`chat_request_timeout_seconds`) *is* an unavailability — the server did not answer — and the per-run timeout is not.
- **The run path does not pre-check availability.** A `requires_llm: true` run attempts the model and reports what happened. The probe exists to drive the UI only; a second source of truth that could disagree with the run is forbidden.
- **A `requires_llm: false` action never touches the chat client** — enforced by types, not only by a test: a builder that declares no LLM is not handed a model.
- **An approved write is never lost to a model outage.** Execution precedes narration; if a model call on the resume path fails, the write and its audit row have already happened and the run ends `ai_unavailable` after them.
- **Partial output stays visible.** Whatever streamed before the failure remains in the transcript; the error frame is appended, it does not replace.
- **Disable exactly the affected inputs, never the pane** (UX-DR8). Degradation is the *disabled* case, not the read-only *absent* case. Diary tab, thread history, thread switching, the `requires_llm: false` action and every non-copilot screen stay live.
- **Outage surfaces are inline, never dialogs or toasts** (UX-DR11/NFR-3), styled from the Story 1.1 `warn` tokens, with a manual retry affordance. The copilot deliberately does not toast.
- **AD-11:** outage and limit logs carry ids and event names only — never a prompt, a token, a completion, or a probe response body.
- **Tests are amended into their opposite, never deleted** (AD-15).

**Block If:**
- The vendor surfaces no way to distinguish a length-capped completion from a complete one (neither `done_reason`/`finish_reason` on the streamed chunk's `response_metadata` nor an equivalent on the model layer). AC 4's `num_predict` half is then unbuildable without inventing a token counter, which would be a second source of truth about the model's own stop reason. Halt rather than approximate.
- Making the model boundary translate uniformly would require a second translation seam — i.e. `create_agent`'s internal model call cannot inherit the same wrapper `_narrate` uses. One seam or none; two would let free chat and QAS disagree about what an outage is.
- Any change to `/healthz`'s response shape or status semantics is required. The api container is deliberately healthy without its model, and `tests/test_problem_json.py` pins the public-route set.

**Never:**
- No new tables, columns, or migrations. No writes of any kind.
- No cloud fallback, no `ModelFallbackMiddleware`, no canned answer, no cached-answer replay dressed as a live one.
- No new copilot features, nodes, tools, prompt files, or quick-action keys.
- No monitoring/alerting stack — the probe is a UI signal, not observability. No Ollama capacity or queueing work (Deferred by decision).
- No sixth stream event name; `SERVER_EVENTS` stays a closed set of five.
- No `dark:` variants (the palette is light-only by decision).
- No automatic client re-fire of a failed run, and no polling outside TanStack Query.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Free chat, model unreachable | `POST …/runs` with a text message, stub stopped | Terminal `error`, `code: "ai_unavailable"`, `type: "/problems/ai-unavailable"`, status 503, problem+json inline; lock released; thread accepts the next message | Bounded attempts, then report |
| `requires_llm: true` action, model unreachable | `quickAction: "reserve"` | Deterministic tool output that already streamed stays; terminal `error` + `ai_unavailable` | Same |
| `requires_llm: false` action, model unreachable | `quickAction: "data_alignment"` | Executes, streams the note, terminal `done`; **zero chat-client calls** | No error expected |
| Wall-clock overrun | Run exceeds `copilot_run_timeout_seconds` | Terminal `error`, `code: "ai_limit"`, existing `type: "/problems/copilot-run-timeout"`, status 504; thread accepting | Unchanged classification, new code |
| `num_predict` overrun | Completion stops on length | Terminal `error`, `code: "ai_limit"`, `type: "/problems/copilot-output-truncated"`, status 503; the decoded tokens stay in the transcript; thread accepting | No `pending_approval` left set |
| Model recovers | Stub restarted | Next run streams to `done`; the availability query flips back on its next poll or on manual retry | No error expected |
| Availability probe | `GET /copilot/availability` | `{available, quickActions:[{key, requiresLlm}]}`; probe result cached `ai_health_probe_cache_seconds` so N clients are one upstream request | Probe failure ⇒ `available: false`, 200 — the endpoint itself never errors on an outage |
| Panel with model down | `available: false` | Composer disabled, the six `requiresLlm` buttons disabled, inline `warn` notice + "Try again"; Diary tab, transcript, thread switcher, `data_alignment` button all live | — |
| Panel while availability is loading | Query pending | Nothing disabled — an unknown state is treated as available, so a slow probe never blocks a working model | Query error ⇒ same as pending |
| Approve a pending write, model down | Resume on an interrupt-pending thread | The write executes and audits first; the run then ends `done` if no model call is needed, or `error`+`ai_unavailable` after the write | The write is never skipped or rolled back |
| RTW save proposed, model down | `proposedWrite` run | Model-free by 6.5's design — the approval card renders as normal | No error expected |
| Non-AI screens, model down | Queue, claim detail, bills, diary, dashboard | Fully functional; AI Insights renders its cached rows and `not_generated` states | Refresh Insights disables with the same notice |
| Insight refresh, model down | `POST …/insights/refresh` | Unchanged 503 problem+json; the button is pre-emptively disabled so it is not the discovery mechanism | Existing inline `insights-refresh-error` retained |

</intent-contract>

## Code Map

**Server — the model boundary**
- `agents/chat_model.py` -- `copilot_chat_model()` (`:73-104`) is the **one** construction site and therefore the one seam: wrapping the object it returns is what makes `_narrate` (`qas.py:374`) and `create_agent`'s internal call translate identically. It wraps nothing today. Its own docstring (`:80-92`) names this story for `ai_limit`.
- `agents/client.py` -- `ChatUnavailable` (`:87-94`) already exists and its docstring says "Story 6.6 owns the degradation UX; this is the exception it will be built on." **Reuse it; do not declare a second unavailability exception.** The translation ordering to copy is `:198-203` — narrow parse/validation errors first, catch-all last.
- `agents/qas.py` -- `_narrate` (`:307-403`) is the single QAS model call site; `_BUILDERS` (`:981-990`) and `build_node` (`:992-1008`) are where the `requires_llm`/`model` pairing is erased to `Callable[...]`. `_build_data_alignment` (`:890-967`) already accepts `model` and deliberately never uses it (`:916-919`).
- `agents/graph.py` -- `QuickAction` dataclass (`:130-160`, three fields: `node`, `requires_llm`, `prompt_key`), `QUICK_ACTIONS` map (`:176-194`), and the build loop (`:459-463`) that passes `model=model` to every builder. `data_alignment` (`:193`) is the only `requires_llm: False` entry.

**Server — the stream**
- `api/routers/copilot.py` -- `_problem_frame` (`:876-887`) returns exactly the four RFC 9457 members and is the only frame shape; `_terminal_for` (`:1586-1683`) is the total, single classification function with four existing branches (`copilot-empty-answer` 503, `copilot-approval-unreadable` 503, `copilot-run-timeout` 504 on `TimeoutError`, catch-all `copilot-run-failed` 503); `_stream` (`:1466`) has its one terminal `yield` at `:1572` and its `finally` release at `:1573-1584`; `_run_frames` (`:1704`) holds the `asyncio.timeout(run_timeout_seconds)` wrapper at `:1777` and raises `_Interrupted` (`:1419`) rather than yielding a terminal — **the precedent for raising `_OutputTruncated` after the astream loop**. Producer-exception sink at `:1544-1548`; `_release` (`:1386-1416`) is the single, shielded, non-fatal release. `_busy()` 409 on `state.interrupt_pending` at `:1167-1169`.
- `api/errors.py` -- `problem_response(..., **extensions)` (`:113-134`) merges extension members flat at `:127`; `RESERVED_PROBLEM_MEMBERS` (`:52`) guards name clashes. `code` is a legal extension.
- `api/app.py` -- `/healthz` (`:464-470`), DB-only and deliberately so (`:304-317`). `CopilotRuntime` (`:203-260`) carries per-run bounds so a run cannot widen its own; new probe settings belong on the same object, read at request time, not from `Settings` inside the stream.
- `config.py` -- knobs already present: `chat_request_timeout_seconds` (`:324`), `copilot_run_timeout_seconds` (`:379`), `copilot_max_output_tokens` (`:386`), `copilot_max_tool_calls_per_run` (`:401`), `ollama_base_url` (`:254`). No retry, backoff, or probe knob exists.

**Frontend**
- `web/src/api/copilot.ts` -- `SERVER_EVENTS` (`:297-303`) and `TERMINAL_EVENTS` (`:315-319`) are closed sets; `translate` (`:480`) returns the server's problem document verbatim on `error` (`:554-555`); `TRUNCATED_STREAM` (`:345-351`) is the one client-synthesised error frame and needs a code too. Hooks and the `streamRun` fetch exemption live here.
- `web/src/features/copilot/ActionsTab.tsx` -- the state owner. `busy` (`:219`), `running` (`:226`), `readOnly` (`:245`), `awaitingApproval` (`:474`); the five disable expressions at `:878`, `:907`, `:945`, `:989`, `:995`; `onError` → `setRefusal` (`:436-447`); the inline refusal notice (`:959-967`) is the exact markup a degradation notice should match.
- `web/src/features/copilot/Composer.tsx` -- **the pre-cut seam**: `disabled` (`:27`, merged at `:37`) exists and nothing passes it; its docstring (`:9-13`) reserves it for this story.
- `web/src/features/copilot/QuickActions.tsx` -- `busy` → `disabled={busy}` at `:66` is the single line to widen; the docstring (`:28-31`) says per-key degradation is this story's and that `disabled` "has one source and not two".
- `web/src/features/copilot/quickActionMeta.ts` -- three display-only fields (`:59-66`); its docstring (`:38-49`) argues against mirroring server truth client-side, which is why `requiresLlm` must arrive over the wire.
- `web/src/api/queryClient.ts` -- `refetchOnWindowFocus: false`, retry policy (`:42-55`). There is **no `refetchInterval` anywhere in the SPA**; this story adds the first and must say why.
- `web/src/features/claim-detail/insights/` -- `InsightsTab.tsx` reads the cached endpoint (fine during outage); `RefreshInsightsButton.tsx:30-31` disables only on `isPending`.
- `web/src/index.css` -- `@theme` tokens (`:21`): `--color-warn` (`:35`), `--color-warn-soft` (`:36`), `--color-error` (`:37`). Disabled idiom is `disabled:cursor-not-allowed disabled:opacity-50`.

**E2E / CI**
- `e2e/fixtures/reset.ts` -- `compose(...args)` (`:9-13`) via `execFileSync("docker", ["compose", "-f", COMPOSE_FILE, ...])` is the **only** container-control precedent and the shape to reuse.
- `e2e/fixtures/test.ts` -- per-spec-**file** DB reset, and it throws unless `workers === 1` (`:23-29`).
- `e2e/playwright.config.ts` -- `fullyParallel: false`, `workers: 1`, no `webServer`, `setup` → `stories` project dependency.
- `deploy/compose.e2e.yaml` -- no `restart:` on any service and no `container_name:`; `api` depends on `model-stub: service_healthy` for **startup ordering only**, so stopping the stub does not restart the api. Stub health target is `http://127.0.0.1:11434/api/version` (`:116-124`) — a ready-made probe path that real Ollama also serves.
- `.github/workflows/ci.yaml` -- ruff scope is `. ../deploy` (`:42`); the web job diffs regenerated `schema.d.ts` (`:70-75`); the compose job runs `check_model_ports.py` (`:157`); the e2e job derives `@story:` greps from the branch name (`:185-207`) and picks up `stories/6-6-*.spec.ts` with no registration.

## Tasks & Acceptance

**Execution:**
- [x] `lineworker/server/config.py` -- add four knobs beside the existing copilot block, each with the module's commented rationale: `chat_max_attempts: int = Field(default=2, gt=0)`, `chat_retry_backoff_seconds: float = Field(default=0.5, ge=0)`, `ai_health_probe_timeout_seconds: float = Field(default=3.0, gt=0)`, `ai_health_probe_cache_seconds: float = Field(default=10.0, gt=0)` -- bounds and probe cadence are deployment decisions, and the story that surfaces them is the story that must state them.
- [x] `lineworker/server/agents/degradation.py` -- **new**. The whole degradation seam in one module, for `approval.py`'s reason: (a) a `BaseChatModel` wrapper over the configured `ChatOllama` that translates any transport/connect/timeout failure into `agents.client.ChatUnavailable` and retries a call that failed **before its first chunk** up to `chat_max_attempts` with `chat_retry_backoff_seconds` backoff, on the async paths the copilot actually uses; (b) `probe_model(settings) -> bool`, a `GET {ollama_base_url}/api/version` with `ai_health_probe_timeout_seconds`, returning a bool and never raising, never logging a response body; (c) a small time-based cache around the probe honouring `ai_health_probe_cache_seconds` so concurrent callers are one upstream request. Reuse `ChatUnavailable` — do not declare a second exception.
- [x] `lineworker/server/agents/chat_model.py` -- wrap the constructed model in the degradation wrapper so `_narrate` and `create_agent` inherit one translation; update the docstring's forward reference to say the surface now exists.
- [x] `lineworker/server/agents/qas.py` -- split `_BUILDERS` into typed narrating and deterministic builder protocols so a `requires_llm: False` key is **not handed a model**, and `build_node` selects by the flag; drop the unused `model`/`prompt_key` parameters from `_build_data_alignment`. This closes the `_BUILDERS` erasure recorded in `deferred-work.md` from 6.4's review, which named this story as its owner.
- [x] `lineworker/server/agents/graph.py` -- adjust the build loop to the new `build_node` contract; export a stable public view of `QUICK_ACTIONS`' `(key, requires_llm)` pairs for the availability endpoint to serialise, so the router does not reach into the map's internals.
- [x] `lineworker/server/api/routers/copilot.py` -- declare a closed `StreamErrorCode` `Literal` covering `ai_unavailable`, `ai_limit` and one code per existing error branch; add `code` as a required parameter of `_problem_frame` and set it at every call site; add a `_OutputTruncated` exception raised from `_run_frames` after the astream loop when the completion stopped on length; extend `_terminal_for` with the `ChatUnavailable` branch (`/problems/ai-unavailable`, 503, `ai_unavailable`) and the `_OutputTruncated` branch (`/problems/copilot-output-truncated`, 503, `ai_limit`), and give the existing `TimeoutError` branch `code: "ai_limit"`. Do not add a terminal `yield`. Log the outage and limit events with ids and event names only.
- [x] `lineworker/server/api/routers/copilot.py` -- add `GET /copilot/availability` returning `{available, quickActions: [{key, requiresLlm}]}`, backed by the cached probe. It answers 200 with `available: false` on an outage; it never errors because the model is down. Authenticated like the rest of the router — `/healthz` is untouched.
- [x] `lineworker/server/api/app.py` -- carry the probe settings onto `CopilotRuntime` alongside the existing per-run bounds, so a request reads them from the runtime rather than `Settings`.
- [x] `lineworker/web/src/api/copilot.ts` -- add the generated availability types, a `useCopilotAvailability()` query keyed under `queryKeys.copilot`, the first `refetchInterval` in the SPA (a module constant, with a comment justifying it against `dashboard.ts`'s documented no-polling stance), a mirror of the `StreamErrorCode` union, a `isAiUnavailable(problem)` predicate beside `isThreadBusy`/`isThreadReadOnly`, and a `code` on the synthesised `TRUNCATED_STREAM` frame.
- [x] `lineworker/web/src/api/queryKeys.ts` -- add the availability key to the copilot group.
- [x] `lineworker/web/src/features/copilot/QuickActions.tsx` -- accept the set of keys to disable and widen the single `disabled` expression at `:66`; keep read-only as *absent*, degradation as *disabled*.
- [x] `lineworker/web/src/features/copilot/ActionsTab.tsx` -- consume the availability query; pass `disabled` to `Composer` (the reserved prop) and the disabled-key set to `QuickActions`; on an `error` frame whose code is `ai_unavailable`, mark unavailable reactively rather than waiting for the poll; render an inline `warn` notice (`data-testid="copilot-degraded"`, `role="status"`, matching the refusal notice's markup) carrying a "Try again" control that refetches availability — no automatic re-fire of the failed run. Treat pending/errored availability as available.
- [x] `lineworker/web/src/features/claim-detail/insights/RefreshInsightsButton.tsx` -- disable when the model is unavailable, with the same quiet inline treatment, so the 503 is not the discovery mechanism. The Insights **read** path is unchanged.
- [x] `lineworker/web/package.json` flow -- run `npm run generate:api` and commit `src/api/schema.d.ts`; CI diffs it.
- [x] `lineworker/e2e/fixtures/stack.ts` -- **new**. `stopModelStub()` / `startModelStub()` reusing `reset.ts`'s `compose()` shape and `COMPOSE_FILE` resolution (export `compose` rather than duplicating it), with `startModelStub()` polling the container's health until healthy — `docker compose start` does not wait. Built in `fixtures/` so Story 8.4's gate can reuse it.
- [x] `lineworker/server/tests/test_copilot_degradation.py` -- **new**. Unit/graph coverage: a chat client that fails at the transport yields a terminal `ai_unavailable` and an accepting thread; the retry attempt count equals `chat_max_attempts` and no more (assert the call count); a mid-stream failure is **not** retried; the `requires_llm: false` path makes zero model calls, using `test_copilot_approval.py`'s `_NeverCalledModel` device (`:107`); a length-stopped completion ends `ai_limit` with the streamed tokens retained; a wall-clock overrun ends `ai_limit`; after each, no `pending_approval` remains without a live interrupt; the availability endpoint answers `available: false` without raising and caches the probe.
- [x] `lineworker/server/tests/` -- amend the existing copilot tests that assert the four current error branches so they also assert the new `code` member; amend `test_copilot_qas.py`'s requires_llm test to the stronger structural claim now that the false builder holds no model.
- [x] `lineworker/web/src/features/copilot/ActionsTab.test.tsx`, `QuickActions.test.tsx` -- extend using `stubApi` and the `sseFrame` fixtures: with `available: false`, exactly the composer and the six `requiresLlm` buttons are disabled while the `data_alignment` button, thread switcher and Diary tab are not; an `ai_unavailable` error frame renders the typed notice and **no assistant-styled prose bubble**; "Try again" refetches and re-enables on recovery.
- [x] `lineworker/e2e/stories/6-6-honest-degradation.spec.ts` -- **new**, `test.describe("@story:6-6 @epic:6 …")`. Stop the stub in `beforeAll`, restart and re-wait in `afterAll` under `try/finally` so later spec files are unaffected. One `@smoke` test: with the stub stopped, queue, claim detail, bills and diary all load and function (AC 3). Then: the panel shows exactly the affected inputs disabled; `data_alignment` still streams to exactly one terminal `done`; a free-chat attempt ends with exactly one terminal `error` whose problem document carries `code: "ai_unavailable"`; the transcript shows the typed error state and no assistant prose. Assert event codes and structure only — never prose.

**Acceptance Criteria:**
- Given the model server is unreachable, when a `requires_llm: true` quick action or a free-chat message runs, then the run ends with exactly one terminal `error` frame carrying inline problem+json with `code: "ai_unavailable"`, the chat client was called no more than `chat_max_attempts` times, no cloud request was made, no pre-authored prose was streamed, and a subsequent message on the same thread is accepted rather than 409-ed.
- Given the same outage, when the `requires_llm: false` quick action runs, then it executes its tools, streams its note, ends with exactly one terminal `done`, and the chat client is called zero times.
- Given the same outage, when a handler uses the console, then queue, claim detail, bills, diary and dashboard are fully functional under Playwright, and the copilot pane keeps its Diary tab, transcript, thread switcher and `requires_llm: false` action live while exactly the free-text composer and the six LLM-backed buttons are disabled with an inline warn notice and a manual retry control.
- Given a run that exceeds `copilot_run_timeout_seconds` or whose completion stops on `num_predict`, when it terminates, then it ends with exactly one terminal `error` frame carrying `code: "ai_limit"`, whatever streamed before the stop remains in the transcript, and the thread is accepting with no `pending_approval` left set.
- Given the model server recovers, when the availability query next polls or the handler presses "Try again", then `available` flips to true, the disabled inputs re-enable, and the next run streams to `done` — with no automatic re-fire of the failed run in between.
- Given a write is pending approval and the model server is unreachable, when the handler approves, then the write executes and audits before any narration is attempted, and it is never skipped or rolled back because of the outage.

## Spec Change Log

## Review Triage Log

### 2026-08-21 — Review pass

- intent_gap: 0
- bad_spec: 0
- patch: 21: (high 4, medium 9, low 8)
- defer: 2: (high 0, medium 0, low 2)
- reject: 2
- addressed_findings:
  - `[high]` `[patch]` **The wrapper's `except Exception` reported every defect as an outage — the exact conflation this story exists to remove, inverted.** A parse error, a `ValidationError`, a `KeyError` in the harness or an exception raised by a callback all became `ChatUnavailable` → `/problems/ai-unavailable`, so a bug in the graph greyed out the composer and told the handler the model server had not answered. The spec's own `Always` rule says only a failure to reach or be answered by the model server is `ai_unavailable`; the Code Map's pointer at `agents/client.py:198-203` supplied the shape but not the narrowing, because that file classifies parse errors first and the streaming path has none to classify. Replaced with a transport allow-list (`httpx.TransportError`, `ollama.RequestError`/`ResponseError`, `OSError`); everything else propagates to `_terminal_for`'s catch-all as `copilot_run_failed`, unretried. Found by one reviewer.
  - `[high]` `[patch]` **`_agenerate` dropped `response_metadata`, so `ai_limit` was structurally undetectable on that entry point.** `done_reason` lives in `generation_info`, and the override never merged it the way `_agenerate_with_cache` does — a backstop that silently lost the feature the story added. It now merges before firing the callback. Both reviewers found it independently.
  - `[high]` `[patch]` **The truncation flag latched for the whole run, failing runs that had completed.** `_run_frames` set one boolean off any `messages` chunk in any namespace, so an intermediate tool-call turn hitting `num_predict` ended a run `ai_limit` even when the final visible answer was whole. `_stopped_on_length` became a three-valued `_stop_reason` and the flag is now assigned per completion, so a later clean turn clears it; interrupt-wins precedence is unchanged. Both reviewers found it independently.
  - `[high]` `[patch]` **"Try again" could not do what an acceptance criterion and three docstrings promised.** The manual retry re-issued the same request, which the server answered from the probe's own cache — inside that window it returned the identical stale answer. The probe gained a `force` path (forced callers still coalesced with each other), the endpoint a `force` parameter, and the panel a mutation that writes the fresh result into the poll's cache entry. Both reviewers found it independently.
  - `[medium]` `[patch]` `_agenerate` fired `on_llm_new_token` twice per chunk by passing `run_manager` into an `_astream` that already fires it — the one caller in the build to do so. Its docstring reasoned about what runs above the method when the problem was below it. Both reviewers found it independently.
  - `[medium]` `[patch]` The reactive outage latch could never clear: it compared against `dataUpdatedAt`, which only advances on a *successful* fetch, so a 401 or a network blip on the poll left the composer disabled indefinitely with a retry that could not help. Now compares against the later of `dataUpdatedAt` and `errorUpdatedAt`, and the same-millisecond strict comparison was relaxed. Both reviewers found it independently.
  - `[medium]` `[patch]` `ai_limit` had no coverage outside a hand-rolled fake — the model-stub emitted `done_reason: "stop"` unconditionally, so no integration or e2e test could reach the truncation path, and a vendor that stopped publishing `generation_info` would have silently reverted truncated answers to `done`. The stub gained a scripted truncation branch, the e2e spec asserts the path end to end, and a new test pins the vendor's metadata shape.
  - `[medium]` `[patch]` The retry doubled worst-case time-to-honest-failure against a hung server (~240 s) and the config comment only checked that it fit. A `model_validator` now refuses any knob combination whose retry budget reaches the run bound, and the comment states the real worst case. It caught an existing test asserting a configuration the story forbids.
  - `[medium]` `[patch]` The e2e `afterAll` restarted the container but left the api's probe cache holding `available: false`, safe only because `6-6` sorts last. `startModelStub()` now polls the availability endpoint until it reports true, and the outage-side helper delegates to the same function.
  - `[medium]` `[patch]` `modelStubHealth` misdiagnosed an array-shaped `compose ps --format json` as a 30-second timeout, blaming the container for a parser mismatch. It now handles both shapes, skips unparseable lines, and distinguishes "cannot read health" from a named unhealthy state.
  - `[medium]` `[patch]` Two assertions passed vacuously: a send button that is disabled whenever the draft is empty proved nothing about the outage, and an e2e count sampled while a run was still streaming. Replaced with a submit-and-assert-no-request check plus a positive control, and a fresh conversation whose turn count the test owns.
  - `[medium]` `[patch]` A terminal frame told the handler the copilot "could not reply" while a half-answer sat in the transcript. The detail now branches on whether anything was answered; code, type and status are unchanged.
  - `[medium]` `[patch]` Refresh Insights let its own 503 be the discovery mechanism during an outage. It now invalidates the availability query on that failure, so the control disables instead of failing.
  - `[low]` `[patch]` `STREAM_ERROR_CODES` was a second copy of the `StreamErrorCode` union in the same file — in a change whose central argument is that a vocabulary has one copy. One is now derived from the other, declared before first use.
  - `[low]` `[patch]` `_terminal_for`'s docstring claimed an `isinstance` ordering was load-bearing, then said the classes could not be confused, then asserted the ordering anyway. It now says once that they are disjoint.
  - `[low]` `[patch]` The four new knobs were absent from `deploy/.env.example` — the file a deployment actually reads did not mention the settings `config.py` argues at length that deployments should tune.
  - `[low]` `[patch]` A server that connected and yielded nothing behaved differently on the two entry points. Both now raise `ChatUnavailable`, and `copilot_empty_answer` keeps its distinct meaning.
  - `[low]` `[patch]` A quick-action key absent from the server's flag list stayed enabled during an outage; an unknown key is now treated as needing the model, while an absent list still reads as available.
  - `[low]` `[patch]` The builder split made a key with no node surface as a prompt-file error. `build_node` now resolves the builder first and names the missing node, with a separate message for a key registered in the wrong map.
  - `[low]` `[patch]` `ModelAvailabilityProbe` held two paths to one knob; the copy is gone.
  - `[low]` `[patch]` `_runtime_stub()` set `availability_probe=None  # type: ignore` on a non-optional field — a typed lie that would become an `AttributeError`. It now builds a real probe pointed at a closed port.

## Design Notes

**`code` is added, `type` and `status` are not disturbed.** Today the SPA discriminates on the relative `type` URI (`errors.ts::problemType`), and four `error` branches already have stable ones. AC 1 and AC 4 ask for a `code`, so `code` becomes an RFC 9457 extension member on `_problem_frame` — `problem_response` already merges extensions flat (`api/errors.py:127`) and `RESERVED_PROBLEM_MEMBERS` already guards the clash. Making it *required* on `_problem_frame` rather than optional is what turns the vocabulary into a closed set a `Literal` can enforce; every existing branch therefore gains a code in the same change. The one consequence worth stating: `ai_limit` covers two shapes with two statuses — 504 on the wall clock (the existing `/problems/copilot-run-timeout`, unchanged) and 503 on a length stop (a new `/problems/copilot-output-truncated`). That is deliberate. `status`/`type` continue to say *what kind of failure and which one*; `code` says *why the AI stopped*, and both overruns stopped for the same reason from a handler's point of view.

**One wrapper, because `create_agent` calls the model where no call site exists.** `_narrate` (`qas.py:374`) could be wrapped in place, but the free-chat node's model call happens inside the vendored harness. Wrapping the object `copilot_chat_model` returns is the only seam both inherit, which is why the wrapper — not a `try/except` at the two visible call sites — is the design. It also keeps `agents/client.py`'s translation ordering as the single house pattern rather than a second one. If the harness turns out to bypass a wrapper, that is a Block If rather than a licence to translate twice: free chat and QAS disagreeing about what an outage is would be exactly the dishonesty this story exists to remove.

**Two timeouts, two codes, and the wrapper must not confuse them.** `chat_request_timeout_seconds` bounds one hop and belongs to the model server — a hop that times out is a server that did not answer, so it translates to `ChatUnavailable` and reports `ai_unavailable`. `copilot_run_timeout_seconds` bounds the loop and belongs to the run — `asyncio.timeout` (`copilot.py:1777`) fires *outside* the graph deliberately, "because a timeout that fired inside a node would leave the terminal decision in two places", and it reaches `_terminal_for` as `TimeoutError`. The hazard is that the run timeout can fire while the task is suspended inside a model call, where an over-broad `except Exception` would relabel it `ai_unavailable` and move the terminal decision back into the node the existing design pushed it out of. Hence the re-raise rule: `CancelledError` passes through untranslated, and the classification stays where `_terminal_for` already is.

**Retry only before the first chunk.** A retry after tokens have been emitted would duplicate them in the transcript, and the copilot streams. So the bound is on *establishing* a completion, not on finishing one; a connection refused retries, a connection dropped at token 200 does not. `chat_request_timeout_seconds` (120 s) already bounds each hop and `copilot_run_timeout_seconds` (300 s) bounds the loop, so two attempts plus backoff cannot outlive the run bound — which is why the default is 2 and not larger.

**`_OutputTruncated` is raised, not yielded.** `_Interrupted` (`copilot.py:1419`) established the pattern: `_run_frames` contains no terminal event name at all, and a condition it discovers becomes an exception the producer's sink (`:1544-1548`) hands to `_terminal_for`. Raising after the astream loop means the decoded tokens have already been yielded as `messages` frames and stay visible, which is what AC 4 asks for — the run is a completed answer that was cut short, not a lost one. Detection reads the streamed chunk's `response_metadata` for the vendor's stop reason; if it is absent there, the wrapper records it, and if it is available in neither place the Block If applies.

**The probe is a UI signal and nothing else.** No run consults it. Two sources of truth about the model's reachability would eventually disagree, and the one that matters is the one the run actually experienced — which is why a `requires_llm: true` run attempts the call even when the panel already believes the model is down, and why the client marks unavailable *reactively* on an `ai_unavailable` frame rather than trusting the poll alone. The endpoint answers 200 with `available: false` during an outage for the same reason: a health endpoint that errors when the thing it reports on is unhealthy tells the caller nothing it can render.

**One endpoint carries two facts on purpose.** `quickActionMeta.ts:38-49` argues against mirroring server truth in the client, and `requires_llm` is server truth (`graph.py:159`). But a client that knew which buttons need the model and not whether it is up — or the reverse — could still not disable the right set. Shipping both on `GET /copilot/availability` means the panel's disabled set is derived from one server response, and adding a key later changes no client code. This is the first `refetchInterval` in the SPA and the comment must say so: `dashboard.ts:38` records "nothing in the app polls" as a deliberate stance, and the reason it is being broken here is that availability is the one piece of state that changes without any user action and whose staleness silently costs the handler a failed run.

**`/healthz` is not touched, and that is the decision.** The api container is healthy without its model — `app.py:304-317` already argues the analogous case for the checkpoint pool, and `compose.e2e.yaml` has no `restart:` policy, so a stub outage cannot bounce the api. Folding an Ollama check into `/healthz` would invert both: compose would treat a model outage as an api failure, and `tests/test_problem_json.py:103`'s pinned public-route set would have to grow a route that leaks a dependency's state to unauthenticated callers. The probe's docstring should say this so nobody helpfully wires it into the container healthcheck later.

**The false path is made honest by types, not by a promise.** `data_alignment` genuinely calls no model today, and `test_copilot_qas.py:464-478` asserts it. What is missing is anything preventing the next `_narrate(...)` from being added inside a `requires_llm: False` builder — `_BUILDERS`' `Callable[...]` erases the pairing, which `deferred-work.md` records from 6.4's review with this story named as the one that would feel it. Not handing the builder a model at all makes the mistake a mypy error at the point it is written, and the existing test becomes the belt to that braces.

**The insights refresh button is in scope; the insights read path is not.** Task 4's audit finds exactly two non-copilot server surfaces that touch the model: `POST /claims/{id}/insights/refresh` and `GET /claims/{id}/similar`. Both already answer 503 with a written problem document (`api/routers/claims.py:2581` names this story as the status line it would be built on). Neither needs changing. What changes is that the Refresh control stops discovering the outage by failing — the same "disable exactly the affected input" rule the copilot panel follows. The Insights **tab** needs nothing: it reads a cached endpoint that answers 200 with four `not_generated` cards, which is already the honest degraded state.

**Stopping a container from a spec is safe here and only here.** `workers: 1` and `fullyParallel: false` are asserted by the fixture itself (`test.ts:23-29`), so no sibling spec is running when the stub goes down. The risk is not concurrency but the tail: a spec that stops the stub and dies leaves every later file broken. Hence `afterAll` under `try/finally` and an explicit health re-wait, since `docker compose start` — unlike `up --wait` — returns immediately.

## Verification

**Commands:**
- `cd lineworker/server && uv run ruff check . ../deploy && uv run ruff format --check . ../deploy` -- expected: clean.
- `cd lineworker/server && uv run mypy .` -- expected: clean under strict; the builder split must typecheck without `Any` or `cast` at the `build_node` call site.
- `cd lineworker/server && MIGRATION_TEST_DATABASE_URL=postgresql://lineworker:test@localhost:55432/lineworker uv run pytest` -- expected: all pass.
- `cd lineworker/server && uv run alembic check` -- expected: "No new upgrade operations detected"; this story adds no schema.
- `cd lineworker/web && npm run generate:api && git diff --exit-code -- src/api/schema.d.ts` -- expected: no diff after committing the regenerated file.
- `cd lineworker/web && npm run lint && npm run typecheck && npm test` -- expected: clean.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml up -d --build --wait` -- expected: all services healthy including `model-stub`.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml config --format json > /tmp/e2e.json && python3 ../.github/scripts/check_model_ports.py /tmp/e2e.json` -- expected: pass; no new service and no published model port.
- `cd lineworker/e2e && npm run typecheck && npx playwright test --grep "@story:6-6\b|@story:6-5\b|@story:6-4\b|@story:6-3\b|@story:6-2\b"` -- expected: pass. 6-2 is included because the Refresh Insights button changes; 6-3/6-4/6-5 because every error frame gains a `code`.
- `cd lineworker && docker compose -f deploy/compose.e2e.yaml ps --format json` -- expected after the suite: `model-stub` running and healthy; a stopped stub means the spec's `finally` did not fire.
- `cd lineworker/server && grep -rn "yield _sse" api/routers/copilot.py` -- expected: unchanged count; the terminal yield stays singular.
- `cd lineworker/server && grep -rniE "anthropic|openai|api\.claude|fallback" agents/ api/ services/` -- expected: no cloud host and no fallback code path (AD-5).

**Manual checks (if no CLI):**
- With `model-stub` stopped: the copilot pane still shows its header, claim context, transcript and thread switcher; the composer and six buttons are visibly disabled with the warn notice; the ⚡ data-alignment button still returns its note.
- A free-chat attempt during the outage leaves a typed error state in the transcript and no assistant-styled bubble containing pre-written claim text.
- Restart the stub, press "Try again": the inputs re-enable and the next message streams normally, with no automatic replay of the failed message.
- Browser devtools during a five-minute outage: the availability request repeats on its interval and nothing else re-fires — no retry storm from either the run or the query.

## Auto Run Result

Status: implemented, uncommitted (left in the working tree).

### Neither `Block If` triggered, and both were checked empirically

**The vendor does surface a length-capped completion.** `langchain_ollama` 1.1.0 copies the final
NDJSON object — `done_reason` included — onto the last chunk's `generation_info`
(`chat_models.py:1329-1334`), and `langchain_core` 1.5.6 merges that into
`AIMessageChunk.response_metadata` *before* firing the callback LangGraph's `messages` stream mode
listens on (`chat_models.py:786/923/2126`). `deploy/model-stub/app.py:517` emits the same field. So
`api/routers/copilot.py::_stopped_on_length` reads the vendor's own stop reason at the one place
this process sees every model call, and no token counter was invented. Asserted end to end by
`test_a_length_stopped_completion_is_ai_limit_and_keeps_its_tokens`.

**One seam was enough.** `create_agent` takes the model through `isinstance(model, BaseChatModel)`,
`model.profile` and `model.bind_tools(...)`, all of which a `ChatOllama` subclass satisfies, and
`bind_tools`' `RunnableBinding` delegates back to the same object's `_astream`. So `_narrate` and
the harness's internal call inherit one translation, proved by
`test_an_unreachable_model_ends_free_chat_with_one_ai_unavailable_frame` (the harness path) and
`test_an_unreachable_model_ends_a_narrating_quick_action_the_same_way` (the QAS path) reporting the
identical `code` and `type`. `/healthz` is untouched.

### Verification

| Check | Result |
|---|---|
| `ruff check` + `ruff format --check` over `. ../deploy` | clean, 280 files |
| `mypy` strict | clean, 266 files; no `Any`/`cast` at the `build_node` call site |
| `pytest` against live Postgres | **2739 passed, 1 skipped** |
| `alembic check` | "No new upgrade operations detected" — this story adds no schema |
| `check_model_ports.py` on the e2e profile | pass; no new service, no published model port |
| `generate:api` | idempotent (identical sha over two runs); `schema.d.ts` regenerated |
| `npm run lint` / `typecheck` / `test` | clean; **665 vitest tests passing** |
| `e2e npm run typecheck` | clean |
| Playwright, full suite | **217 passed**, including 9 for `@story:6-6` |
| `model-stub` after the suite | running and healthy — the `afterAll` restore fired |
| `grep -c "yield _sse" api/routers/copilot.py` | unchanged; the terminal `yield` is still singular |

### Deviations from this spec's letter, all deliberate

1. **The wrapper is a `ChatOllama` subclass rather than a delegating `BaseChatModel`.** The spec
   says "a `BaseChatModel` wrapper over the configured `ChatOllama`"; a subclass is one, and a
   delegate would have had to re-publish `bind_tools`, `with_structured_output` and `profile`
   correctly for ever — the exact surface `create_agent` introspects. Argued in
   `agents/degradation.py`'s docstring.
2. **`_agenerate` is re-implemented over `_astream`** rather than wrapped. `ChatOllama._agenerate`
   hands back only a final result, so a wrapper around it could not distinguish "refused the
   connection" from "stopped after two hundred tokens" — and the retry rule is precisely that
   distinction. It is unreachable inside the graph (`_should_stream` picks `_astream` whenever a
   streaming callback handler is attached) and exists as the backstop for every other context.
3. **The probe's settings ride `CopilotRuntime` as a `ModelAvailabilityProbe` object**, not as two
   loose fields. The spec asked for the settings on the runtime; the object carries them *and* the
   cache, and a probe constructed per request would be a cache with nothing in it.
4. **`TRUNCATED_STREAM` carries `code: "copilot_run_failed"`.** The client-synthesised frame needed
   a code from the same closed union; a dropped connection is the catch-all's own case ("the copilot
   could not finish answering"). Deliberately not `ai_unavailable` — a closed laptop lid must not
   disable the composer over a model server that had no part in it.
5. **`e2e/stories/6-6-*.spec.ts` polls `GET /copilot/availability` before asserting on a disabled
   control.** The api caches its probe for `ai_health_probe_cache_seconds`, so running immediately
   after another copilot spec left the cache holding `available: true`. Found by running the suite
   in order — the file passed alone and failed after 6-5. The wait is a poll on the shipped endpoint
   rather than a sleep.
6. **Two extra tests beyond the task list.** `test_a_model_free_builder_is_never_handed_a_model`
   asserts the builder split as a *signature* (the structural claim the spec asks for, made
   checkable), and the e2e "an approved write executes and is filed with the model down" closes
   AC 6, which the task list did not name a test for.
7. **`tests/test_ai_insights.py::test_exactly_the_declared_modules_read_the_ollama_base_url` gained
   a fifth reader.** `agents/degradation.py` reads `ollama_base_url` for the probe; that test's own
   docstring says a fifth reader "should be a diff somebody argues for here", and the argument is
   written there.
8. **`deferred-work.md`'s `_BUILDERS` erasure item is marked resolved** by this story, which it had
   named as its owner.

### Residual, stated rather than hidden

The availability probe's ten-second cache means the panel can render degraded for up to that long
after a recovery, and available for up to that long after an outage. It is deliberate (`config.py`
argues the arithmetic), it is why the panel marks unavailable *reactively* on an `ai_unavailable`
frame, and it is why the "Try again" control exists — a control the review pass had to make real,
because it was answered from that same cache and returned the stale answer it was meant to shorten.

*(Superseded by the review pass: this paragraph originally rested the e2e restore on "no spec file
runs after `6-6-*` today". `startModelStub()` now polls the availability endpoint until it reports
true, so a later spec inherits a recovered stack rather than the alphabet.)*

### Review pass (2026-08-21)

Two reviewers ran independently on the full diff. Twenty-five findings after deduplication: **21
patched, 2 deferred, 2 rejected**, with no intent gaps and no spec defects — so the code was never
reverted and the spec outside this section is unamended.

Four patches were high, and all four sat on the paths this story exists to make honest:

- The wrapper's `except Exception` reported **any** defect — a parse error, a `KeyError` in the
  harness, an exception from a callback — as `ai_unavailable`, so a bug in the graph greyed out the
  composer and told the handler the model server had not answered. That is the conflation the
  module's own opening paragraph says it exists to remove, running the other way. Now a transport
  allow-list; everything else reaches `_terminal_for` as `copilot_run_failed`.
- `_agenerate` never merged `generation_info` into the message metadata, so `_stopped_on_length`
  could not fire on that entry point at all — a backstop that had quietly lost the feature.
- The truncation flag latched for the whole run, so an intermediate tool-call turn hitting
  `num_predict` failed a run whose visible answer was complete. It is now assigned per completion.
- "Try again" was answered from the probe's cache, returning the stale answer three docstrings
  claimed it would shorten to zero. The probe, the endpoint and the panel all gained a forced path.

Six findings were reported by both reviewers independently. That is the signal worth carrying
forward: the model wrapper and the frontend's availability latch are dense, not that the reviewers
were unusually thorough. Two items were deferred to `deferred-work.md` — the probe's per-call HTTP
client (the same shape 6.1 already deferred for the embedding client, now wanting one decision
across all three readers of `ollama_base_url`) and the fact that the Insights tab now also starts
the SPA's only polling query.

Re-verified on the final tree after every fix: ruff and ruff-format over `server/` and `deploy/`,
mypy strict, **2757** pytest against a live Postgres, `alembic check` clean, `schema.d.ts`
regenerated and idempotent, web lint/typecheck and **669** vitest, e2e typecheck, the composed e2e
stack healthy, the model-port gate, and the **full** Playwright suite at **218 passed** (10 of them
`@story:6-6`) with `model-stub` left running and healthy.
