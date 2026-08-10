# Adversarial Review — Agent Harness v1.4 (gate-remediation amendment)

- **Target:** `ARCHITECTURE-SPINE.md` (LINEWORKER), agent harness as amended **v1.4** — AD-6, AD-13, AD-14, AD-7, and their touchpoints (AD-4 Write-concurrency convention, AD-11 PHI, the Copilot-stream convention, the Testing & CI convention).
- **Under re-attack:** the v1.4 amendment that claims to close the five v1.3 HIGH findings (CF-1..CF-5) plus the MED items (CF-6..CF-12): the *single gated write step* "core invariant," the write-scoped `ToolCallLimitMiddleware` one-write-per-turn cap, the tool-call-id-keyed *approval marker*, the `edit`-revises-non-identity-args constraint, and the AI-insight-refresh carve-out.
- **Method:** Two-unit divergence attack. Each finding constructs two builds one level down that obey every AD **to the letter** yet integrate incompatibly (clashing shared-data shape, two owners of one control path, wire mismatch). Judged against what the spine **says now**, not what a sensible engineer would infer. The attack targets (a) whether each prior HIGH is actually closed and (b) NEW divergences the v1.4 edits introduced.
- **Date:** 2026-08-10
- **Verdict:** **FAIL for parallel build — but a much narrower failure than v1.3.** All five prior HIGH are **CLOSED at the level they were raised**: the no-unapproved-write invariant is intact, no ungated direct-command path survives, `pending_approval` is singular, `edit` cannot change the target entity, the double-gate signal is named, and the refresh is de-classified as a write tool. However, the v1.4 edits introduced **two NEW HIGH** conforming-but-incompatible seams — one a direct **AD-13 ↔ AD-6/AD-7 contradiction on where the caller context lives** (which reintroduces the *closed* F7 stale-scope bug in one reading), and one a **residual of CF-1**: the QAS→gated-step *handoff mechanism* is still unspecified and forks the interrupt wire shape. Plus five MED and two LOW residuals. The two HIGH are each independently sufficient to make two teams build incompatibly.

## The two units

- **Unit A — Copilot backend team.** Owns `server/agents/` (the QAS supervisor-router StateGraph, the `create_agent` core, `agents/state.py`, `agents/tools/` registry, `HumanInTheLoopMiddleware` + `ToolCallLimitMiddleware` wiring, the approval-marker channel, prompts, AsyncPostgresSaver), the AD-4 approve-execution path, and the copilot SSE endpoint in `server/api/`.
- **Unit B — Copilot frontend team.** Owns `web/features/copilot` on the assistant-ui LangGraph runtime (`@assistant-ui/react-langgraph` 0.14.x, AD-9), consuming the SSE stream and driving the resume path.
- Where a finding is intra-backend, the parties are **Engineer A1** and **Engineer A2**, both inside Unit A.

---

## Verdict on the five prior HIGH

| Prior HIGH | v1.4 mechanism | Verdict | Residual |
| --- | --- | --- | --- |
| **CF-1 / F6** — ungated router-node (RTW) write | "Single gated write step" invariant; QAS nodes emit a *proposal* into the same step, "never a direct command call"; gate declared node-agnostic | **CLOSED** (no ungated/direct-command path survives) | **NEW-F2 (HIGH)** — the *handoff mechanism* is unspecified; forks the interrupt wire shape |
| **CF-2 / F2** — multi-write batch vs single-flight | Write-scoped `ToolCallLimitMiddleware` caps one write/turn → `pending_approval` singular | **CLOSED** (no two-pending-writes path) | **NEW-F5 (MED)** — per-turn vs per-run cap semantics fork the second-write sequencing |
| **CF-3 / F1** — `edit` defeats version-CAS / crosses entities | `edit` revises "non-identity arguments only, never target entity or expected_version"; CAS on drafted versions | **CLOSED** (single-entity target frozen) | **NEW-F3 (MED)** — "non-identity arguments" undefined; collection args expand the entity set past drafted versions |
| **CF-4 / F4** — middleware↔registry double-gate handshake | Middleware sets an approval **marker** keyed by tool-call id; registry raise = defence-in-depth, "not a second approval" | **CLOSED** (signal named; deadlock removed) | **NEW-F6 (MED)** — marker storage/lifecycle + "middleware records a marker" asserts non-native behavior |
| **CF-5 / F3** — refresh is a write, is exempt, registry forbids | Refresh "not a `kind: write` tool — an agent→`services/rag` command," gate- and registry-exempt, audited | **CLOSED** (de-classified as a write tool) | **NEW-F4 (MED)** — collides with AD-13 "every agent→service call goes through a registry tool with kind read\|write" |

