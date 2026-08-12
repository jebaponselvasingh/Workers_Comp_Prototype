/**
 * The Injury Diagram tab (Story 2.4, UX-DR6) — the prototype's `injHTML`.
 *
 * Two columns: the body map with its body-part select and ICD caption on the
 * left, five cards on the right. Story 2.2 shipped this tab as an explicit
 * empty state naming this story; that seam is gone from `DetailTabs` and
 * this is what replaced it.
 *
 * **The body-part select is 2.3's command, not a second one** (AD-12: one
 * write path). Changing the region here goes through the same
 * `PATCH /claims/{id}` that the investigation overview's select uses, with
 * the same optimistic scalar, the same 409 handling and the same inline
 * refusal — which is why the select is an `InlineEditField` rather than a
 * bare `<select>` wired to a fetch.
 *
 * **The severity score is a different route**, `PATCH /claims/{id}/severity`,
 * because the patch whitelist is machinery for text — see
 * `services/claims/edit.py`. From the handler's side the two are
 * indistinguishable, which is the point.
 *
 * **Both vocabularies are on screen at once here, and they disagree.** The
 * select's labels are the diagram's ("Right Hand"); `header.bodyPart` is the
 * carrier's own wording ("Wrist(s) & Hand(s)") until the moment somebody
 * edits it, at which point the server rewrites it from the diagram's list.
 * That is the prototype's behaviour, ported deliberately, and this tab is
 * where it becomes visible — recorded in `deferred-work.md` as a product
 * decision rather than resolved by inventing a mapping the dataset does not
 * contain.
 *
 * **The layout is `.blayout`'s**: `grid-template-columns: 180px 1fr` above
 * the breakpoint, one column below it. The prototype has no responsive rule
 * at all (it is a desktop console), so the stacked form is this port's
 * addition rather than a deviation from something it specifies.
 */
import type { ClaimDetail } from "@/api/claims";

import { CaseCard } from "../Cards";
import { InlineEditField } from "../InlineEditField";
import { useInlineEdits } from "../useInlineEdits";
import { AddInjuryPopover } from "./AddInjuryPopover";
import { BodyMap } from "./BodyMap";
import { SeverityCard } from "./SeverityCard";

/** The prognosis card's four rows, in the prototype's order. */
const PROGNOSIS_ROWS = [
  { key: "mmi", label: "MMI estimate" },
  { key: "rtw", label: "RTW outlook" },
  { key: "impairment", label: "Impairment" },
  { key: "litigation", label: "Litigation risk" },
] as const;

export function InjuryTab({ claim }: { claim: ClaimDetail }) {
  const edits = useInlineEdits(claim);
  const { injury, header } = claim;

  return (
    <div
      className="grid gap-3 lg:grid-cols-[180px_1fr] lg:items-start"
      data-testid="injury-tab"
    >
      <section className="relative flex flex-col items-center gap-[5px] rounded-lg border border-border bg-surface p-3">
        <h3 className="self-start font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase">
          Injury location{" "}
          <span className="font-sans text-[9px] font-normal tracking-normal normal-case text-faint">
            (editable)
          </span>
        </h3>

        <BodyMap markers={injury.markers} />

        <div className="w-full">
          <InlineEditField
            field="bodyKey"
            label="Body part"
            value={header.bodyKey}
            options={claim.editOptions.bodyParts.map((option) => ({
              value: option.key,
              label: option.label,
            }))}
            saving={edits.busy}
            feedback={edits.feedbackFor("bodyKey")}
            onCommit={(next) => edits.commit({ bodyKey: next })}
          />
        </div>
        <p
          data-testid="injury-icd-caption"
          className="font-mono text-[9.5px] text-faint"
        >
          {injury.icd}
        </p>

        <AddInjuryPopover claim={claim} />
      </section>

      {/* `.binfo` is a single column of stacked cards, not a grid: the
          prognosis card already has two columns *inside* it, and a second
          level of columns around it is how a dense console stops being
          readable. */}
      <div className="flex min-w-0 flex-col gap-[9px]">
        <SeverityCard claim={claim} />

        <CaseCard title="ICD-10 diagnosis" testId="injury-icd">
          <p className="font-mono text-[13px] font-bold text-text">
            {injury.icd}
          </p>
          <p className="text-[11px] text-muted-text">{injury.icdDesc}</p>
        </CaseCard>

        <CaseCard title="Prognosis" testId="injury-prognosis">
          <dl className="grid grid-cols-2 gap-2">
            {PROGNOSIS_ROWS.map((row) => (
              <div
                key={row.key}
                className="rounded border border-hairline p-[6px]"
              >
                <dt className="text-[9px] tracking-[0.3px] text-faint uppercase">
                  {row.label}
                </dt>
                <dd
                  data-testid={`injury-prognosis-${row.key}`}
                  className="text-[11.5px] text-text"
                >
                  {injury.prognosis[row.key]}
                </dd>
              </div>
            ))}
          </dl>
        </CaseCard>

        <CaseCard title="Treatment plan" testId="injury-treatment-plan">
          {/* No empty branch: `treatment_plan_step` is seeded for every
                claim in the portfolio and the migration refuses a claim
                without steps, so a card rendering nothing here would be a
                broken seed rather than a state — and `<ol>` with no items
                is already visibly nothing. */}
          <ol className="flex flex-col gap-[5px]">
            {injury.treatmentPlan.map((step) => (
              <li
                key={step.stepNo}
                data-testid="injury-treatment-step"
                className="flex items-baseline gap-2 text-[11.5px] text-text"
              >
                <span className="inline-flex size-[15px] shrink-0 items-center justify-center rounded-full bg-steel-soft font-mono text-[9px] text-steel">
                  {step.stepNo}
                </span>
                <span className="min-w-0">{step.description}</span>
              </li>
            ))}
          </ol>
        </CaseCard>

        <CaseCard
          title={<span className="text-warn">⚠ Restrictions</span>}
          testId="injury-restrictions"
          className="border-warn/30 bg-warn-soft"
        >
          <p className="text-[11.5px] leading-[1.5] text-muted-text">
            {injury.contraindications}
          </p>
        </CaseCard>
      </div>
    </div>
  );
}
