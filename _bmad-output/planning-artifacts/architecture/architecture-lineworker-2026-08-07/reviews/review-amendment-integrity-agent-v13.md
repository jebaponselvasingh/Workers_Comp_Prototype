# Amendment-Integrity / Regression Review — Agent Runtime v1.3

- **Gate:** BMad architecture "Validate" — amendment-integrity / regression pass.
- **Question:** Did the v1.3 agent-runtime amendment silently reopen any finding that the prior adversarial gate (`review-adversarial-agent-update.md`, F1–F13) already closed in v1.1/v1.2?
- **Targets:** `ARCHITECTURE-SPINE.md` (AD-6, AD-13, AD-14 and their touchpoints: AD-3, AD-7, AD-11, AD-12, Copilot stream convention, Stack, Capability Map).
- **What v1.3 changed:** The write-approval gate was reimplemented. v1.2 fixed F1 by *hiding* write tools from every LLM tool-selection node (model emits a `pending_approval` payload; a separate approval-execution step calls the write tool; the registry raises without an approval token). v1.3 *replaces* this: write tools are now **registered on a `langchain.agents.create_agent` core** and gated by **`HumanInTheLoopMiddleware`** (`interrupt_on`; `approve | edit | reject`). The model MAY select a write tool, but the middleware pauses before execution; the registry-raise is kept only as defence-in-depth. The core is embedded as the free-chat node inside the deterministic supervisor-router.
- **Date:** 2026-08-10
- **Verdict:** **PASS with two WEAKENED findings.** The mechanism flip from "hide writes" to "expose-but-gate" preserves the core F1 no-unapproved-write invariant (middleware interrupt + registry defence-in-depth backstop). No finding is REOPENED. But two closed findings are softened by the new mechanism: **F5** (a `create_agent` turn can emit multiple write tool calls → one multi-action interrupt, contradicting the singular `pending_approval` + "second write = second interrupt" rule) and **F8** (the RTW-letter QAS node is told to run "…→ the `HumanInTheLoopMiddleware` write-tool gate," but that middleware lives on the `create_agent` core and a QAS node is *not* the core — the gate mechanism for QAS write nodes is now inconsistent/under-specified).

---

## Regression matrix F1..F13

