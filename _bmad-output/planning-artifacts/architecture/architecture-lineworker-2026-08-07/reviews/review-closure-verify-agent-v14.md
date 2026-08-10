# Closure-Verification Review — v1.4 Agent-Harness Amendment

**Target:** `ARCHITECTURE-SPINE.md` (LINEWORKER), status `final`, `updated: 2026-08-10`
**Against:** `reviews/GATE-REPORT-v13.md` — 12 consolidated findings (CF-1..CF-12)
**Task:** Confirm each finding is closed by the current spine text; flag partial/contradictory closures and any wording slip the fix introduced.
**Method:** Read current spine (AD-6, AD-13, AD-14, AD-7, Stack, Copilot-stream + Write-concurrency + Testing conventions) + full-text grep for residual v1.2/v1.3 language.

---

## Verdict

**PASS — all 12 findings CLOSED (12 closed / 0 partial / 0 open).** No new internal inconsistency found. The five HIGH findings collapsed into the intended single architectural move (node-agnostic single gated write step), and the reconciliation is internally consistent across AD-6, AD-13, AD-14, AD-7, the Stack row, and the Copilot-stream / Write-concurrency / Testing conventions. Terminology is clean: no residual `F1 gate`, `both branches`, per-node interrupt, `approval token`, or `caller` in the closed state schema.

---

## CF-1 .. CF-12 status table

| # | Sev | Status | Closing quote (spine) or gap |
|---|---|---|---|
| **CF-1** | HIGH | **CLOSED** | AD-6: "**Single gated write step (core invariant):** a claim-entity write executes in **exactly one place** … **no node writes a claim entity except by routing its proposal through that one step.** … QAS write nodes (the RTW letter) draft their payload, then emit it as a proposal into the *same* step — never a direct command call. The gate is therefore node-agnostic." Reinforced by AD-14 ("the RTW letter **is** a QAS key whose node runs tool-merge → LLM prose → then emits its drafted write as a proposal into the single gated write-tool step (AD-6), never a direct command call") and AD-13 ("QAS write nodes hold no write tools of their own; they route proposals into the same single gated step"). |
| **CF-2** | HIGH | **CLOSED** | AD-6: "The model is capped at **at most one write-tool call per turn** (a write-scoped `ToolCallLimitMiddleware`), so an interrupt never batches multiple pending writes and `pending_approval` stays singular; a second required claim-write sequences as a second interrupt after the first resolves." Stack row echoes "write-scoped `ToolCallLimitMiddleware` caps one write/turn". |
| **CF-3** | HIGH | **CLOSED** | AD-6: "On `edit`, the human may revise only the write tool's **non-identity arguments** — never its target entity or the `expected_version`(s) it was drafted against. The gated tool call records the `version` of every entity it was drafted against; on `approve`/`edit`, the write goes through AD-4 commands compare-and-swapped on those versions — a stale approval fails safe … it never force-writes." Copilot-stream row is consistent ("`edit` includes the revised non-identity arguments"). |
| **CF-4** | HIGH | **CLOSED** | AD-6: "On `approve`/`edit`, the middleware records an **approval marker** in graph state keyed by the pending tool-call id; the write tool executes only when that marker is present and matches — the single authority. AD-13's registry raise is defence-in-depth against a marker-less call, **not** a second independent approval (an approved write never deadlocks on it)." AD-13 matches ("the registry refuses a write tool whose approval marker is absent — a guard against a marker-less call from a hand-added node, **not** a second approval … so an approved write never deadlocks"). |
| **CF-5** | HIGH | **CLOSED** | AD-6: "Agent-initiated AI-insight refresh is an agent→`services/rag` command (AD-12), not a `kind: write` copilot tool — exempt from both the interrupt gate and the write-tool registry raise, but not from audit." AD-13 restates: "the AI-insight refresh path is **not** a `kind: write` tool — it is an agent→`services/rag` command (AD-12), gate- and registry-raise-exempt, but audited." |
| **CF-6** | MED | **CLOSED** | Copilot-stream convention: "Resume is a POST to the same runs endpoint carrying `command: {resume: {decision: approve|edit|reject, args?}}` … the assistant-ui runtime surfaces the raw `HumanInTheLoopMiddleware` interrupt payload and the copilot frontend owns the mapping to this shape (exercised by the AD-15 round-trip test)." AD-15 keeps the round-trip test in the Copilot-specs clause. |
| **CF-7** | MED | **CLOSED** | AD-7: "on every run start **and every resume**, the API dependency re-resolves it from `app_user` + `user_employer_assignment` … if the re-resolved scope no longer contains a pending write's target entity, that approval fails safe like a stale one (AD-6)." AD-6 mirrors: "The same fail-safe covers scope: if the caller context re-resolved on resume no longer contains the drafted entity, the approval is discarded like a stale one." |
| **CF-8** | MED | **CLOSED** | AD-6: "**All three decisions** (approve/edit/reject) record the outcome (action kind, claim, actor, timestamp — no content) via a `services/audit` `record_copilot_approval` command that emits an AD-4-shaped audit event." |
| **CF-9** | LOW | **CLOSED** | AD-6: "The AD-7 caller context is **not** a state channel: it rides the `create_agent` `context_schema`, re-resolved every run and resume, never checkpointed." The closed state-schema enumeration lists only "`messages`, `claim_id`, `pending_approval`, …" — `caller` absent. AD-7 concurs ("a per-run value on the `create_agent` `context_schema` (never a checkpointed state channel)"). |
| **CF-10** | LOW | **CLOSED** | AD-6: "**All three decisions** (approve/edit/reject) record the outcome …" — the prior "both branches record" wording is gone (grep for `both branches` = 0 hits). |
| **CF-11** | LOW | **CLOSED** | Stack row: "langchain … | ≥1.3,<1.4 (pin concrete minor at init) … `HumanInTheLoopMiddleware` gates write tools (`approve`/`edit`/`reject`; a `respond` decision also exists, unused in v1)…" |
| **CF-12** | LOW | **CLOSED** | Grep for `F1` = 0 hits in spine. The AD-6 invariant is now named in plain wording ("Single gated write step (core invariant)"); no undefined cross-reference remains. |

