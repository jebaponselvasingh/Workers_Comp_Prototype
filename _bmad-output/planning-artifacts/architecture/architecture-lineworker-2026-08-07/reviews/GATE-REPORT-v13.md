# Reviewer Gate Report — v1.3 Agent-Runtime Amendment

**Target:** `ARCHITECTURE-SPINE.md` (LINEWORKER) — AD-6 / AD-13 / AD-14 + Stack, now built on LangChain v1 `create_agent` + `HumanInTheLoopMiddleware`
**Intent:** Validate (report only — spine unchanged this pass)
**Date:** 2026-08-10
**Method:** `lint_spine.py` mechanical floor + 4 parallel reviewer subagents against the spine

---

## Gate verdict

> **FAIL for parallel build — but fixable, and from a single root cause.**
> The *direction* is validated: every named technology is real and current (web-verify PASS), and the amendment did **not** reopen the core F1 invariant — no unapproved AI write can execute (amendment-integrity: 0 reopened). But the adversarial pass found a cluster of **5 HIGH** under-specifications that would make two independent teams build incompatibly. Nearly all trace to one root cause.

### Root cause

The write-approval gate moved from a **node-agnostic approval step** (v1.2: "any node that writes must pass `interrupt()`; the registry raises without an approval token") to a **core-bound middleware** (`HumanInTheLoopMiddleware` on the `create_agent` free-chat node). That relocation was not reconciled with four facts the rest of the spine still asserts:

1. **Not every write path goes through the core.** The RTW-letter write is a *deterministic QAS router node* (AD-14: routes *before* any LLM call) — it never enters the `create_agent` loop the middleware sits in, so it has no gate.
2. **The middleware's decision model is richer than the old one.** `edit` (new) and multi-tool batching don't map cleanly onto AD-6's single `pending_approval` + version-CAS invariants.
3. **One write is deliberately gate-exempt** (AI-insight refresh, AD-6) — but AD-13's registry write-raise forbids *any* unapproved write, so the two rules collide once refresh is a core tool.
4. **The double gate (middleware + registry raise) has no specified handshake** — so it either deadlocks or is silently vacuous.

### Scorecard

| Reviewer | Verdict | Headline |
|---|---|---|
| `lint_spine.py` (mechanical) | **PASS** | 0 findings — no placeholders, dup IDs, missing Binds/Prevents/Rule, or unpinned versions |
| Rubric walker | **PASS with findings** | Legacy residue cleanly scrubbed; 1 HIGH (RTW path), 2 MED, 2 LOW |
| Web / version reality-check | **PASS** | All 7 LangChain/LangGraph/assistant-ui claims current; 3 non-blocking hygiene notes |
| Amendment-integrity (F1–F13 regression) | **PASS** | 11 still-closed · 2 weakened · **0 reopened** |
| Adversarial (two-unit divergence) | **FAIL for parallel build** | 5 HIGH + 2 MED conforming-but-incompatible pairs |

---

## Consolidated findings (deduped across reviewers, severity-ranked)

Each finding notes which reviewers independently flagged it — convergence = high confidence.

| # | Sev | Finding | Flagged by | Fix direction |
|---|---|---|---|---|
| **CF-1** | **HIGH** | **RTW / QAS router-node writes are ungated.** The gate is bound to the `create_agent` core, but the RTW letter is a QAS node routed *before* any LLM call (AD-14). Its write bypasses the middleware → the exact node-vs-tool F1 divergence, reintroduced. Two teams build two write disciplines. | Rubric (HIGH), Adversarial (F6-v13), Integrity (F8) | Make the gate **node-agnostic**: exactly one place a claim-entity write executes, always behind the gate. QAS write nodes route their write *through* the same core-gated tool step (or one shared approval node both paths feed). |
| **CF-2** | **HIGH** | **Multi-write turn vs. single-flight.** A model turn can select two write tools; `HumanInTheLoopMiddleware` surfaces them as **one** multi-action interrupt — contradicting AD-6's singular `pending_approval` and "a second write sequences as a second interrupt." | Adversarial (F2-v13), Integrity (F5), Rubric (MED) | Pick one: cap writes to one per turn (write-scoped `ToolCallLimitMiddleware` / prompt constraint), **or** make `pending_approval` plural and CAS each action. Schema must match mechanism. |
| **CF-3** | **HIGH** | **`edit` decision can defeat version-CAS / retarget the entity.** New `edit` branch: "frozen recorded versions" vs "re-resolve on approval" reads two ways — one lets a human cross entities, the other nullifies AD-6 stale-safety. | Adversarial (F1-v13), Integrity (nit), Rubric (LOW) | Constrain `edit` to non-identity tool args (never the target entity); CAS still on the versions recorded at draft; a version change forces a re-draft, not a force-write. |
| **CF-4** | **HIGH** | **Middleware↔registry double-gate handshake unspecified.** AD-13's defence-in-depth "registry raises without the middleware's approval in graph state" names no signal — so it either deadlocks (raises on approved writes) or is vacuous. | Adversarial (F4-v13), Rubric (MED) | Name the approval marker (middleware writes it to graph state keyed by tool-call id; registry checks it), **or** collapse to a single gate. Don't ship two gates with an undefined handshake. |
| **CF-5** | **HIGH** | **AI-insight refresh: gate-it vs. carve-it-out contradiction.** Refresh is a write (AD-12), interrupt-exempt (AD-6), yet AD-13's registry raise forbids any unapproved write. As a core tool, the two rules collide. | Adversarial (F3-v13) | State explicitly that the rag-refresh path is **not** a `kind: write` copilot tool — it's an agent→`services/rag` command exempt from both the middleware gate and the registry raise. The write-tool gate binds claim-entity writes only. |
| **CF-6** | **MED** | **approve/edit/reject wire shape unmapped (backend↔assistant-ui).** The runtime surfaces the *raw* interrupt; mapping the middleware's structured decisions + building the resume `Command` is developer-owned. The Copilot-stream convention's `command:{resume:…}` doesn't specify per-decision payloads. | Adversarial (F5-v13), Web-verify (nuance) | Define the resume payload shape per decision in AD-6 / AD-9 / Copilot-stream convention. AD-15's round-trip test is the correct mitigation — keep it. |
| **CF-7** | **MED** | **Scope narrowed mid-approval is undefined.** AD-7 covers resume-actor 403 and stale-version fail-safe, but not "same user, fewer employers now" — a resumed approval could complete a write to a now-out-of-scope claim. | Adversarial (F7-v13) | On resume, re-resolve scope; if the drafted entity is now out of the caller's book, fail safe exactly like a stale approval. |
| **CF-8** | **MED** | **`record_copilot_approval` payload shape drifts from AD-4.** The approve/reject audit command's shape isn't pinned to the fixed AD-4 event schema. | Adversarial (F8-v13) | Spec the payload against AD-4's fixed schema (action kind, claim, actor, at — no content). |
| **CF-9** | **LOW** | **`caller` is in both `state_schema` and `context_schema`.** F7 wants it context-only (a reference re-resolved each run), never a checkpointed state channel. | Integrity (nit), Adversarial (~F7) | Move `caller` to `context_schema` only; drop it from the closed state-schema list. |
| **CF-10** | **LOW** | **`edit` not covered by F11's audit wording.** AD-6 says "both branches record" (approve/reject) — should be all **three** decisions. | Integrity (nit), Web-verify | Reword to "all three decisions record the outcome via `record_copilot_approval`." |
| **CF-11** | **LOW** | **Version hygiene.** `langchain` `1.x` has advanced to 1.3.x — pin a concrete minor at init. `HumanInTheLoopMiddleware` also offers a 4th decision (`respond`); the spine's approve/edit/reject is a valid subset — note it so a future ask-user path isn't blocked. | Web-verify | `langchain >=1.3,<1.4`; one-line note on `respond`. |
| **CF-12** | **LOW** | **"F1 gate" is undefined jargon in AD-6.** `companions: []`, so the internal cross-reference resolves to nothing for a reader. | Rubric (LOW) | Replace with plain wording or footnote the reference. |