**All five prior HIGH are CLOSED.** The v1.4 amendment is architecturally sound on the invariant it was defending. The failure below is entirely from **new seams the edits opened**, not from the old holes reappearing.

---

## Findings

### NEW-F1 — HIGH — `caller` lives in `context_schema` (AD-6/AD-7) but the registry injects it "from graph state" (AD-13): the CF-9 fix was not propagated, and one reading reintroduces the closed F7 stale-scope bug

The CF-9 edit moved the caller context out of the checkpointed state channel and onto the runtime `context_schema`, so a resumed thread can never replay yesterday's book of business. AD-6: *"The AD-7 caller context is **not** a state channel: it rides the `create_agent` `context_schema`, re-resolved every run and resume, never checkpointed."* AD-7: *"agents receive it as a per-run value on the `create_agent` `context_schema` (**never a checkpointed state channel**)."* **But AD-13 was not updated to match** — it still says: *"The AD-7 caller context is injected by the registry **from graph state** — never a tool parameter the model can populate."*

In LangChain `create_agent`, `state` (checkpointed graph state) and `context` (runtime, via `context_schema`) are **distinct objects**. "Graph state" is a term of art for the checkpointed channels. AD-13 and AD-6/AD-7 now name two different sources for the single most security-critical value in the system — the scope the repository layer enforces on every data path (AD-7).

- **Engineer A1 (AD-13 literal — registry reads `state`):** the tool-wrapping registry injects `caller` from `state["caller"]`. To make that available, A1 must place `caller` in the closed `state_schema` (`agents/state.py`) — where it becomes **checkpointed and replayed on resume**, exactly the materialized-scope-that-replays-yesterday's-book bug F7 was raised to kill and CF-9 was edited to fix. A1 conforms to AD-13 verbatim and silently un-fixes F7.
- **Engineer A2 (AD-6/AD-7 literal — registry reads `context`):** the registry injects `caller` from `runtime.context.caller`, re-resolved each run/resume, never in `state`. A2 conforms to AD-6/AD-7 verbatim and directly contradicts AD-13's "from graph state."

The two registries read the caller from different places; A1's is checkpointed (stale-on-resume), A2's is runtime (fresh). Because *every* tool's scope enforcement flows through this injection, the divergence is not local — it decides whether the whole agent surface honors the F7 fail-safe. This is a clean textual contradiction between two ADs, not an inference gap.

**Fix:** In AD-13 replace *"injected by the registry from graph state"* with *"injected by the registry from the per-run `context_schema` runtime context (AD-7) — never a checkpointed `state` channel and never a tool parameter."* Add a one-line cross-check to the F7/CF-9 note so the three ADs (AD-6, AD-7, AD-13) name the identical source.

### NEW-F2 — HIGH — CF-1 residual: the QAS→gated-step *handoff mechanism* is unspecified; "the same step" reads as re-enter-the-core (middleware-native interrupt) or a shared executor node (custom interrupt), forking the wire shape Unit B consumes

