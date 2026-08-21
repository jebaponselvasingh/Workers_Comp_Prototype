/**
 * The ranked red-flag clauses — cached AI content, labelled as such (AC 2, AD-10).
 *
 * The only surface on this dashboard whose figures do not describe claims. Every
 * number here is about the **cache**: how many claims in scope carry a fraud
 * narrative, when the oldest and newest of them were generated, how many rows the
 * server could not read. AD-10's rule is that model output is rendered with its
 * generation time and never presented as claim data, and on a portfolio-wide view
 * the practical form of that rule is the coverage line: five clauses over a
 * hundred claims and four narratives says something very different from five over
 * a hundred and a hundred.
 *
 * **It renders through `InsightShell`, imported from the claim detail's insights
 * folder rather than copied.** That component owns the two things this card must
 * not be able to ship without — the `Generated … · model` line and the
 * `data-kind`/`data-status` attributes — so reusing it is what makes "the
 * provenance cannot be dropped" structural instead of a habit. The one thing it
 * assumes and this surface does not have is a *single* model: a portfolio view
 * spans generations, so the shell is handed the models joined, and the range is
 * captioned separately below.
 *
 * **Rows are not clickable, and that is a decision rather than an omission.**
 * Every other segment on this dashboard opens the claims behind it. A clause
 * cannot: "which claims does this phrase name" is a question only the fold's own
 * normalisation can answer, and turning that into a claim population would be the
 * browser asserting a classification nobody versioned (AD-2). The card says so in
 * its own caption rather than leaving a reader to wonder why the rows are inert.
 *
 * **The caption admits what the ranking is.** Exact-text grouping over
 * model-authored prose, so a count of one is the ordinary case. Clustering would
 * be the server originating a taxonomy with no owner and no version; the honest
 * alternative is to say what the fold did and let the reader judge it.
 *
 * **A cold cache is a first-class empty card, never a spinner and never a gap**
 * (NFR-3). It is the seeded state and the ordinary one — nothing is generated for
 * a claim until a refresh reaches it — and this surface cannot start one:
 * `services/rag` owns the writes (AD-12), and the affordance that regenerates an
 * insight is on the claim, where the handler who edited it is standing. So the
 * empty state is **one** sentence, supplied through `InsightShell`'s
 * `emptyMessage` prop rather than left to that component's per-claim default:
 * telling an analyst on a portfolio page to "use Refresh above to generate this
 * claim's insights" names a control that is not there, about a claim this card is
 * not about, and appending a correction underneath it made two paragraphs that
 * disagreed.
 *
 * **And there are two empties, which is why the sentence is chosen rather than
 * fixed.** "No narrative has been generated for any claim in this book" and "every
 * narrative in this book came back low-risk" are different facts, and the second
 * is a *result* — on a fraud surface, the reassuring one. Treating both as
 * `not_generated` is still right, because `InsightShell`'s ready branch draws a
 * provenance line and a provenance line over an empty list is a card claiming to
 * have said something; conflating what they *say* was not.
 */
import type { FraudRedFlags } from "@/api/dashboard";
import { InsightBullets, InsightShell } from "@/features/claim-detail/insights/InsightShell";
import { FRAUD_OUTCOME_TONE } from "@/features/claim-detail/insights/insightTone";
import { formatNotedAt } from "@/lib/clock";
import { CHIP_CLASS } from "@/features/claim-detail/bills/statusTone";

/**
 * The card's identity in the DOM — the server's own `InsightKind` token.
 *
 * The same string `ai_insight.kind` holds and the same one the per-claim fraud
 * card stamps, so a test asserting "this is the fraud insight surface" reads one
 * vocabulary on both screens.
 */
const KIND = "fraud_risk_indicators";

/**
 * What the chip says, and why it is not `FRAUD_OUTCOME_LABEL.red_flags`.
 *
 * That map's words — "Review indicated" / "Low risk" — are the *outcome*
 * vocabulary, and `outcome` is a per-claim discriminator two registered
 * derivations decide on the server (`insightTone.ts` says so outright: nothing in
 * the browser decides which variant a claim gets). `FraudRedFlagsResponse`
 * carries no outcome at all — it is a fold over a cache — so a card stamping
 * "Review indicated" on it was the browser originating a verdict about a hundred
 * claims from a payload that makes no such claim, and stamping it on a cold cache
 * and on a book whose every narrative came back low-risk alike. That is precisely
 * the AD-2 failure the modules either side of this one spend paragraphs refusing.
 *
 * So the chip labels the **kind of content** the card holds, and it is drawn only
 * when there is content of that kind to label. `FRAUD_OUTCOME_TONE.red_flags`
 * still supplies the colour, because the tone really is the same vocabulary: an
 * analyst who has read this tint on a case file should read it here.
 */