| # | Prior sev | Status | One-line justification |
| --- | --- | --- | --- |
| F1 | Critical | **STILL-CLOSED** | Rationale flipped to expose-but-gate, but the no-unapproved-write invariant holds: `HumanInTheLoopMiddleware` `interrupt_on` pauses before every write-tool execution on the core, and the registry still raises without an approval token (defence-in-depth). Fail mode is fail-safe (raise), not silent write. |
| F2 | Critical | **STILL-CLOSED** | Untouched by v1.3. `SVC --embeddings--> OLLAMA` arrow, two-client rule, and single `services/rag` refresh command are all present; AI-insight refresh still routes through the owning `services/rag` command. |
| F3 | Critical | **STILL-CLOSED** | Stream contract (`messages/updates/interrupt/error/done`, one terminal event, inline problem+json, resume = POST `command:{resume:…}` → new stream) intact; the resume payload now simply carries the HITL approve/edit/reject decision. |
| F4 | High | **STILL-CLOSED** | Thread key `(scope, user_id, conversation_seq)`, optional `claim_id`, server-minted `thread_id`, and "new conversation" all preserved; `dashboard` scope reserved-but-deferred. Untouched by v1.3. |
| F5 | High | **WEAKENED** | Single-flight-per-thread (409) survives, but a `create_agent` model turn can emit ≥2 write tool calls → one HITL interrupt with multiple actions; the singular `pending_approval` schema and "a second claim-write sequences as a second interrupt after the first resolves" no longer follow from the mechanism. |
| F6 | High | **STILL-CLOSED** | AD-3 checkpoint-table exception (vendored Alembic DDL, saver-only writes, no `setup()` in prod), 90-day retention, `thread_id`-prefix purge, and the Capability-Map row all intact. `create_agent` still checkpoints via AsyncPostgresSaver. |
| F7 | High | **STILL-CLOSED** | `caller` re-resolved on every run start **and resume**; resume rejects any actor but the thread's `user_id` (403). v1.3 carries caller on the `create_agent` `context_schema` (runtime, re-supplied per invoke) — aligns with the "reference not materialized scope" fix. |
| F8 | High | **WEAKENED** | `requires_llm`, degradation scope, RTW-is-a-QAS-key, and "disable affected inputs not the pane" all survive — but the RTW QAS node is told to end in "…→ the `HumanInTheLoopMiddleware` write-tool gate (AD-6)," and that middleware is bound to the `create_agent` core, which the QAS node is not. The gate mechanism for a QAS-node write is now ambiguous/inconsistent. |
| F9 | Medium | **STILL-CLOSED** | AD-13 still binds every service invocation from `agents/` (tool node or router node) through the registry; "RAG node" is topology, its retrieval is a registered read tool. Untouched. |
| F10 | Medium | **STILL-CLOSED** | `{ok, data, display}` envelope preserved verbatim: `data` = cents/ISO, `display` = service-side formatted strings quoted verbatim by the model. `create_agent` tools still return this envelope as tool content. |
| F11 | Medium | **STILL-CLOSED** | Reject branch still records the outcome (kind, claim, actor, timestamp, no content) via `services/audit record_copilot_approval`, "the only persistence permitted on the reject branch." (Minor note: the new `edit` decision is a third outcome the "both branches" wording doesn't enumerate — see notes.) |
| F12 | Low | **STILL-CLOSED** | Closed single-TypedDict schema preserved and made concrete: the `create_agent` `state_schema` (`AgentState` subclass) in `agents/state.py`; new channels added there via review. (Minor note: `caller` is listed both as a state channel and on `context_schema` — see notes.) |
| F13 | Low | **STILL-CLOSED** | Per-run `num_predict` + wall-clock timeout → `error` code `ai_limit` preserved, and strengthened with `Model/ToolCallLimitMiddleware` on the core; `ModelFallbackMiddleware` explicitly unused (AD-5). |

**Tally: 11 STILL-CLOSED · 2 WEAKENED (F5, F8) · 0 REOPENED.**

---

## Findings (WEAKENED)

### F5 — WEAKENED — `create_agent` turns can produce a multi-action interrupt; singular `pending_approval` no longer follows from the mechanism

**What v1.2 guaranteed structurally.** In v1.2 the model's only way to propose a write was to emit a single `pending_approval` payload — one payload, one write, structurally singular. "At most one `pending_approval` per thread; a second claim-write sequences as a second interrupt after the first resolves" fell out of the mechanism for free.

**What v1.3 changed.** The core is now a standard `create_agent` tool-calling loop and write tools are in the model's tool set. A single assistant turn can emit `tool_calls` of length > 1, including two write tools. `HumanInTheLoopMiddleware` surfaces the pending calls it intercepts as **one interrupt carrying multiple actions** — it does not serialize them into sequential interrupts. So:

- The state schema declares a **singular** `pending_approval` ("the middleware's pending write-tool call"), but the mechanism can present N pending write calls in one pause.
- AD-6's "a second claim-write in one run sequences as a second interrupt after the first resolves" is an *assertion the mechanism does not enforce*. Nothing in v1.3 constrains the model to at most one write tool call per turn.

**Divergence.** Engineer A (writes ⇒ core, HITL default) lets the middleware batch both write calls into one interrupt and CASes both on approve. Engineer B reads AD-6 literally (one `pending_approval`, sequential) and caps writes to one per turn. Same ADs, two incompatible pause/approve shapes on the first multi-write turn (e.g., "approve this payment and save the RTW letter"). The single-flight-*per-thread* 409 still holds; the single-write-*per-interrupt* invariant does not.

**Fix (AD-6 + AD-14 rule).** Reconcile the schema with the mechanism — pick one and state it:
- **(a) Constrain to one write per turn:** bound write tool calls to at most one per model turn (a write-scoped `ToolCallLimitMiddleware` and/or a prompt/middleware rule that the model may propose at most one write per turn). Then singular `pending_approval` and "second write = second interrupt" hold as written; OR
- **(b) Make `pending_approval` plural:** redefine it to hold the middleware's (possibly multi-action) interrupt request, CAS each action independently on approve, and delete the "sequences as a second interrupt" sentence.

Either closes the gap; leaving both the singular schema and the batch-capable mechanism in the spine is the ambiguity.

### F8 — WEAKENED — The RTW QAS node's write gate is the core-bound middleware, but a QAS node is not the core

**F8's four concerns survive.** `requires_llm: bool` per key, QAS-may-narrate-not-originate, RTW-**is**-a-QAS-key, and outage degradation scoped to affected inputs (never the pane) are all preserved in AD-14.

**What v1.3 softened.** v1.2's RTW node ran "tool-merge → LLM prose → **`interrupt()`**" — a plain LangGraph interrupt any node can call, cleanly implementable by a bespoke QAS node. v1.3 re-specifies it as "tool-merge → LLM prose → **the `HumanInTheLoopMiddleware` write-tool gate (AD-6)**." But AD-6 binds that middleware "**on the `create_agent` core**," and AD-6 defines the core as *only the free-text / LLM tool-calling node*. The RTW letter is a **QAS node in the deterministic supervisor-router**, i.e. **not the core**. So the RTW node is instructed to pass through a gate that structurally lives on a component it is not part of.

**Consequence (an F1-flavored divergence returns).** A QAS node has no defined way to fire the core's middleware, yet the registry defence-in-depth "raises without the middleware's approval in graph state." Two resolutions, both AD-compliant:
- Engineer A routes the RTW write **through the `create_agent` core's** middleware-gated tool step (one gate discipline). 
- Engineer B gives the RTW QAS node its **own `interrupt()`** and writes the approval token the registry checks (a second, parallel write-approval discipline).

That is exactly the "two inconsistent write disciplines" that F1 was raised to kill — reintroduced for the QAS write path. The *invariant* (no unapproved write) still holds (both disciplines gate before the write; the registry backstops either way), so this is WEAKENED, not REOPENED — but the mechanism is under-specified and will fork.

**Fix (AD-6 + AD-14 rule).** Make the write-approval gate node-agnostic: state that **any** node needing a write — free-chat or a QAS node such as RTW — reaches the write tool **only by routing the call through the single `create_agent`-core tool step gated by `HumanInTheLoopMiddleware`** (the QAS node does its tool-merge + prose, then hands the write proposal to the core's gated executor). Equivalently, redefine "the write gate" as one shared approval step keyed on `pending_approval` that both the core middleware and any router node feed, and stop describing it as bound to the core alone. There must be exactly one write-approval discipline regardless of entry node.

---

## Minor consistency notes (non-blocking; fold into a copy-edit pass)

- **`caller` appears in two places (touches F7/F12).** AD-6 lists `caller` inside the state-channel set "(`messages`, `caller`, `claim_id`, `pending_approval`, …)" *and* says "the AD-7 caller context carried on its `context_schema`." F7's fix requires caller to be a re-resolved runtime reference, never a checkpointed/replayed scope — i.e. it should live on `context_schema` only. Recommend removing `caller` from the checkpointed state-channel list and stating it is supplied via `context_schema` on every run/resume, so the doc can't be read as persisting it.
- **`edit` is a third outcome the F11 audit wording predates.** AD-6 now allows `approve | edit | reject` but still says "**Both** branches record the outcome … via `record_copilot_approval`." With three decisions, "both" is ambiguous. Recommend: **all three** decisions record via `record_copilot_approval`; on `edit`, the record notes the decision was `edit` (still content-free) and the subsequent AD-4 write references it, same as `approve`. This keeps the F11 "every approval outcome leaves a trace" guarantee whole.

---

## Bottom line

The v1.3 write-gate reimplementation is architecturally sound and does **not** reopen the closed critical findings: F1's no-unapproved-write invariant is preserved by the middleware interrupt plus the retained registry defence-in-depth (fail-safe raise), and F2/F3/F4/F6/F7/F9/F10/F11/F12/F13 are either untouched or preserved (F7 and F13 arguably strengthened). The two soft spots are direct products of moving from a bespoke, node-callable gate to a framework middleware bound to the `create_agent` core: **F5** (multi-action interrupts vs. singular `pending_approval`) and **F8** (QAS-node write path vs. a core-bound gate). Both are WEAKENED (mechanism ambiguous / spec-vs-mechanism mismatch), neither is REOPENED (the underlying invariants still hold). Apply the F5 and F8 fixes above and the amendment integrity is clean.
