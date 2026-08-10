# Spine Rubric Review — LINEWORKER Architecture, v1.3 amendment (copilot middleware swap)

**Reviewer role:** good-spine rubric reviewer, BMad "Validate" gate
**Artifact:** `ARCHITECTURE-SPINE.md` (status: final, updated 2026-08-10)
**Scope of pass:** v1.3 amendment — AD-6 / AD-13 / AD-14 + Stack rows moved the copilot's tool-calling core to LangChain v1 `langchain.agents.create_agent` embedded as the free-chat node of a deterministic supervisor-router `StateGraph`, and reimplemented the write-approval gate as `HumanInTheLoopMiddleware` (interrupt_on the write tools; allowed_decisions approve/edit/reject). Emphasis on internal consistency of the swap; anything else flagged opportunistically.

---

## VERDICT: PASS WITH FINDINGS

The swap is largely coherent and — importantly — clean of legacy residue: no dangling reference to the old v1.2 "approval-token" step or the "write tools never exposed to any LLM tool-selection node" mechanism survives anywhere in the document (verified across all ADs, the Stack table, the Consistency Conventions, and the Capability Map). AD-6, AD-13, the Stack rows, the Copilot-stream / Write-concurrency / Testing-&-CI conventions, and the Capability Map all agree on the new middleware model. Every dimension the feature altitude owns is decided or explicitly deferred — no whole silent dimension. Two real internal contradictions remain, both a direct consequence of the swap not being propagated into the *non-core* write path (RTW quick action) and into the defence-in-depth handshake. Neither is a silent safety hole on its own, but they must be reconciled before build. Hence PASS WITH FINDINGS, not FAIL.

---

## Findings

### HIGH-1 — RTW-letter write path is gated by a middleware that its node never routes through (AD-14 ↔ AD-6 contradiction)

**Location:** AD-14 rule, line 125 ("the RTW letter **is** a QAS key whose node runs tool-merge → LLM prose → the `HumanInTheLoopMiddleware` write-tool gate (AD-6)"); against AD-14 line 125 ("only free-text messages go to the `create_agent` core") and AD-6 line 77 ("every `kind: write` tool is gated by `HumanInTheLoopMiddleware` **on the `create_agent` core**").

**Problem:** `HumanInTheLoopMiddleware` is, per AD-6 and the Stack row (line 161), bound to the `create_agent` core, and it fires by intercepting a *model-selected* tool call inside that agent loop. But the RTW letter is a QAS key, and AD-14 is explicit that QAS keys "route through a static key→node map in the supervisor router **before** any LLM call" and that "only free-text messages go to the `create_agent` core." The RTW node therefore (a) does not execute inside the core agent loop, and (b) constructs its write deterministically via `tool-merge` — the LLM in a QAS node "drafts only surrounding prose" and "never chooses tools" (AD-2, AD-14). A deterministic write call in a non-core node is exactly what `interrupt_on` cannot catch, so the gate named for it does not apply. In v1.2 the standalone approval-token step was node-agnostic and any node (including a QAS node) could invoke it; the swap replaced it with a core-bound middleware and never reconciled the one write path that lives outside the core. This is the single clearest "claim in one section contradicts another after the swap."

**Why it matters:** AD-6's stated Prevents is "AI mutating claim records without approval." The RTW letter produces an AD-12-owned write (a saved document/email). As written, its gate mechanism is inapplicable; the feature path is either broken (the write errors out — see HIGH-1's interaction with MEDIUM-1) or, worse, ungated if the defence-in-depth in MEDIUM-1 also doesn't hold.

**Suggested fix:** Pick one and state it in both AD-6 and AD-14: (a) route the RTW node's *write step* through the `create_agent` core with a constrained toolset so the middleware naturally gates it, and reword AD-14's "only free-text messages go to the core" to "only free-text messages *choose tools via* the core; QAS write steps re-enter the core solely to execute their pre-merged write tool under the gate"; or (b) factor the approval gate into a single reusable interrupt/approval node (a `require_approval` step) that both the core middleware and any QAS write node invoke, so the gate is node-agnostic again — restoring the v1.2 property the swap lost — and have AD-6 describe the middleware as the core's binding of that shared node.

### MEDIUM-1 — Defence-in-depth registry raise depends on an unspecified middleware↔tool state handshake (AD-13)

**Location:** AD-13 rule, line 119 ("the registry still refuses a write tool executed outside an approved interrupt (**raises without the middleware's approval in graph state**), so a hand-added node cannot bypass the gate").

**Problem:** The write-tool wrapper is said to independently verify "the middleware's approval in graph state." But `HumanInTheLoopMiddleware`'s approve path simply resumes execution of the tool call inside the agent loop; the amendment never defines a durable, tool-readable approval marker keyed to *that specific call* that the wrapper can inspect at execution time. So the second, independent gate — the whole point of "defence-in-depth" and the thing that would otherwise contain HIGH-1 — rests on an interface that is asserted but not specified, and may not exist in the middleware's actual state shape. This is the same swap-propagation gap as HIGH-1, seen from the enforcement side: the old approval-token was a concrete artifact a wrapper could check; "the middleware's approval in graph state" is not defined to be one.

**Why it matters:** The rubric requires every Rule to be *enforceable*. As written, the registry raise is a good intention without a defined mechanism; combined with HIGH-1 it is the difference between "RTW write fails safe" and "RTW write executes unapproved."