CF-1 correctly closed the *ungated* hole: AD-6/AD-13/AD-14 now forbid the RTW node from calling a command directly ("never a direct command call") and restate the invariant node-agnostically. But **how** a deterministic QAS router node — routed, per AD-14, *before any LLM call* and structurally *outside* the `create_agent` core — gets its drafted write executed by a gate the spine also binds *to* the core is still not specified. The text oscillates: AD-6 calls the one place *"the gated write-tool step on the copilot graph"* (a graph step both paths feed) **and** *"Free-chat tool calls hit it via `HumanInTheLoopMiddleware` on the `create_agent` core"* (the gate is the core's middleware). QAS nodes *"emit it as a proposal into the same step."* The proposal-emission mechanism is named nowhere.

- **Engineer A1 (re-enter-the-core reading):** the RTW node does tool-merge → LLM prose, then injects a synthetic assistant message carrying a `save_rtw_letter` tool call into `messages` and routes control **into the `create_agent` core** so the core's `HumanInTheLoopMiddleware` `interrupt_on` intercepts it. The interrupt is the **middleware-native** `HumanInterrupt`/action-request payload — identical to a free-chat write. Wire-compatible with the free-chat path.
- **Engineer A2 (shared-executor-node reading):** "the gated write-tool step **on the copilot graph**" is a distinct StateGraph node that both the core (via `pending_approval`) and the QAS node feed. The RTW node writes its proposal into `pending_approval` and routes to the shared node, which calls **its own `interrupt()`** and executes. That interrupt payload is **hand-shaped by A2**, not the middleware's native structure.

Both cite AD-6/AD-13/AD-14 verbatim. A1's RTW interrupt and A2's RTW interrupt have **different payload shapes**, and Unit B's assistant-ui runtime — which "surfaces the raw `HumanInTheLoopMiddleware` interrupt payload" (Copilot-stream convention) — can render/resume only one of them. The frontend built against the middleware-native shape hangs on A2's custom interrupt, and vice-versa. Worse, **the AD-15 gate does not catch it**: AD-15 tests "the `HumanInTheLoopMiddleware` approve/edit/reject round-trip against the stub's scripted write-proposal" — that is the *free-chat/middleware* path. A2's shared-node RTW interrupt never passes through `HumanInTheLoopMiddleware`, so the round-trip test is green while the RTW letter — the single most visible write feature — mismatches in the browser.

**Fix (tighten AD-6 + AD-14):** pin the handoff. State that a QAS write node reaches the gate by **routing its drafted tool call back through the `create_agent` core's gated tool step** (the proposal is materialized as a pending write-tool call the core's `HumanInTheLoopMiddleware` intercepts), so **every** write — free-chat or QAS — raises the *identical* middleware-native interrupt and resume contract. Explicitly forbid a second, node-local `interrupt()` for writes. Extend the AD-15 round-trip test to cover a QAS-originated (RTW) write-proposal, not only a free-chat one.

### NEW-F3 — MED — CF-3 residual: `edit` may revise "non-identity arguments," but that term is undefined; a collection-typed argument expands the target-entity set to entities whose version was never drafted

AD-6 now freezes the target: *"the human may revise only the write tool's **non-identity arguments** — never its target entity or the `expected_version`(s) it was drafted against,"* CAS on the drafted versions. This closes the single-entity retarget. But **"non-identity argument" is undefined**, and several write tools plausibly take a *collection* that selects rows: e.g. `approve_payment(claim_id, week_ids=[…])` over multiple `payment_schedule_week` entities (AD-12: worklist approval → financials command; the batch pays `payment_scheduled` rows). The gated call records *"the `version` of every entity it was drafted against."*

- **Engineer A1 (identity = the top-level claim only):** `week_ids` is a non-identity argument → editable. A human `edit` changes `week_ids` from `[W1,W2]` to `[W1,W2,W3]`. The drafted-version map has entries for W1,W2 only; W3 has **no recorded version**. A1 either CASes W3 against a missing version (an unguarded read-modify-write — AD-4 flatly forbids it) or fabricates one. The edit has effectively crossed into an entity the approval was never drafted against — the CF-3 hole, re-entered through a collection field.
- **Engineer A2 (identity = every argument that names a target row):** `week_ids`, `note_id`, `injury_id`, etc. are all identity → frozen; only scalar value fields (amount, date, prose) are editable. Safe — but now A2's `edit` form exposes a *strictly smaller* editable field set than A1's, and Unit B's edit card, built to one classification, submits args the other backend rejects as a protocol error.