const CLAUSE_CHIP_LABEL = "Red-flag clauses";

/**
 * The generation range as one phrase, or `null` when there is nothing to date.
 *
 * `null` rather than a range with an em dash in it: a card cannot draw a range
 * that does not exist, and "Generated — to —" is not a sentence. The cold-cache
 * branch below renders the empty state instead, which is the honest answer.
 *
 * Both ends are printed even when they are the same instant. A portfolio whose
 * narratives were all generated in one batch reads "X to X", which is true and
 * slightly awkward; collapsing it to one date would need a comparison, and a
 * comparison against a payload field in this directory is precisely what
 * `noDerivation.test.ts` refuses — for a good reason on a different field, and
 * for no benefit here.
 */
function generationRange(data: FraudRedFlags): string | null {
  if (data.generatedFrom === null || data.generatedTo === null) return null;
  return `${formatNotedAt(data.generatedFrom)} to ${formatNotedAt(data.generatedTo)}`;
}

/**
 * "1 claim" / "3 claims" — the caption below says a count of one is ordinary.
 *
 * Which is the whole reason this is a function and not an interpolation: exact-
 * text grouping over model-authored prose makes the singular the *most common*
 * row on the card, so "Late reporting of the injury — 1 claims" was the line an
 * analyst read first, on the surface whose caption argues that a count of one is
 * exactly what to expect.
 */
function claimTally(claims: number): string {
  return `${String(claims)} ${claims === 1 ? "claim" : "claims"}`;
}

/**
 * The one sentence a not-generated card says, chosen between two different facts.
 *
 * A book with no cached narrative at all has nothing to report. A book where
 * every narrative came back low-risk has been analysed and *found nothing* —
 * which is a result, is the more useful sentence on a fraud surface, and reading
 * "Not generated yet" over it was simply false. Both are the empty card, for
 * `InsightShell`'s reason (a provenance line over an empty list is a card
 * claiming to have said something); only what they say differs.
 */
function emptyMessageFor(data: FraudRedFlags): string {
  // Named in both branches rather than only where a ranking exists: a row this
  // build could not read is a fact about the cache, and an empty card that
  // quietly excluded one would be the state most likely to be *mistaken* for
  // "nothing to report".
  const degraded =
    data.unreadable === 0
      ? ""
      : ` ${String(data.unreadable)} cached narratives could not be read and are excluded.`;
  if (data.claimsWithInsight === 0) {
    return `No fraud narratives are cached for this portfolio yet. Indicators appear here once claims have been analysed — this view reads the cache and never generates it.${degraded}`;
  }
  return `${String(data.claimsWithInsight)} of ${String(data.claimsInScope)} claims in this portfolio have a cached fraud narrative, and none of them raised a red flag.${degraded}`;
}