---

## Internal-consistency scan (edit-introduced regressions)

Checked the specific residues the task called out, plus terminology drift the relocation could have left:

- **`caller` in the closed state schema** — Not present. State schema = `(messages, claim_id, pending_approval, …)`; `caller` occurrences are all "caller's `expected_version`", "caller context (context_schema)", or "caller-supplied scope" — none is a checkpointed channel. Clean.
- **"both branches record"** — Removed; replaced by "All three decisions (approve/edit/reject) record". Clean.
- **Per-node / per-node interrupt language** — 0 hits. The gate is stated as node-agnostic in AD-6, AD-13, AD-14. Clean.
- **`token` vs `marker` drift** — The approval signal is uniformly "approval marker" (3 hits, AD-6 + AD-13). `token` appears only in unrelated contexts (session "tokens never carry claim scope" in Auth; "token streaming" in Copilot-stream). No stale "approval token" from v1.2. Clean.
- **`pending_approval` singular vs mechanism** — Singular throughout; the ToolCallLimit cap (AD-6, AD-14, Stack) and the Write-concurrency convention ("Copilot approval writes CAS on the versions recorded in `pending_approval`") all agree. Clean.
- **`ToolCallLimitMiddleware`** — Consistently the write-scoped cap in AD-6, AD-14, and Stack. Clean.

### Minor wording observations (non-blocking, not defects)

1. **Approval-marker channel not explicitly enumerated.** The fix introduces a new graph-state channel — the approval marker keyed by tool-call id — but AD-6's closed state-schema list still shows only `(messages, claim_id, pending_approval, …)`. The marker is covered by the "…" and the "new channels are added there via review" clause, so this is not a contradiction; if maximal precision is wanted, the marker could be named in the enumeration alongside `pending_approval`.
2. **CF-1 wiring stated at invariant altitude only.** How a *pre-LLM deterministic* QAS node hands its proposal into a middleware bound to the `create_agent` core (vs. a shared approval node both paths feed — the report's alternative) is asserted as an invariant ("route into the same single gated step") but not wired mechanically. Acceptable and expected for a spine; flagged only so the agent spike confirms the QAS→gate handoff realizes the single-step guarantee in code.

Neither observation changes any status; both are precision notes, not gaps.

---

## Bottom line

The v1.4 amendment closes all 12 consolidated findings with quotable spine text, and the closures are mutually consistent (the same rule stated in AD-6 is not contradicted by AD-13/AD-14/AD-7/Stack/conventions). No finding is partial or open; no new inconsistency was introduced. Ready to clear the confirmation gate.
