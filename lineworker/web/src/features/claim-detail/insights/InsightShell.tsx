/**
 * The frame every AI Insights card is drawn in (Story 6.2, AC 2).
 *
 * One component, four cards, and what it owns is the part that must be
 * identical on all four: the heading, the **generation timestamp and model
 * label**, and the not-generated empty state.
 *
 * **The timestamp is not optional and is not a footnote.** AD-10's rule is that
 * an AI narrative is a cache row rendered with the time it was generated, never
 * a claim column — and the practical form of that rule is that a reader can
 * always see how old the sentence in front of them is. Putting it in the shell
 * rather than in each card means a fifth card cannot ship without one.
 *
 * **There is no edit affordance here and there must never be one** (FR-H-9).
 * The cards render into `Kv` rows and plain text; `EditableRow` and
 * `InlineEditField` — the two components every other tab reaches for — are
 * deliberately not imported anywhere in this directory. `ai_insight` has no
 * `version` column, so there is nothing an inline edit could send back as
 * `expectedVersion`: the absence is structural, and this is the surface where
 * it is visible.
 *
 * **The not-generated state is a first-class card, not a gap** (NFR-3). Every
 * claim is in it until a refresh job reaches the claim, so it says what is
 * true — nothing has been generated yet — beside the affordance that changes
 * that. A spinner would be a lie about work in progress and a missing card
 * would hide a whole section of the case file.
 */
import { formatNotedAt } from "@/lib/clock";

import { CaseCard } from "../Cards";
import { BULLET_CLASS } from "./insightTone";

export function InsightShell({
  title,
  testId,
  status,
  kind,
  model,
  generatedAt,
  children,
}: {
  title: React.ReactNode;
  testId: string;
  status: "ready" | "not_generated";
  /** The server's own token, stamped into the DOM as the card's identity. */
  kind: string;
  model: string | null;
  generatedAt: string | null;
  /** The card's body — rendered only when the narrative exists. */
  children: React.ReactNode;
}) {
  return (
    <CaseCard title={title} testId={testId} className="flex flex-col">
      <div data-testid={`${testId}-body`} data-kind={kind} data-status={status}>
        {status === "not_generated" ? (
          <p data-testid={`${testId}-empty`} className="text-[11.5px] text-faint">
            Not generated yet. Use Refresh above to generate this claim&apos;s insights.
          </p>
        ) : (
          children
        )}
      </div>

      {/* Rendered only on a generated card, because there is nothing to date
          otherwise — and a "generated —" row would read as a failed load.

          `generatedAt` is not defensively re-checked inside this branch, and
          the removal of that check is the point (follow-up review of Story 6.2,
          C7): it rendered an em dash for a state the comment two lines up says
          cannot happen, so the file both claimed the invariant and hedged
          against it. It holds on the server — `CachedInsight.generated_at` is
          non-optional by AD-10's rule that a narrative is always shown with its
          time — and the prop is typed `string | null` only because the
          not-generated slot shares this component. A `!` rather than a silent
          fallback, so a build that broke the invariant fails loudly instead of
          shipping a card dated "—". `model` keeps its check because it is
          genuinely optional in the payload's other state. */}
      {status === "ready" && (
        <p
          data-testid={`${testId}-generated`}
          className="mt-[10px] border-t border-hairline pt-[6px] text-[10px] text-faint"
        >
          Generated {formatNotedAt(generatedAt!)}
          {model === null ? "" : ` · ${model}`}
        </p>
      )}
    </CaseCard>
  );
}

/**
 * A card's list of clauses — takeaways, considerations, red flags, monitoring.
 *
 * The four narratives all end in one of these and they all render the same way,
 * so the markup is written once. The list is exactly what the server sent, in
 * the order it sent it; nothing here sorts, filters or counts it.
 */
export function InsightBullets({
  items,
  testId,
  tone = "text-text",
}: {
  items: string[];
  testId: string;
  tone?: string;
}) {
  return (
    <ul className="mt-[6px] flex flex-col gap-[4px]">
      {items.map((item, index) => (
        <li
          // The clauses have no id on the wire and two can legitimately read
          // alike, so position is the only stable key — and it is stable
          // because the order is the model's answer as stored, which nothing
          // in this component reorders (`TimelineCard`'s reasoning).
          key={`${index}-${item.slice(0, 24)}`}
          data-testid={testId}
          className={`flex items-start gap-[6px] text-[11.5px] ${tone}`}
        >
          <span aria-hidden className={BULLET_CLASS}>
            ·
          </span>
          <span className="min-w-0">{item}</span>
        </li>
      ))}
    </ul>
  );
}
