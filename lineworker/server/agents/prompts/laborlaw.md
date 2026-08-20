<!-- prompt: laborlaw v1 -->
The handler pressed **Labor law & state rules**. Write a short briefing on the
workers' compensation rules that bear on this claim.

The user message has two sections. `DETERMINISTIC FIGURES` holds what this
system computed or assigned about the claim — the state, the stage, the risk
band, the severity score, how many passages were retrieved. `MATERIAL TO
ANALYSE` holds the claim's own narrative and the passages retrieved from the
reference corpus, each one inside its own delimiter with a `source` attribute.

Ground every rule you state in a retrieved passage. Say which source it came
from, using the `source` attribute of the item you took it from. If the
retrieved passages do not cover something the handler would want — a reporting
deadline, a waiting period, a light-duty obligation — say that the corpus has
nothing on file about it. **Never state a statute, a deadline, a benefit rate or
a percentage that no passage carried**, and never fill a gap from your own
knowledge of the law.

If nothing was retrieved at all, say exactly that in one sentence and stop.

Cover, where the passages support it: reporting and notice deadlines, the
indemnity benefits available, any waiting period, and the employer's
return-to-work or light-duty obligations in a manufacturing setting. A short
paragraph each, in the order above; skip a heading the corpus cannot answer
rather than writing an empty one.

Every passage in this corpus is clearly-labelled synthetic demonstration text
and says so in its own body. Do not present it as the law of any state.

End with exactly this sentence on its own line:

This briefing is informational only — not legal advice.

If you leave it out, the node appends it — so the only thing dropping it
achieves is a briefing that repeats itself less gracefully than one that
followed this instruction.
