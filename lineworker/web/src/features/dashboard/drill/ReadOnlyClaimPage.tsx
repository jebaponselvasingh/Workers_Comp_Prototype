/**
 * The read-only claim view a drill-through opens (AC 2).
 *
 * `/dashboard/claims/WC-nnnn`. The case header, the risk gauge, the stage
 * stepper, the stage's own facts, its money and its documents — and **no edit
 * affordance of any kind**, because a supervisor reads and a handler writes
 * (AD-7: scope gates visibility, role gates capability).
 *
 * ## Composed from the pure pieces, never flagged
 *
 * The tempting shape is a `ClaimViewMode` context read by `InlineEditField`,
 * `ActionsCard`, `AddInjuryPopover` and `LineItemDialog` — four leaves, one
 * edit, done. It is the wrong shape twice over. It puts a mode flag on the
 * handler workspace's hottest components, so every *future* edit affordance is
 * correct only if its author remembers the flag; and it makes "no edit
 * controls" a property of a boolean rather than of the tree, which means the
 * test that proves AC 2 is really testing a prop.
 *
 * So this view is built from the presentational half of Epic 2/3's case file —
 * `CaseHeader`, `RiskGauge` (inside it), `StageStepper`, `Cards.tsx`'s
 * `CaseCard`/`Kv`/`CardGrid`/`CostBar`, `reserveAccent` and `DocumentList` —
 * and **`overview/*.tsx` is deliberately not reused**: `IntakeOverview` and
 * `InvestigationOverview` call `useInlineEdits` unconditionally, and
 * `TreatmentOverview` and `SettledOverview` render action controls. Reusing
 * them would mean editing them. Nothing under `features/claim-detail/` is
 * touched by this story, and read-only here means the controls are
 * **structurally absent** rather than hidden behind a flag — which is what lets
 * a test assert a property of the rendered tree.
 *
 * ## One request, and deliberately not the financials one
 *
 * `useClaimDetail` and nothing else. The money on this page comes off the
 * stage variant the case-file payload already carries.
 * `GET /claims/{id}/financials` is **not** usable here: its own docstring says
 * "This GET can write", because it refreshes `payment_schedule_week` before
 * reading it — and a read-only surface triggering a write is exactly the thing
 * this view exists to make impossible. `FinancialSummaryCard` is fed by that
 * endpoint, which is why it is absent too.
 *
 * ## Four states, and 404 is an answer
 *
 * `ClaimDetailPane`'s branch order: loading, not-found, error, content. A 404
 * means the claim is not in this caller's book — or does not exist, and the
 * server deliberately does not say which — so it renders an in-app not-found
 * state and **leaves the URL as typed**. Redirecting would rewrite the address
 * under somebody trying to work out why their link was wrong, and would also
 * be the console telling them which claim ids are real.
 */
import { useState } from "react";

import { Link, useLocation, useParams } from "react-router";

import { useClaimDetail, type ClaimDetail, type StageOverview } from "@/api/claims";
import { isNotFound } from "@/api/errors";
import { DASHBOARD_ROUTE } from "@/features/shell/routes";
import { formatCents } from "@/lib/money";

import { CaseHeader } from "../../claim-detail/CaseHeader";
import { CardGrid, CaseCard, CostBar, CostLegendItem, Kv, formatDate } from "../../claim-detail/Cards";
import { DocumentList } from "../../claim-detail/documents/DocumentList";
import { DocumentViewerDialog } from "../../claim-detail/documents/DocumentViewerDialog";
import {
  DISABILITY_LABEL,
  RECOVERY_LABEL,
  RESERVE_VERDICT_LABEL,
  RETURN_STATUS_LABEL,
  RISK_LABEL,
} from "../../claim-detail/labels";
import { VERDICT_ACCENT } from "../../claim-detail/reserveAccent";
import { StageStepper } from "../../claim-detail/StageStepper";

import { DRILL_LIST_PATH, type DrillOrigin } from "./filters";

/**
 * The stage's own facts, one card, four variants.
 *
 * A `switch` over `stageVariant` rather than four optional blocks, which is the
 * shape the payload's discriminated union was built for (`ClaimDetailResponse`)
 * — and it is exhaustive by type, so a fifth stage would fail to compile here
 * rather than render a blank card.
 *
 * Each variant shows what its own payload carries and nothing more. The intake
 * variant has no paid columns at all, so it shows none; the settled variant
 * carries four, so it shows four. Inventing a zero for an absent figure would
 * be the client asserting a fact the server did not send.
 */
