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
 *
 * **…and what that state *says* is the caller's, because the default sentence
 * names a control.** "Use Refresh above to generate this claim's insights" is
 * true on the four cards this shell was written for, all of which sit on a claim
 * beside that button. Story 7.1's portfolio-wide fraud card has no Refresh — this
 * surface reads the cache and `services/rag` owns the writes (AD-12) — and it is
 * not about a claim, it is about a hundred of them; it rendered the default
 * anyway, told an analyst to use an affordance that is not on the page, and then
 * contradicted itself in a second paragraph underneath. So `emptyMessage` is an
 * optional prop defaulting to the per-claim sentence: no existing caller changes,
 * and a caller whose empty state is a different fact says so in one place instead
 * of appending a correction to a wrong one.
 */
import { formatNotedAt } from "@/lib/clock";

import { CaseCard } from "../Cards";
import { BULLET_CLASS } from "./insightTone";

/**
 * What a not-generated card says when its caller does not say otherwise.
 *
 * The four per-claim cards' sentence, and the default rather than a required
 * prop so that adding the option changed none of them. It names the Refresh
 * button because on a claim there *is* one, two components up.
 */
const CLAIM_EMPTY_MESSAGE = "Not generated yet. Use Refresh above to generate this claim's insights.";

export function InsightShell({
  title,
  testId,
  status,
  kind,
  model,
  generatedAt,
  emptyMessage = CLAIM_EMPTY_MESSAGE,
  children,
}: {
  title: React.ReactNode;
  testId: string;
  status: "ready" | "not_generated";
  /** The server's own token, stamped into the DOM as the card's identity. */
  kind: string;
  model: string | null;
  generatedAt: string | null;
  /**
   * The whole of the not-generated state, for a caller whose empty is a
   * different fact from "this claim has not been analysed yet".
   *
   * One sentence rather than an addition to the default one: two paragraphs
   * disagreeing about what the empty card means is the state this prop exists to
   * remove, not a shape it should make easier.
   */
  emptyMessage?: string;
  /** The card's body — rendered only when the narrative exists. */
  children: React.ReactNode;
}) {
  return (
    <CaseCard title={title} testId={testId} className="flex flex-col">
      <div data-testid={`${testId}-body`} data-kind={kind} data-status={status}>
        {status === "not_generated" ? (
          <p data-testid={`${testId}-empty`} className="text-[11.5px] text-faint">
            {emptyMessage}
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
