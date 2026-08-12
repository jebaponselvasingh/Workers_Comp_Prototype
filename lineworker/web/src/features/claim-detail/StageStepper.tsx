/**
 * The four-step lifecycle stepper (FR-DET-1, UX-DR5) — always the first
 * element of the Overview tab.
 *
 * **The marks are the server's.** `done` and `current` arrive on each step;
 * this component does not compare the claim's stage against a list to work
 * out which steps are behind it. That is AD-1, and it is also why the
 * connector between two steps is drawn from the *left* step's `done` flag
 * rather than from an index comparison — there is no index arithmetic here
 * at all.
 *
 * The steps are rendered as an ordered list with the current one marked
 * `aria-current="step"`, so the progression is available to a screen reader
 * as structure rather than as three coloured dots.
 */
import type { StepperStep } from "@/api/claims";

import { STAGE_LABEL } from "./labels";

function dotClass(step: StepperStep): string {
  if (step.current) return "bg-brand ring-4 ring-brand-soft";
  if (step.done) return "bg-ok";
  return "bg-border";
}

export function StageStepper({ steps }: { steps: StepperStep[] }) {
  return (
    <ol
      data-testid="stage-stepper"
      aria-label="Claim lifecycle"
      className="mb-[10px] flex items-center rounded-lg border border-border bg-surface px-3 py-[10px]"
    >
      {steps.map((step, index) => (
        <li
          key={step.stage}
          data-testid="stepper-step"
          data-stage={step.stage}
          data-state={step.current ? "current" : step.done ? "done" : "upcoming"}
          aria-current={step.current ? "step" : undefined}
          className="flex flex-1 items-center last:flex-none"
        >
          <span className="flex items-center gap-[6px]">
            <span aria-hidden className={`block size-2 rounded-full ${dotClass(step)}`} />
            <span
              className={`text-[11px] ${
                step.current
                  ? "font-bold text-text"
                  : step.done
                    ? "text-muted-text"
                    : "text-faint"
              }`}
            >
              {STAGE_LABEL[step.stage]}
            </span>
          </span>
          {index < steps.length - 1 && (
            <span
              aria-hidden
              // Drawn from this step's own `done` flag: the connector says
              // "the claim has left this step", which is exactly what `done`
              // means. Reading the next step's flag would leave the segment
              // into the current step unfilled.
              className={`mx-2 block h-px flex-1 ${step.done ? "bg-ok" : "bg-border"}`}
            />
          )}
        </li>
      ))}
    </ol>
  );
}
