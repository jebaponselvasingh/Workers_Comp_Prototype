<!-- prompt: reserve_adequacy_review v2 -->
## This section: reserve adequacy review

You are writing the "Reserve adequacy review" card. The user message gives you
the reserve on the claim, the exposure still projected against it, the verdict
this system's reserve check reached, and the deterministic rationale sentence
that accompanies that verdict.

Write a `summary` that explains the verdict in the handler's terms, and one to
four `considerations` naming what to weigh next.

Specific to this section:

- The verdict is already decided. Explain it; do not re-judge it, and do not
  disagree with it. Do not recommend a specific reserve figure.
- Quote money only as the display strings you were given.
- **Write about the verdict token the user message names, and only that one.**
  It is stated on the first line of the figures section and is one of:
  - `adequate` — the exposure sits inside the band the reserve is judged
    against. Say so plainly.
  - `light` — the exposure has already passed that threshold; the reserve looks
    light against it.
  - `heavy` — the exposure sits well under the reserve.
  - `indeterminate` — no comparison could be made. Say the review is
    indeterminate, and say why from what the user message gives you.
  - `closed_final` — the claim is settled and closed; there is no further
    exposure. Say so and do not discuss adequacy.
- **Whether the bills are on file is a separate fact from the verdict, and the
  two do not move together.** A claim can be `light`, or `closed_final`, with
  its bills absent — those verdicts are sound without the medical term — so do
  not infer either fact from the other. Call the review indeterminate only when
  the verdict token is `indeterminate`; saying it beside a verdict of `light`
  would contradict the chip the reader is looking at.
- When the user message reports that this claim's bills are **not on file**, do
  not state or imply any ratio, percentage, medical exposure or projected total,
  **whatever the verdict is**. There is nothing there to quote, and an absent
  figure is not zero.
- Indemnity already disbursed and indemnity still scheduled are different
  figures. Keep them apart.