Both satisfy "revise only non-identity arguments." They disagree on what that set is, and A1's reading defeats the recorded-version CAS for multi-entity writes.

**Fix:** define "non-identity argument" explicitly as *any argument that is not an entity identifier and does not alter the set of entities the call targets*; a collection that selects target rows **is** identity and is frozen at interrupt time. An `edit` whose revised args would add or drop a target entity (hence a version not in the drafted set) is a protocol error → re-draft, never execute. (This also forces a decision the Write-concurrency convention leaves open: whether `version` lives on the claim aggregate root or per child row — pin it, since the drafted-version set depends on it.)

### NEW-F4 — MED — CF-5 residual: the refresh is "an agent→`services/rag` command, not a tool," but AD-13 mandates that *every* service invocation from `agents/` go through a registered read|write tool — so is it in the registry or not?

CF-5's contradiction (a write that is interrupt-exempt yet the registry forbids unapproved writes) is resolved by de-classifying: AD-6/AD-13 now say the AI-insight refresh is *"**not** a `kind: write` tool — it is an agent→`services/rag` command … gate- and registry-raise-exempt, but audited."* But AD-13 also says, in the same rule: *"AD-13 binds **every service invocation from `agents/`** … nodes call registry entries, **never services**"* and *"All tools live in one registry … each with a declared `kind: read | write`."* The refresh mutates `ai_insight`, so it is neither a read nor a gated write — yet it is a service invocation from `agents/`.

- **Engineer A1 (honor "goes through the registry"):** registers the refresh as a tool. The only non-gated kind is `read`, so A1 labels the mutating refresh `kind: read` — an honest-kind violation AD-13 forbids, and it lands in the read-tool set the model may freely call (the model can now trigger insight refreshes at will).
- **Engineer A2 (honor "not a tool, an agent→services command"):** calls `services/rag` **directly** from the node, outside the registry — violating "nodes call registry entries, never services," and **bypassing the registry's caller-context injection** (AD-13). A2 must re-inject the AD-7 caller context by hand; if omitted, the repository read behind the refresh runs without a scope context (fail-closed, but a different failure than the registry path) — and the injection source is now the very thing NEW-F1 is ambiguous about.

Both readings gate-exempt the refresh correctly (CF-5 intent preserved), but they disagree on registry membership and on how the caller context reaches the refresh.

**Fix:** adopt the F3-v13 remedy the amendment declined — a third classification. Make refresh a registry entry with an explicit `kind` such as `write:autonomous` (or `command:autonomous`): in the registry (so caller-context injection and the single-service-call rule still bind), **not** in `interrupt_on`, and admitted by the registry raise via an allowlist keyed to `services/rag` — never a per-tool `read` mislabel and never a registry bypass. State that adding such an entry is an AD-level change.

### NEW-F5 — MED — CF-2 residual: "one write-tool call per turn" is attributed to `ToolCallLimitMiddleware`, which is natively per-**run**/thread — so "a second write sequences as a second interrupt" reads two ways

AD-6: *"The model is capped at **at most one write-tool call per turn** (a write-scoped `ToolCallLimitMiddleware`) … a second required claim-write sequences as a second interrupt after the first resolves."* The stated *behavior* is per-turn (one write per model turn, but a later turn may propose the next). The named *mechanism*, `ToolCallLimitMiddleware`, counts tool calls with **run-/thread-scoped** limits, not "one per model turn."

- **Engineer A1 (per-turn behavior literal):** builds a custom per-turn write cap (or disables parallel write-tool selection) that allows exactly one write per model turn and a fresh write in the *next* turn. A "approve this payment, then update the reserve" run produces **two sequential interrupts in one run** — matching AD-6's sentence.
- **Engineer A2 (`ToolCallLimitMiddleware` literal):** configures the actual middleware with a write-scoped `run_limit = 1`. That caps writes to **one per run**. The model's second write hits the limit and the run **ends with a limit error** — there is no second interrupt; the user must send a new message. A2 satisfies "`pending_approval` stays singular" but violates "a second required claim-write sequences as a second interrupt after the first resolves."