**Suggested fix:** Specify the exact state contract: e.g., on approve/edit the middleware (or the shared approval node from HIGH-1's fix (b)) records an `approved_calls` entry (tool name + call id + drafted-against versions) in the closed state schema declared in `agents/state.py`, and every `kind: write` registry wrapper asserts a matching un-consumed entry before touching its AD-4 command, consuming it atomically. Name that channel in AD-6's State paragraph alongside `pending_approval`.

### MEDIUM-2 — "At most one `pending_approval` per thread" may not match the middleware's parallel-tool-call batching (AD-6)

**Location:** AD-6 rule, line 77 ("at most one `pending_approval` (the middleware's pending write-tool call) exists per thread — a second claim-write in one run **sequences as a second interrupt after the first resolves**").

**Problem:** If the model emits two write tool calls in a single turn, `HumanInTheLoopMiddleware` collects the approvals into one interrupt payload (a list of action requests), rather than raising them as strictly sequential, one-after-the-other interrupts. The spine asserts a stronger single-flight invariant ("at most one pending_approval," "sequences as a second interrupt after the first resolves") than the chosen middleware necessarily provides out of the box. If the middleware batches, the invariant is violated and the write-serialization the convention relies on (Write concurrency row, CAS per recorded version) has to be enforced some other way. (Version/tech behavior is another reviewer's job; flagged here as a *structural* mismatch between the asserted invariant and the named mechanism.)

**Suggested fix:** Either constrain the core to at most one write tool call per model turn (reject/parallel-disable parallel tool calls for `kind: write`, stated in AD-6 or AD-13) so the "one pending_approval" invariant is actually produced, or restate the invariant to match batched approvals (one interrupt may carry N write requests; each is individually approve/edit/reject'd and each CAS's on its own recorded versions). Pick one and make AD-6 and the Write-concurrency convention agree.

### LOW-1 — Undefined term "F1 gate" (AD-6)

**Location:** AD-6 rule, line 77 ("the F1 gate is the per-tool middleware, not tool-hiding").

**Problem:** "F1" appears exactly once and is defined nowhere in the spine; `companions: []` is empty, so there is no external glossary it resolves against. It reads as a leftover reference to a hazard/failure-mode ID from another artifact.

**Suggested fix:** Either spell it out ("the write-safety gate is the per-tool middleware, not tool-hiding") or, if F1 is a real hazard ID, add the companion/reference that defines it.

### LOW-2 — `edit`-decision branch leaves version/audit handling implicit (AD-6)

**Location:** AD-6 rule, line 77 (approve and reject branches are fully specified — version CAS + fail-safe for approve, discard + cancel message for reject; the `edit` branch says only "the human-revised arguments replace the model's before execution").

**Problem:** For the three allowed decisions (approve | edit | reject), the version-CAS fail-safe and the `record_copilot_approval` audit are spelled out for approve and reject but not for edit. It is left implicit whether an edited write still CAS's on the *originally recorded* drafted-against versions (preserving the stale-approval fail-safe) or silently re-reads fresh versions (which would let an edit bypass stale detection), and whether edit audits as its own outcome. "Both branches record the outcome" reads as approve/edit vs. reject, but the grouping is ambiguous.

**Suggested fix:** State that an `edit` executes through the same AD-4 command path as `approve`, CAS-ing on the originally recorded versions (so edit cannot bypass the 409 fail-safe), and is audited via `record_copilot_approval` with `action kind = edit`.

---

## Checklist scorecard

- **Fixes the real divergence points, misses none:** Pass. The copilot write path, thread identity, state schema ownership, streaming protocol, and degradation are all pinned; the one gap is the RTW write path's gate mechanism (HIGH-1).
- **Every AD's Rule enforceable and prevents its divergence:** Mostly. AD-6's approval gate is enforceable for the free-chat core but under-specified for the RTW QAS node (HIGH-1); AD-13's defence-in-depth is asserted without a defined handshake (MEDIUM-1).
- **Nothing under Deferred lets two units diverge:** Pass. Dashboard-scope copilot, model pinning, knowledge-corpus ingestion, answer-quality eval, etc. are all fenced with the binding floor stated (thread key reserves `dashboard`; `services/rag` ownership fixed; graph/routing tests are the binding floor).
- **Named tech verified-current:** Deferred to the version reviewer per instructions; structurally the Stack rows are consistent with AD-6/13/14.
- **Inherited-parent weakening:** N/A (no parent spine).
- **Every owned dimension decided/deferred/open:** Pass. No whole silent dimension found.
- **v1.3 internal consistency (the focus):** AD-6, AD-13, Stack, Copilot-stream, Write-concurrency, Testing-&-CI, and the Capability Map agree; AD-14's RTW clause is the one section that contradicts AD-6 (HIGH-1). No dangling "approval-token" / "never-LLM-selectable" residue anywhere.

---

*Root-cause note:* HIGH-1, MEDIUM-1, and LOW-2 share one root — the swap moved the gate from a node-agnostic approval-token *step* to a core-bound middleware, but three places still assume a node-agnostic, state-inspectable approval (the RTW QAS node, the registry defence-in-depth, and the edit branch's fail-safe). Fixing the gate as a single shared approval node invoked by the core middleware (HIGH-1 fix (b)) resolves all three at once and restores the v1.2 property that any write path — core or QAS — hits the same gate.
