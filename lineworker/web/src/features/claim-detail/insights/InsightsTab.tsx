/**
 * The AI Insights tab — the sixth tab of the case-file pane (Story 6.2, UX-DR5).
 *
 * Story 2.2 shipped this tab as a dashed-border seam reading "AI insights arrive
 * with the copilot"; that seam is gone from `DetailTabs` and this is what
 * replaced it. It was the last one, which is why `SEAMS` and `SeamPanel` went
 * with it.
 *
 * Four read-only cards in a two-column grid, each carrying its own generation
 * timestamp and the model that wrote it (AD-10). Everything on them was decided
 * on the server: the figures by `services/financials`, `services/worklist`,
 * `services/derivations` and `services/rag`, the prose by a local model
 * constrained to a schema with no field a figure could be written into (AD-2),
 * and the fraud card's variant by two registered derivations. This component
 * renders; it does not derive.
 *
 * **This tab fetches its own payload**, `BillsTab`'s call and for its reasons
 * twice over: four narratives are far more than the Overview needs, and the tab
 * is mounted only while selected, so the request is paid for by the reader who
 * asked for it.
 *
 * ## Three states, and the third is the interesting one
 *
 * Loading is a skeleton, an error is a `role="alert"` sentence (UX-DR11), and
 * **"not generated yet" is neither** — it is a 200 with four explicit empty
 * cards and a Refresh button, because it is the state every claim is in until a
 * scheduled run reaches it. NFR-3 asks for that distinction by name: a claim
 * whose insights have not been generated has not failed at anything.
 *
 * ## No edit affordance anywhere on this tab, and that is structural
 *
 * `EditableRow` and `InlineEditField` are not imported in this directory and
 * must not be (FR-H-9, AD-10). `ai_insight` carries no `version` column, so
 * there is nothing an inline edit could send back as `expectedVersion` — the
 * schema, the API and this tab all say the same thing, and the honest way to
 * change a narrative is to regenerate it.
 */
import { useClaimInsights, useRefreshInsights } from "@/api/claims";

import { CardGrid } from "../Cards";
import { INSIGHT_KIND_LABEL } from "../labels";
import { RefreshInsightsButton } from "./RefreshInsightsButton";
import { FraudRiskInsightCard } from "./FraudRiskInsightCard";
import { InsightShell } from "./InsightShell";
import { NextBestActionsInsightCard } from "./NextBestActionsInsightCard";
import { ReserveAdequacyInsightCard } from "./ReserveAdequacyInsightCard";
import { SimilarCaseInsightCard } from "./SimilarCaseInsightCard";

/**
 * How a failed refresh reads to a handler, whatever the transport actually did.
 *
 * A written sentence rather than `refresh.error.message` (review of Story 6.2,
 * M16). `api/client.ts` **synthesises** the four RFC 9457 members for any
 * response that carried no problem envelope — a proxy timeout, a gateway 502,
 * an expired session — and the synthesised `detail` reads "The server answered
 * 504.". Rendering that put machine text where a sentence belongs, and
 * `api/errors.ts` warns about it in as many words. The server's own 503 detail
 * *is* a good sentence, but a branch that renders it sometimes is a branch that
 * renders the machine text the rest of the time.
 *
 * Not exported: a non-component export from a component module trips the
 * fast-refresh lint rule, and no other file needs it — the tests assert the
 * text, which is what a reader sees anyway.
 */
const REFRESH_FAILED =
  "⚠ These insights could not be regenerated. Try again in a moment.";

function InsightsSkeleton() {
  return (
    <div
      data-testid="insights-skeleton"
      aria-hidden
      className="grid gap-[10px] lg:grid-cols-2"
    >
      <span className="block h-44 w-full animate-pulse rounded-lg bg-surface-2" />
      <span className="block h-44 w-full animate-pulse rounded-lg bg-surface-2" />
      <span className="block h-44 w-full animate-pulse rounded-lg bg-surface-2" />
      <span className="block h-44 w-full animate-pulse rounded-lg bg-surface-2" />
    </div>
  );
}