export function RedFlagFrequencyCard({
  data,
  isPending,
  isError,
}: {
  /** The server's ranking, or `undefined` while it is unknown. */
  data: FraudRedFlags | undefined;
  isPending: boolean;
  isError: boolean;
}) {
  // `HandlerBenchmarkTable`'s one-predicate ruling, so the busy state and the
  // placeholder cannot come apart.
  const isLoading = isPending && data === undefined;

  if (isError) {
    return (
      <section
        data-testid="fraud-red-flags"
        aria-labelledby="fraud-red-flags-heading"
        className="rounded-lg border border-border bg-surface p-3"
      >
        <h3
          id="fraud-red-flags-heading"
          className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase"
        >
          Cached fraud indicators
        </h3>
        {/* Inline, never a dialog (NFR-3), and in place of the card's body: an
            empty ranking under a warning reads as "the model found nothing",
            which is a different and much quieter lie than a failure. */}
        <p
          role="alert"
          data-testid="fraud-red-flags-error"
          className="rounded-md border border-border bg-error-soft px-3 py-2 text-[11.5px] font-semibold text-error"
        >
          ⚠ The cached fraud indicators could not be loaded. Try again in a moment.
        </p>
      </section>
    );
  }

  if (isLoading) {
    return (
      <section
        data-testid="fraud-red-flags"
        aria-labelledby="fraud-red-flags-heading"
        aria-busy
        className="rounded-lg border border-border bg-surface p-3"
      >
        <h3
          id="fraud-red-flags-heading"
          className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase"
        >
          Cached fraud indicators
        </h3>
        <div data-testid="fraud-red-flags-skeleton" aria-hidden className="flex flex-col gap-[10px]">
          {Array.from({ length: 4 }, (_, index) => (
            <span key={index} className="block h-[14px] animate-pulse rounded bg-surface-2" />
          ))}
        </div>
      </section>
    );
  }

  const range = data === undefined ? null : generationRange(data);
  // "Ready" means *this view has something to show*, which on a cache-backed
  // surface is "at least one clause was read". A view with coverage but no
  // clauses — every narrative in the book said low risk — is deliberately the
  // empty state too: `InsightShell`'s ready branch draws a provenance line, and a
  // provenance line over an empty list is a card claiming to have said something.
  const status = data !== undefined && data.items.length !== 0 ? "ready" : "not_generated";

  return (
    <div data-testid="fraud-red-flags">
      <InsightShell
        title={
          <span className="flex flex-wrap items-center gap-2">
            Cached fraud indicators
            {/* Drawn only when there are clauses to label, and labelling *them*
                rather than the portfolio. See `CLAUSE_CHIP_LABEL`: this payload
                carries no `outcome`, so a chip rendered unconditionally was the
                browser announcing a verdict — on a cold cache, and on a book
                whose every narrative came back low risk, in error tokens. */}
            {status === "ready" && (
              <span className={`${CHIP_CLASS} ${FRAUD_OUTCOME_TONE.red_flags}`}>
                {CLAUSE_CHIP_LABEL}
              </span>
            )}
          </span>
        }
        testId="fraud-red-flag-card"
        status={status}
        kind={KIND}
        // Every distinct model that wrote a readable row, joined. There is no
        // single model to label a portfolio view with, and the honest answer over
        // a set is the set — `null` when nothing was readable, which is when the
        // shell draws no provenance line because there is no provenance.
        model={data === undefined || data.models.length === 0 ? null : data.models.join(", ")}
        // The **newest** generation, which is what "Generated …" means for a
        // range: the oldest end is in the coverage caption below, where it can be
        // labelled as one end of a range rather than read as a single instant.
        generatedAt={data?.generatedTo ?? null}
        // The whole of the empty state, in one sentence, chosen between the two
        // facts an empty ranking can be. Passed in rather than defaulted, because
        // the shell's own line names a Refresh button this page does not have and
        // speaks about "this claim" on a card describing a hundred.
        emptyMessage={data === undefined ? undefined : emptyMessageFor(data)}
      >
        {data !== undefined && (
          <>
            <InsightBullets
              items={data.items.map((item) => `${item.clause} — ${claimTally(item.claims)}`)}
              testId="fraud-red-flag-clause"
            />
            <p data-testid="fraud-red-flag-coverage" className="mt-[10px] text-[10px] text-faint">
              {data.claimsWithInsight} of {data.claimsInScope} claims in this
              portfolio have a cached fraud narrative
              {range === null ? "" : `, generated ${range}`}. Clauses are grouped
              on their exact text, so a count of one is ordinary — this is a
              reading aid over what the model wrote, not a classification.
              {data.truncated
                ? ` Showing ${String(data.limit)} of ${String(data.totalClauses)} distinct clauses.`
                : ""}
              {/* Named rather than hidden: a row this build could not read is a
                  fact about the cache, and a coverage figure that quietly
                  excluded it would be the one number on this card that must not
                  flatter itself. */}
              {data.unreadable === 0
                ? ""
                : ` ${String(data.unreadable)} cached narratives could not be read and are excluded.`}
            </p>
          </>
        )}
      </InsightShell>

      {/* Nothing follows the shell, and the two paragraphs that used to are the
          reason this comment exists.

          One was a second coverage line correcting the shell's default sentence
          instead of replacing it; `emptyMessage` above replaces it, so the empty
          card is one paragraph saying one thing. The other was an `sr-only`
          "Generated —" whose own comment called it "deliberately visible" —
          prose and code disagreeing about the same element, which is a
          reviewer's coin toss over which was meant. It is gone rather than
          un-hidden: `InsightShell` already omits the provenance line on a
          not-generated card *because* there is nothing to date, and a hidden row
          reading "Generated —" announced the opposite of that decision to the
          one reader who could not see it was hidden. What this card is and is
          not is stated in the coverage caption, visibly, to everybody. */}
    </div>
  );
}
