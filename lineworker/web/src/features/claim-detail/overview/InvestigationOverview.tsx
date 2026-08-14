/**
 * The investigation variant: the **editable** injury card (Story 2.3) and the
 * read-only financials/reserve card.
 *
 * Seven fields go through `PATCH /claims/{id}` — an audited,
 * compare-and-swapped command (AD-4) — and the card's whole job here is to
 * turn one committed value into one mutation. The interaction, the
 * per-field feedback and the one-commit-at-a-time rule all live in
 * `useInlineEdits`, which the treatment overview shares.
 *
 * **The ICD-10 code and its description travel together.** The command
 * refuses one without the other, because a code corrected while its
 * description still names the previous diagnosis is a silently wrong claim
 * file. Either row sends both; the unchanged half is dropped server-side, so
 * the audit diff still records only what moved.
 *
 * **One thing this variant still does not show.** The prototype embeds an
 * "Upcoming actions required" worklist below the benefit card; it is Story
 * 3.5's. It is omitted rather than stubbed: an empty actions panel would read
 * as "no outstanding actions", which is a statement about the claim this
 * story cannot make. The benefit card beside it arrived with Story 3.1.
 */
import type {
  ClaimDetail,
  Disability,
  InvestigationOverviewData,
  RecoveryWindow,
} from "@/api/claims";
import { formatCents } from "@/lib/money";

import { BenefitCard } from "../BenefitCard";
import {
  CardGrid,
  CaseCard,
  CostBar,
  CostLegendItem,
  Kv,
  RISK_TEXT,
  TimelineCard,
} from "../Cards";
import { EditableRow } from "../EditableRow";
import { DISABILITY_LABEL, RECOVERY_LABEL } from "../labels";
import { useInlineEdits } from "../useInlineEdits";

export function InvestigationOverview({
  claim,
  overview,
}: {
  /** The whole case file: the edit needs its `version` and its vocabularies. */
  claim: ClaimDetail;
  overview: InvestigationOverviewData;
}) {
  const edits = useInlineEdits(claim);

  return (
    <>
      <CardGrid>
        <CaseCard
          testId="investigation-injury"
          title={
            <>
              Injury — manufacturing context{" "}
              <span className="font-sans text-[9.5px] font-normal tracking-normal normal-case text-faint">
                (editable)
              </span>
            </>
          }
        >
          <EditableRow
            edits={edits}
            field="injuryType"
            label="Injury type"
            value={overview.injuryType}
            width={160}
            onCommit={(next) => edits.commit({ injuryType: next })}
          />
          <EditableRow
            edits={edits}
            field="cause"
            label="Cause"
            value={overview.cause}
            width={160}
            onCommit={(next) => edits.commit({ cause: next })}
          />
          {/* Keyed on `bodyKey`, labelled from the server's vocabulary — the
              stored `bodyPart` wording and the diagram's labels are two
              different vocabularies, so the select cannot be built from the
              value it displays elsewhere. */}
          <EditableRow
            edits={edits}
            field="bodyKey"
            label="Body part"
            value={claim.header.bodyKey}
            options={claim.editOptions.bodyParts.map((option) => ({
              value: option.key,
              label: option.label,
            }))}
            onCommit={(next) => edits.commit({ bodyKey: next })}
          />
          {/* The code and its description are one fact, and the command
              refuses one without the other — so each row sends both, and the
              unchanged half is dropped server-side. */}
          <EditableRow
            edits={edits}
            field="icd"
            label="ICD-10"
            value={overview.icd}
            width={100}
            onCommit={(next) => edits.commit({ icd: next, icdDesc: overview.icdDesc })}
          />
          <EditableRow
            edits={edits}
            field="icdDesc"
            label="ICD-10 description"
            value={overview.icdDesc}
            width={200}
            onCommit={(next) => edits.commit({ icd: overview.icd, icdDesc: next })}
          />
          <EditableRow
            edits={edits}
            field="disability"
            label="Disability"
            value={overview.disability}
            options={claim.editOptions.disabilities.map((value) => ({
              value,
              label: DISABILITY_LABEL[value],
            }))}
            // The cast is the seam between an HTML select (which yields a
            // `string`) and the server's vocabulary. Sound here because the
            // options *are* that vocabulary, and the command re-validates.
            onCommit={(next) => edits.commit({ disability: next as Disability })}
          />
          <EditableRow
            edits={edits}
            field="recovery"
            label="Recovery window"
            value={overview.recovery}
            options={claim.editOptions.recoveryWindows.map((value) => ({
              value,
              label: RECOVERY_LABEL[value],
            }))}
            onCommit={(next) => edits.commit({ recovery: next as RecoveryWindow })}
          />
          {/* Not editable: the average weekly wage is Story 3.1's, where it
              is an input to the statutory benefit calculation rather than a
              number to correct in place. */}
          <Kv label="AWW">{formatCents(overview.awwCents)}</Kv>
        </CaseCard>

        <CaseCard title="Financials & reserve" testId="investigation-financials">
          {/* "Total incurred" is the prototype's label for the sum of the
              three paid columns. The wire field is named for what it adds up
              (`totalPaidCents`) and the label is the prototype's — the
              discrepancy is recorded in the story's Dev Agent Record. */}
          <Kv label="Total incurred" testId="investigation-total">
            {overview.costSplit === null ? "Active" : formatCents(overview.totalPaidCents)}
          </Kv>
          <Kv label="Reserve">{formatCents(overview.reserveCents)}</Kv>
          <Kv label="Policy number">{overview.policyNum}</Kv>
          {/* Uncoloured, unlike the prototype: it tints this figure at 55 and
              35, two cut-offs that appear in no rule document. See
              `CaseHeader`. */}
          <Kv label="Fraud score">{overview.fraudScore}/100</Kv>
          <Kv
            label="Severity score"
            testId="investigation-severity"
            valueClassName={`font-bold ${RISK_TEXT[overview.risk]}`}
          >
            {overview.severityScore}/100
          </Kv>

          {overview.costSplit === null ? (
            <p data-testid="investigation-unpaid" className="pt-2 text-[11px] text-faint">
              Active — payments pending. Reserve: {formatCents(overview.reserveCents)}
            </p>
          ) : (
            <CostBar
              split={overview.costSplit}
              legend={
                <>
                  <CostLegendItem swatch="bg-steel">
                    Indemnity {formatCents(overview.paidIndemnityCents)}
                  </CostLegendItem>
                  <CostLegendItem swatch="bg-brand">
                    Medical {formatCents(overview.paidMedicalCents)}
                  </CostLegendItem>
                </>
              }
            />
          )}
        </CaseCard>
      </CardGrid>

      {/* The prototype puts the benefit card between the two-column grid and
          the timeline on this variant and on treatment (`benefitCardHTML` is
          called from both). */}
      <div className="mb-[10px]">
        <BenefitCard claim={claim} />
      </div>

      <TimelineCard title="Case timeline" entries={overview.timeline} />
    </>
  );
}