export function InsightsTab({ claimId }: { claimId: string }) {
  const insights = useClaimInsights(claimId);
  const refresh = useRefreshInsights(claimId);

  if (insights.isPending) return <InsightsSkeleton />;

  if (insights.isError) {
    // No 404 branch, `BillsTab`'s reason: this tab is only reachable from a
    // case file that has already loaded, so "not in this caseload" is not a
    // state a handler can arrive at here — and if the claim vanished under
    // them, the pane behind this panel says so.
    //
    // **A Refresh button, not just a sentence** (review of Story 6.2, M3). The
    // most likely cause of a failed read on this tab is a stored narrative the
    // server can no longer validate against its schema, and regenerating is
    // precisely what replaces that row — so an error branch with no way out
    // left a handler looking at a permanent-seeming failure they could have
    // fixed with one click.
    return (
      <div
        data-testid="insights-error-panel"
        className="flex min-w-0 flex-col gap-[10px]"
      >
        <div className="flex items-start justify-between gap-3">
          <p
            role="alert"
            data-testid="insights-error"
            className="text-[11.5px] text-error"
          >
            ⚠ This claim&apos;s AI insights could not be loaded. Try again in a
            moment.
          </p>
          <RefreshInsightsButton
            onRefresh={() => refresh.mutate()}
            isPending={refresh.isPending}
          />
        </div>
        {refresh.isError && (
          <p
            role="alert"
            data-testid="insights-refresh-error"
            className="text-[11px] font-semibold text-error"
          >
            {REFRESH_FAILED}
          </p>
        )}
      </div>
    );
  }

  const {
    similarCaseOutcomes,
    reserveAdequacyReview,
    nextBestActions,
    fraudRiskIndicators,
  } = insights.data;

  return (
    <div data-testid="insights-tab" className="flex min-w-0 flex-col">
      <div className="mb-[10px] flex items-center justify-between gap-3">
        <p className="text-[10.5px] text-faint">
          AI-generated context, cached per claim. Figures are quoted from this
          console&apos;s own calculations; the narrative is not a substitute for
          the case file.
        </p>
        <RefreshInsightsButton
          onRefresh={() => refresh.mutate()}
          isPending={refresh.isPending}
        />
      </div>

      {refresh.isError && (
        <p
          role="alert"
          data-testid="insights-refresh-error"
          className="mb-[10px] text-[11px] font-semibold text-error"
        >
          {REFRESH_FAILED}
        </p>
      )}

      {refresh.isSuccess && refresh.data.failedKinds.length !== 0 && (
        // A partial refresh — some cards regenerated, some refused. Not an
        // error: the cards that were written are new and the ones that were not
        // are still showing their previous generation. Saying which is the whole
        // of M7: without it a handler watched one card stay unchanged and had no
        // way to tell a refused kind from a button that did nothing.
        <p
          role="status"
          data-testid="insights-partial"
          className="mb-[10px] text-[11px] text-warn"
        >
          {refresh.data.failedKinds.length === 1
            ? `The model could not regenerate ${INSIGHT_KIND_LABEL[refresh.data.failedKinds[0]]}. It still shows its previous generation.`
            : `The model could not regenerate ${refresh.data.failedKinds.length} of these cards. They still show their previous generation.`}
        </p>
      )}

      <CardGrid>
        <InsightShell
          title={INSIGHT_KIND_LABEL.similar_case_outcomes}
          testId="insight-similar"
          kind={similarCaseOutcomes.kind}
          status={similarCaseOutcomes.status}
          model={similarCaseOutcomes.model}
          generatedAt={similarCaseOutcomes.generatedAt}
        >
          {similarCaseOutcomes.content !== null && (
            <SimilarCaseInsightCard content={similarCaseOutcomes.content} />
          )}
        </InsightShell>

        <InsightShell
          title={INSIGHT_KIND_LABEL.reserve_adequacy_review}
          testId="insight-reserve"
          kind={reserveAdequacyReview.kind}
          status={reserveAdequacyReview.status}
          model={reserveAdequacyReview.model}
          generatedAt={reserveAdequacyReview.generatedAt}
        >
          {reserveAdequacyReview.content !== null && (
            <ReserveAdequacyInsightCard
              content={reserveAdequacyReview.content}
            />
          )}
        </InsightShell>

        <InsightShell
          title={INSIGHT_KIND_LABEL.next_best_actions}
          testId="insight-actions"
          kind={nextBestActions.kind}
          status={nextBestActions.status}
          model={nextBestActions.model}
          generatedAt={nextBestActions.generatedAt}
        >
          {nextBestActions.content !== null && (
            <NextBestActionsInsightCard content={nextBestActions.content} />
          )}
        </InsightShell>

        <InsightShell
          title={INSIGHT_KIND_LABEL.fraud_risk_indicators}
          testId="insight-fraud"
          kind={fraudRiskIndicators.kind}
          status={fraudRiskIndicators.status}
          model={fraudRiskIndicators.model}
          generatedAt={fraudRiskIndicators.generatedAt}
        >
          {fraudRiskIndicators.content !== null && (
            <FraudRiskInsightCard content={fraudRiskIndicators.content} />
          )}
        </InsightShell>
      </CardGrid>
    </div>
  );
}