---

## Recommended remediation (one coherent v1.4 amendment)

All five HIGH findings collapse into **one architectural move**, plus reconciliation of the richer decision model:

1. **Re-establish the single node-agnostic write gate as the AD-6 invariant** (not a middleware detail): *there is exactly one step where a claim-entity write executes, and it is always behind human approval.* Realize it via `HumanInTheLoopMiddleware` on the core **and** by routing every non-core (QAS) write node through that same gated tool step. (CF-1, CF-4)
2. **Reconcile single-flight with batching** — one pending write per interrupt, or plural `pending_approval` with per-action CAS. (CF-2)
3. **Constrain `edit`** — args-only, never the target entity; CAS on drafted versions; audit all three decisions. (CF-3, CF-10)
4. **Carve out rag-refresh** explicitly as a non-gated, non-write-tool command. (CF-5)
5. **Specify the resume wire contract** per decision, and the mid-approval scope re-resolution. (CF-6, CF-7, CF-9)
6. **Pin `langchain` to a concrete minor**; note the 4th decision; drop the "F1 gate" jargon. (CF-11, CF-12, CF-8)

---

## Per-reviewer detail

- Rubric walker — `reviews/review-rubric-agent-v13.md`
- Web / version reality-check — `reviews/review-web-verify-agent-v13.md`
- Adversarial two-unit divergence — `reviews/review-adversarial-agent-v13.md`
- Amendment-integrity (F1–F13 regression) — `reviews/review-amendment-integrity-agent-v13.md`

---

## v1.4 Remediation Outcome (2026-08-10)

The full remediation was applied to the spine + both renderings, then a **confirmation gate** was run.

- **CF-closure verifier** — `reviews/review-closure-verify-agent-v14.md` — **PASS: all 12 findings (CF-1…CF-12) CLOSED**, 0 partial / 0 open, no new inconsistency.
- **Adversarial re-attack** — `reviews/review-adversarial-agent-v14.md` — confirmed **all 5 original HIGH genuinely closed**, but surfaced **2 new HIGH** the edits introduced (local wording, not structural):
  - **NEW-F1** — `caller` moved to `context_schema` in AD-6/AD-7 but AD-13 still read it "from graph state" → **fixed** (AD-13 now reads it from the run context).
  - **NEW-F2** — QAS→gate handoff mechanism ambiguous (forking the wire shape) → **fixed** (QAS writes hand to the core as a write-tool call, one identical interrupt/resume shape; AD-15 now tests both write paths).
  - Plus 3 MED (refresh vs registry `kind`, one-write-cap wording, "non-identity args" definition) → **fixed**; and 4 lower-altitude mechanism nits (marker channel lifecycle, decision reverse-mapping, fail-safe status code, reject audit shape) → **named under Deferred › Copilot approval-gate wiring** for the agent spike.

**Final state:** `lint_spine.py` clean (0); all 12 CF closed; 7 original + 2 introduced HIGH all closed; residual implementation-altitude mechanism explicitly deferred (invariants binding, wiring the spike's to finalize under the AD-15 round-trip test). **No HIGH open at spine altitude.** A third independent adversarial pass on the two NEW-F fixes was not run (the fixes are the reviewer's prescribed wording, applied verbatim) — available on request.