Same ADs, two different stream-event sequences and UX for a two-write request. Unit B renders a second approval card (A1) or a limit-error + prompt-to-continue (A2). (No path to *two pending writes* exists — a run is QAS-xor-free-text, so the two-pending-writes hole itself is closed; this is a sequencing divergence.)

**Fix:** name the mechanism that produces per-turn (not per-run) behavior — e.g. "disable parallel write-tool calls on the core so each model turn surfaces at most one write; the run continues after each approved write, so a subsequent turn may propose the next write as a second interrupt" — and reserve `ToolCallLimitMiddleware` for its actual per-run bound (AD-14). The Testing convention already asks the graph test to assert "a two-write turn produces two sequential single-request interrupts"; make that assertion normative here.

### NEW-F6 — MED — CF-4 residual: the approval marker is named but its home channel, its clearing lifecycle, and the claim that "the middleware records" it are underspecified

CF-4 is closed at the level it was raised — the signal is now named ("an approval marker in graph state keyed by the pending tool-call id"), single-authority, and the registry raise is explicitly *"not a second approval … an approved write never deadlocks."* Deadlock and vacuity are gone. Three residual seams remain:

1. **Home channel.** AD-6's closed schema is `(messages, claim_id, pending_approval, …)` and *"new channels are added there via review, never node-locally."* The marker is "in graph state keyed by … tool-call id" but is not listed. **Engineer A1** makes it a field `pending_approval.approved_call_id` (singular — fine, one write/turn). **Engineer A2** reads "keyed by tool-call id" as a map/set and adds a top-level `approved_tool_calls` channel. The middleware writes and the registry reads must agree on the shape; they are in different modules and will diverge.
2. **Who records it.** AD-6/AD-13 say *"the middleware records an approval marker."* `HumanInTheLoopMiddleware` does not natively stamp a user-defined graph-state channel on approve — it resumes the intercepted tool call through its own path. So a custom bridge (a resume/`after_model` hook) must write the marker. The spine asserts a behavior the framework does not provide and does not assign the bridge; **A1** builds it, **A2** assumes the middleware already does it and ships a registry guard that never sees a marker.
3. **Clearing.** AD-6 spells out clearing `pending_approval` only on **reject**. On approve/edit after execution, marker-clearing is unspecified; a lingering marker for an executed tool-call id is harmless only because the AD-4 CAS makes re-execution a 409 — but A1 (clears on execute) and A2 (relies on CAS) have different replay behavior.

**Fix:** declare the marker as a named field of `pending_approval` (added to `agents/state.py` via review), written **only** by the approve/edit resume hook and cleared on terminal resolution of that tool-call id; state that the hook is the custom bridge that records it (the native middleware does not). One graph test asserts: approved write executes once; a tool-call id absent from the marker raises; the marker does not strand a sequential second interrupt.

### NEW-F7 — MED — CF-6 residual: the resume envelope is pinned to `{decision, args?}`, but the raw-HITL ↔ wire ↔ middleware-native three-way mapping ownership and vocabulary are not fully closed

CF-6 improved: the Copilot-stream convention now pins the resume payload to `command: {resume: {decision: approve|edit|reject, args?}}`. Two residuals straddle the Unit A / Unit B boundary:

- **Singular wire vs list-shaped native resume.** The wire carries **one** `{decision, args?}`; `HumanInTheLoopMiddleware`'s native resume is a **list** of responses (one per pending action). Since writes are capped at one/turn, singular ↔ single-element list is a clean 1:1 — but the mapping (backend: `{decision,args?}` → `Command(resume=[…])`) is not assigned. The convention assigns only the *frontend* mapping ("the copilot frontend owns the mapping to this shape"); the *backend* reverse mapping is unowned. **Unit A** may assume the frontend POSTs the native list; **Unit B** POSTs the singular object per the pinned shape — one side must convert, and neither is told to.
- **Vocabulary.** The spine asserts the middleware's `allowed_decisions` are literally `approve | edit | reject` (Stack row notes a 4th, `respond`, unused). If the installed `HumanInTheLoopMiddleware` uses different native literals (e.g. `accept`/`response`/`ignore`), the mapping must translate, and the spine treats the spine's own vocabulary as the native one without a named adapter.

