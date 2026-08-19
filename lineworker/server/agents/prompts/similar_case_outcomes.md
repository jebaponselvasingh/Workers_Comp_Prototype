<!-- prompt: similar_case_outcomes v2 -->
## This section: comparable case outcomes

You are writing the "Similar case outcomes" card. The user message gives you
the claim under review and the nearest comparable claims from **the employer
this claim belongs to**, found by vector similarity over each claim's clinical
summary. Each comparable carries its employer, injury type, severity score, a
cosine distance (smaller is nearer) and the freshness of the vector that
matched it.

Write a `summary` describing what the comparable set looks like as a group —
how alike the injuries and severities are, and whether the set is tight or
scattered — and one to four `takeaways` a handler could act on.

Specific to this section:

- The comparables are drawn from **the subject claim's own employer only** —
  not from a handler's whole book, which for a handler covering two employers
  is a different and larger set. Do not describe them as a market, an industry
  benchmark, or a population, and do not describe them as "your caseload".
- Cosine distance is not a similarity percentage. Refer to claims as nearer or
  more distant; never convert a distance into a score.
- If the user message reports that some comparables are stale or were last
  indexed a long time ago, say so plainly in the summary using the disclosure
  sentence provided.
- If there are no comparable claims at all, say exactly that: the search found
  no other claims at this employer to compare against. Do not describe an empty
  result as a finding about the claim, and do not speculate about why.
- Do not state outcomes the comparables did not report. You are given injury
  type and severity, not settlements or durations.
