/**
 * The "Next best actions" card (Story 6.2, AC 2).
 *
 * The rows are the checklist `services/worklist` generated, in the order it
 * ranked them, capped where it capped them — the *same list* the Overview tab's
 * "⏰ Upcoming actions required" card renders, narrated. Nothing here re-ranks,
 * re-cuts or re-labels it; the urgency chip reuses `ACTION_URGENCY_TONE` so the
 * two surfaces cannot show one row in two colours.
 *
 * **No controls.** The checklist card offers a "go to" and a completion button
 * per row; this one offers neither, and the difference is deliberate rather
 * than unfinished. The server's insight content carries only `id`, `key`,
 * `label` and `urgency` — no `target`, no `enabled`, no `command` — precisely
 * so an AI card cannot grow a second, unaudited path into the three completion
 * commands. A handler acts from the checklist; they read here.
 */
import type { NextBestActionsContent } from "@/api/claims";

import { ACTION_CHIP_CLASS, ACTION_URGENCY_TONE } from "../actions/actionTone";
import { ACTION_URGENCY_LABEL } from "../labels";
import { InsightBullets } from "./InsightShell";

export function NextBestActionsInsightCard({ content }: { content: NextBestActionsContent }) {
  return (
    <>
      <p data-testid="insight-actions-summary" className="text-[11.5px] text-text">
        {content.narrative.summary}
      </p>

      {content.actions.length === 0 ? (
        <p data-testid="insight-actions-none" className="mt-[8px] text-[11.5px] text-faint">
          Nothing is outstanding on this claim.
        </p>
      ) : (
        <ul className="mt-[8px] flex flex-col">
          {content.actions.map((action) => (
            <li
              key={action.id}
              data-testid="insight-action"
              data-action={action.id}
              className="flex items-center gap-2 border-b border-hairline py-[5px] last:border-b-0"
            >
              <span
                data-testid="insight-action-urgency"
                data-urgency={action.urgency}
                className={`${ACTION_CHIP_CLASS} ${ACTION_URGENCY_TONE[action.urgency]}`}
              >
                {ACTION_URGENCY_LABEL[action.urgency]}
              </span>
              <span className="min-w-0 text-[11.5px] text-text">{action.label}</span>
            </li>
          ))}
        </ul>
      )}

      <InsightBullets
        items={content.narrative.considerations}
        testId="insight-actions-consideration"
      />
    </>
  );
}