function OverviewFacts({ overview }: { overview: StageOverview }) {
  switch (overview.stageVariant) {
    case "intake":
      return (
        <CaseCard title="Intake" testId="readonly-overview">
          <Kv label="Worker">{overview.workerName}</Kv>
          <Kv label="Role">{overview.workerRole}</Kv>
          <Kv label="Plant">{overview.plant}</Kv>
          <Kv label="Date of injury">{formatDate(overview.doi)}</Kv>
          <Kv label="FROI filed">{formatDate(overview.froiDate)}</Kv>
          <Kv label="Handler">{overview.handlerName}</Kv>
          <Kv label="Injury">{overview.injuryType}</Kv>
          <Kv label="Body part">{overview.bodyPart}</Kv>
          <Kv label="Severity">{RISK_LABEL[overview.risk]}</Kv>
        </CaseCard>
      );
    case "investigation":
      return (
        <CaseCard title="Investigation" testId="readonly-overview">
          <Kv label="Injury">{overview.injuryType}</Kv>
          <Kv label="Cause">{overview.cause}</Kv>
          <Kv label="Body part">{overview.bodyPart}</Kv>
          <Kv label="ICD-10">{overview.icd}</Kv>
          <Kv label="Disability">{DISABILITY_LABEL[overview.disability]}</Kv>
          <Kv label="Recovery window">{RECOVERY_LABEL[overview.recovery]}</Kv>
          <Kv label="Policy">{overview.policyNum}</Kv>
          <Kv label="Severity">{RISK_LABEL[overview.risk]}</Kv>
        </CaseCard>
      );
    case "treatment":
      return (
        <CaseCard title="Treatment" testId="readonly-overview">
          <Kv label="Phase note">{overview.phaseNote}</Kv>
          <Kv label="Recovery window">{RECOVERY_LABEL[overview.recovery]}</Kv>
          <Kv label="Return status">{RETURN_STATUS_LABEL[overview.returnStatus]}</Kv>
          <Kv label="Coordination">{overview.coordinationNote}</Kv>
          <Kv label="Handler">{overview.handlerName}</Kv>
        </CaseCard>
      );
    case "settled":
      return (
        <CaseCard title="Settlement" testId="readonly-overview">
          <Kv label="Settled">{formatDate(overview.settlementDate)}</Kv>
          <Kv label="Disability">{DISABILITY_LABEL[overview.disability]}</Kv>
          <Kv label="Return status">{RETURN_STATUS_LABEL[overview.returnStatus]}</Kv>
          <Kv label="Handler">{overview.handlerName}</Kv>
        </CaseCard>
      );
  }
}

/**
 * The money the stage variant already carries, formatted and nothing else.
 *
 * Every figure is read off the payload; none is added to another. The cost bar
 * receives the three percentages `services/derivations/claim_money.py` computed
 * — the two variants that carry a `costSplit` draw it, and the two that do not
 * simply have no bar to draw.
 */
function MoneyCard({ overview }: { overview: StageOverview }) {
  return (
    <CaseCard title="Financial position" testId="readonly-financials">
      {overview.stageVariant === "intake" ? (
        <>
          <Kv label="Average weekly wage" testId="readonly-aww">
            {formatCents(overview.awwCents)}
          </Kv>
          <Kv label="Reserve" testId="readonly-reserve">
            {formatCents(overview.reserveCents)}
          </Kv>
        </>
      ) : overview.stageVariant === "treatment" ? (
        <>
          <Kv label="Paid — indemnity" testId="readonly-paid-indemnity">
            {formatCents(overview.paidIndemnityCents)}
          </Kv>
          <Kv label="Paid — medical" testId="readonly-paid-medical">
            {formatCents(overview.paidMedicalCents)}
          </Kv>
          <Kv label="Reserve" testId="readonly-reserve">
            {formatCents(overview.reserveCents)}
          </Kv>
        </>
      ) : (
        <>
          <Kv label="Total paid" testId="readonly-total-paid">
            {formatCents(overview.totalPaidCents)}
          </Kv>
          <Kv label="Paid — indemnity" testId="readonly-paid-indemnity">
            {formatCents(overview.paidIndemnityCents)}
          </Kv>
          <Kv label="Paid — medical" testId="readonly-paid-medical">
            {formatCents(overview.paidMedicalCents)}
          </Kv>
          <Kv label="Reserve" testId="readonly-reserve">
            {formatCents(overview.reserveCents)}
          </Kv>
          {overview.costSplit !== null && (
            <CostBar
              split={overview.costSplit}
              showExpense={overview.stageVariant === "settled"}
              legend={
                <>
                  <CostLegendItem swatch="bg-steel">Indemnity</CostLegendItem>
                  <CostLegendItem swatch="bg-brand">Medical</CostLegendItem>
                  {overview.stageVariant === "settled" && (
                    <CostLegendItem swatch="bg-faint">Expense</CostLegendItem>
                  )}
                </>
              }
            />
          )}
        </>
      )}
    </CaseCard>
  );
}

/**
 * The reserve adequacy verdict, as a statement rather than as a control.
 *
 * The same token and the same accent map the treatment overview and the Bills
 * summary use, so one claim reads one judgement wherever it is drawn. There is
 * no ✓ and no "adjust reserve" here: the verdict is the server's answer, and
 * acting on it is a handler's capability.
 */