**Fix:** add a Copilot-stream sub-row pinning the backend reverse mapping (one canonical adapter module owned by Unit A, imported by the generated client, converting `{decision, args?}` ↔ the middleware's native resume list and vocabulary), so both sides derive it from one source; keep the AD-15 round-trip test as the guard and assert the exact wire bytes end to end (not just the backend round-trip).

### NEW-F8 — LOW — CF-7 residual: scope-loss-on-resume "fails safe like a stale one," but the surfaced error and user message diverge from the version-409 path

AD-6/AD-7 close CF-7's silent success: a resume whose re-resolved scope no longer contains the drafted entity has *"the approval … discarded like a stale one."* Good. But the two fail-safes are not mechanically the same: a stale **version** returns a 409 and the graph *"tells the user the claim changed since drafting."* A **scope loss** makes the repository's unconditional employer filter exclude the row → the AD-4 command sees **not-found / 403**, not a 409, and telling that user "the claim changed since drafting" is misleading (nothing changed; access was revoked). **A1** surfaces a 403/not-found with an access message; **A2** funnels it through the 409 "claim changed" copy. Divergent error codes and user-facing text for the same case.

**Fix:** enumerate both fail-safe causes distinctly in AD-6 — stale version (409, "claim changed since drafting") and revoked scope (403/not-found, "you no longer have access to this claim") — both discard the proposal, neither force-writes.

### NEW-F9 — LOW — CF-8 residual: `record_copilot_approval` is "AD-4-shaped," but a reject is a non-mutation with no `before/after`

AD-6 now routes all three decisions through `record_copilot_approval`, which *"emits an AD-4-shaped audit event."* AD-4's fixed schema requires `before`/`after` JSONB diffs. A reject/approve-of-intent is a non-mutation with "no content." **A1** writes `before/after = null`; **A2** writes a content-free redaction marker. Both "AD-4-shaped," minor drift; audit consumers filtering by diffs must special-case whichever ships.

**Fix:** pin `before/after` on the copilot-approval event to a content-free marker (consistent with the AD-11 "no content" redaction convention), `action: copilot_{approved|edited|rejected}`, `entity/entity_id` = the proposed target — one shape, no separate table.

---

## Summary table

| # | Severity | Prior link | One-line |
| --- | --- | --- | --- |
| NEW-F1 | **HIGH** | CF-9 edit | `caller` is `context_schema` (AD-6/AD-7) but "injected from graph state" (AD-13) — contradiction; A1's reading re-checkpoints caller and reopens F7 stale-scope |
| NEW-F2 | **HIGH** | CF-1 residual | QAS→gated-step handoff mechanism unspecified — re-enter-core (native interrupt) vs shared node (custom interrupt) fork the wire Unit B consumes; AD-15 tests only the middleware path |
| NEW-F3 | MED | CF-3 residual | "non-identity argument" undefined; a collection arg expands the target set to entities with no drafted version → unguarded write or protocol-reject divergence |
| NEW-F4 | MED | CF-5 residual | refresh is "a command, not a tool," yet AD-13 requires every agent→service call to be a registry tool with kind read\|write — registry-membership + caller-injection fork |
| NEW-F5 | MED | CF-2 residual | "one write per turn" attributed to per-run `ToolCallLimitMiddleware` — second-write-as-second-interrupt vs run-limit-error diverge |
| NEW-F6 | MED | CF-4 residual | approval marker's home channel, clearing lifecycle, and "middleware records it" (non-native) underspecified |
| NEW-F7 | MED | CF-6 residual | resume wire pinned, but backend reverse-mapping ownership + native vocabulary not closed |
| NEW-F8 | LOW | CF-7 residual | scope-loss fail-safe surfaced as 409 "claim changed" vs 403/not-found — code + message diverge |
| NEW-F9 | LOW | CF-8 residual | `record_copilot_approval` `before/after` on a non-mutation reject: null vs marker |

## Explicit CLOSED/OPEN on the five prior HIGH

1. **Ungated router-node write (CF-1/F6):** **CLOSED.** No direct-command or ungated write path survives; the invariant is restated node-agnostically ("never a direct command call"). Residual **NEW-F2 (HIGH)** — the *handoff mechanism* into the gated step is still forkable and escapes the AD-15 test.
2. **Multi-write batching vs single-flight (CF-2/F2):** **CLOSED.** The write-scoped cap keeps `pending_approval` singular; a run is QAS-xor-free-text, so no path yields two pending writes (including a QAS write co-occurring with a free-chat write). Residual **NEW-F5 (MED)** on the cap's per-turn-vs-per-run mechanism.
3. **`edit` defeating version-CAS (CF-3/F1):** **CLOSED** for the single-entity target (entity and `expected_version` frozen). Residual **NEW-F3 (MED)** — collection-typed "non-identity" args can still cross into un-drafted-version entities.
4. **Middleware↔registry double-gate (CF-4/F4):** **CLOSED.** The marker is named, keyed by tool-call id, single-authority; the registry raise is explicitly defence-in-depth, not a second approval — deadlock and vacuity removed. Residual **NEW-F6 (MED)** on marker home/lifecycle/who-writes-it.
5. **AI-insight refresh contradiction (CF-5/F3):** **CLOSED.** The refresh is de-classified as a `kind: write` tool and made gate- and registry-exempt-but-audited. Residual **NEW-F4 (MED)** — the "command, not a tool" framing collides with AD-13's "every agent→service call is a registry tool."

## MED-item check (CF-6..CF-12)

- **Resume wire shape `command:{resume:{decision,args?}}`** — pinned; **partially closed**, see NEW-F7 (backend reverse-mapping ownership + vocabulary).
- **Scope-narrowed-on-resume fail-safe** — present and correct in AD-6/AD-7; **closed**, minor NEW-F8 (LOW) on surfaced error/message.
- **`record_copilot_approval` AD-4-shaped** — now "emits an AD-4-shaped audit event," all three decisions; **closed**, minor NEW-F9 (LOW) on `before/after`.
- **`caller` context-only (CF-9)** — the edit landed in AD-6/AD-7 but **not** AD-13 → **NEW-F1 (HIGH)**, the headline new failure.
- **`edit` in all-three-decision audit (CF-10)** — AD-6 now says "All three decisions (approve/edit/reject) record …" — **closed.**
- **`langchain >=1.3,<1.4` pin + `respond` note (CF-11)** — present in Stack row — **closed.**
- **"F1 gate" jargon (CF-12)** — the "F1 gate" phrase is gone; AD-6 now reads "the guarantee is enforced by the per-tool middleware, not by hiding write tools" — **closed.**

## Bottom line

The v1.4 amendment **succeeds at what it set out to do**: the single-gated-write invariant is intact and node-agnostic, `pending_approval` is singular, `edit` cannot retarget the entity, the double-gate signal is named, and the refresh is de-classified — all five prior HIGH are CLOSED, and the closed MED/LOW items (CF-9-in-AD-6/7, CF-10, CF-11, CF-12) stayed closed. The remaining risk is a **different, smaller class**: one AD-to-AD contradiction the CF-9 edit left behind (**NEW-F1**, caller `state` vs `context`, which quietly reopens F7 in one reading) and one unspecified mechanism the "single step" abstraction papers over (**NEW-F2**, the QAS→gate handoff, which forks the interrupt wire and evades the AD-15 test). Both are HIGH and integration-fatal for two independent teams, so the gate remains **FAIL for parallel build** — but both are closable with local wording fixes (propagate the `context_schema` source to AD-13; pin the QAS-write handoff to a single mechanism and extend the round-trip test), not another structural move. The five MED residuals are the framework-behavior-vs-spine-prose seams that always accompany binding an invariant to a specific middleware; each is closable in the same pass.
