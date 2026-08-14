/**
 * The week-by-week indemnity payment schedule (Story 3.3, AC 2).
 *
 * The prototype's second `billsHTML` card. Rows come from the server already
 * ordered, already dated and already statused — this component renders a
 * table and maps each status token to a label and a chip colour, which is the
 * enum convention's UI half and the whole of its job.
 *
 * **A real `<table>`, unlike everything else on the case file.** The rest of
 * the tab is definition lists and rows of cards, because those are lists of
 * label/value pairs. This is genuinely tabular — four columns, twenty rows,
 * compared down the column — so a screen reader announcing "Week, Period,
 * Amount, Status" for each row is the accessible rendering rather than a
 * concession.
 *
 * **Rows are clickable and open a read-only sheet.** The approve button inside
 * arrives with Story 3.4; until then the sheet states what the week is and
 * what its status means, which is what the prototype's `reviewScheduleWeek`
 * does minus the action.
 */
import type { FinancialSummary, ScheduleWeek } from "@/api/claims";
import { formatCents } from "@/lib/money";

import { formatDate } from "../Cards";
import { SCHEDULE_STATUS_LABEL } from "../labels";
import { CHIP_CLASS, SCHEDULE_STATUS_TONE } from "./statusTone";

export function PaymentScheduleCard({
  schedule,
  summary,
  onOpenWeek,
}: {
  schedule: ScheduleWeek[];
  summary: FinancialSummary;
  onOpenWeek: (weekNo: number) => void;
}) {
  return (
    <section
      data-testid="payment-schedule"
      className="mb-[10px] rounded-lg border border-border bg-surface p-3"
    >
      <h3 className="mb-1 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase">
        📅 Indemnity payment schedule — week by week
      </h3>

      {/* The prototype's sub-heading, which is where the two figures that make
          this card agree with the Overview's paid-vs-reserve row live: both
          render `disbursedIndemnityCents` of `scheduledIndemnityCents` from the
          same server block (AC 4). */}
      <p data-testid="schedule-subheading" className="mb-2 text-[11px] text-muted-text">
        {formatCents(summary.weeklyIndemnityCents)}/wk · {summary.weekCount}-week schedule shown
        · <span data-testid="schedule-disbursed">{formatCents(summary.disbursedIndemnityCents)}</span>{" "}
        disbursed to date of{" "}
        <span data-testid="schedule-scheduled">
          {formatCents(summary.scheduledIndemnityCents)}
        </span>{" "}
        scheduled
        {summary.nextPaymentDue !== null && <> · next due {formatDate(summary.nextPaymentDue)}</>}
      </p>

      {schedule.length === 0 ? (
        /* NFR-3. Unreachable against a materialized claim — the generator
           clamps every schedule to at least four weeks — so this is the state
           for a claim whose rows have not been written rather than one with
           nothing to pay. */
        <p data-testid="schedule-empty" className="text-[11.5px] text-faint">
          No indemnity schedule has been generated for this claim.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[11px]">
            <caption className="sr-only">
              Week-by-week indemnity payment schedule for this claim
            </caption>
            <thead>
              <tr className="text-left text-[10px] tracking-[0.3px] text-muted-text uppercase">
                <th scope="col" className="border-b border-border py-[5px] pr-2 font-semibold">
                  Week
                </th>
                <th scope="col" className="border-b border-border py-[5px] pr-2 font-semibold">
                  Period
                </th>
                <th scope="col" className="border-b border-border py-[5px] pr-2 font-semibold">
                  Amount
                </th>
                <th scope="col" className="border-b border-border py-[5px] font-semibold">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {schedule.map((week) => (
                <tr
                  key={week.weekNo}
                  data-testid="schedule-row"
                  data-week={week.weekNo}
                  data-status={week.status}
                  className="border-b border-hairline last:border-b-0 hover:bg-surface-2"
                >
                  <td className="py-[5px] pr-2">
                    {/* The button carries the click rather than the row: a
                        `<tr onClick>` is unreachable by keyboard and announces
                        nothing, and putting a button in each of four cells
                        would make one week four tab stops. */}
                    <button
                      type="button"
                      data-testid="schedule-week-button"
                      onClick={() => onOpenWeek(week.weekNo)}
                      className="font-mono font-semibold text-brand hover:underline"
                    >
                      Wk {week.weekNo}
                    </button>
                  </td>
                  <td className="py-[5px] pr-2 text-muted-text">
                    {formatDate(week.periodStart)} – {formatDate(week.periodEnd)}
                  </td>
                  <td className="py-[5px] pr-2 font-mono font-semibold">
                    {formatCents(week.amountCents)}
                  </td>
                  <td className="py-[5px]">
                    <span className={`${CHIP_CLASS} ${SCHEDULE_STATUS_TONE[week.status]}`}>
                      {SCHEDULE_STATUS_LABEL[week.status]}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