function ReserveVerdict({ claim }: { claim: ClaimDetail }) {
  const accent = VERDICT_ACCENT[claim.reserveCheck.verdict];
  return (
    <CaseCard title="Reserve check" testId="readonly-reserve-check">
      <p
        data-testid="readonly-reserve-verdict"
        data-verdict={claim.reserveCheck.verdict}
        className={`rounded-md border px-2 py-1 text-[11.5px] font-semibold ${accent.box} ${accent.text}`}
      >
        {RESERVE_VERDICT_LABEL[claim.reserveCheck.verdict]}
      </p>
      <p className="mt-2 text-[11px] text-muted-text">{claim.reserveCheck.rationale}</p>
    </CaseCard>
  );
}

function DetailSkeleton() {
  return (
    <div data-testid="readonly-skeleton" aria-hidden className="flex flex-col gap-[10px]">
      <span className="block h-6 w-1/3 animate-pulse rounded bg-surface-2" />
      <span className="block h-4 w-1/2 animate-pulse rounded bg-surface-2" />
      <span className="block h-10 w-full animate-pulse rounded bg-surface-2" />
      <span className="block h-40 w-full animate-pulse rounded bg-surface-2" />
    </div>
  );
}

function BackLink() {
  // Where the caller came from, when the caller said. `useLocation().state` is
  // `null` on a pasted or reloaded URL — the AC 4 path — and the unfiltered
  // list is the honest fallback: a bare claim URL records no narrowing, so
  // inventing one would be a worse answer than the whole book.
  const origin = (useLocation().state as DrillOrigin | null)?.from ?? DRILL_LIST_PATH;
  const toDashboard = origin === DASHBOARD_ROUTE;
  return (
    <Link
      data-testid="readonly-back"
      to={origin}
      className="rounded text-[11px] font-semibold text-steel hover:underline focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
    >
      {toDashboard ? "← Back to dashboard" : "← Back to claims"}
    </Link>
  );
}

function CaseFile({ claimId }: { claimId: string }) {
  const detail = useClaimDetail(claimId);
  /**
   * Which document the viewer is showing, or `null`.
   *
   * The one piece of state on this page, and it opens a **read-only** dialog:
   * `DocumentViewerDialog`'s own docstring says it is read-only structurally —
   * no input, no select, no submit, no mutation hook — and the sheet behind it
   * is a plain GET. So it is not an exception to this view's rule; it is the
   * same rule, on a surface that already obeys it. The alternative was a
   * document list whose rows are buttons that do nothing, which is a worse
   * answer than either rendering them or removing them.
   */
  const [openDocumentId, setOpenDocumentId] = useState<number | null>(null);

  if (detail.isPending) return <DetailSkeleton />;

  if (detail.isError) {
    // A 404 is an answer, not a failure: this claim is not in the caller's book
    // (or does not exist — the server deliberately does not say which, so that a
    // stale link cannot be used to find out whose claims exist). The URL is left
    // exactly as it was typed.
    return isNotFound(detail.error) ? (
      <p data-testid="readonly-not-found" className="text-sm text-muted-text">
        <span className="font-mono">{claimId}</span> is not in this portfolio.
      </p>
    ) : (
      <p role="alert" data-testid="readonly-error" className="text-sm text-error">
        ⚠ <span className="font-mono">{claimId}</span> could not be loaded. Try again in a
        moment.
      </p>
    );
  }

  const claim = detail.data;

  return (
    <>
      {/* The header carries the risk gauge, so `RiskGauge` is reused here by
          being inside the component that already owns it rather than by being
          mounted twice. */}
      <CaseHeader header={claim.header} />
      <StageStepper steps={claim.stepper} />
      <CardGrid>
        <OverviewFacts overview={claim.overview} />
        <MoneyCard overview={claim.overview} />
        <ReserveVerdict claim={claim} />
      </CardGrid>
      <div className="mt-[10px]">
        <DocumentList documents={claim.documents.documents} onOpen={setOpenDocumentId} />
      </div>
      <DocumentViewerDialog
        claimId={claimId}
        documentId={openDocumentId}
        onClose={() => setOpenDocumentId(null)}
      />
    </>
  );
}

export function ReadOnlyClaimPage() {
  const { claimId } = useParams<{ claimId: string }>();

  return (
    <section
      data-testid="readonly-claim"
      aria-label="Claim detail"
      className="flex w-full flex-col gap-[10px]"
    >
      <BackLink />
      {claimId === undefined ? (
        <p data-testid="readonly-none" className="text-sm text-muted-text">
          No claim selected.
        </p>
      ) : (
        <CaseFile key={claimId} claimId={claimId} />
      )}
    </section>
  );
}
