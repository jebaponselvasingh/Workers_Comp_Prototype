/**
 * The medical-bills and expenses cards (Story 3.3, AC 3).
 *
 * The prototype's third and fourth `billsHTML` cards, which are the same card
 * over two lists — so this is one component used twice rather than two that
 * drift. What differs between them is the heading, the empty state and the
 * category vocabulary; none of that is worth a second implementation of a row.
 *
 * **The three figures in the heading are the server's** ("6 on file ($4,120 of
 * $23,400 paid)"): counting the rows and summing the paid ones in the browser
 * is exactly the arithmetic AD-1 keeps out of components, and it is the kind
 * that goes wrong quietly the first time anything is filtered.
 *
 * **The expenses card has an empty state and the bills card does not need
 * one** — but both get the branch, because "this claim has no expenses on
 * file" is a fact worth rendering (NFR-3) and a card that showed a heading
 * over nothing reads as a loading state that never finished.
 */
import type { BillLine, ExpenseLine } from "@/api/claims";
import { formatCents } from "@/lib/money";

import { LINE_ITEM_STATUS_LABEL } from "../labels";
import { CHIP_CLASS, LINE_ITEM_STATUS_TONE } from "./statusTone";

export type AnyLineItem = BillLine | ExpenseLine;

export function LineItemsCard({
  title,
  icon,
  items,
  count,
  totalCents,
  paidCents,
  emptyMessage,
  testId,
  onOpen,
}: {
  title: string;
  icon: string;
  items: AnyLineItem[];
  count: number;
  totalCents: number;
  paidCents: number;
  emptyMessage: string;
  testId: string;
  onOpen: (id: number) => void;
}) {
  return (
    <section
      data-testid={testId}
      className="mb-[10px] rounded-lg border border-border bg-surface p-3 last:mb-0"
    >
      <h3 className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase">
        {icon} {title} —{" "}
        <span data-testid={`${testId}-heading-figures`}>
          {count} on file ({formatCents(paidCents)} of {formatCents(totalCents)} paid)
        </span>
      </h3>

      {items.length === 0 ? (
        <p data-testid={`${testId}-empty`} className="text-[11.5px] text-faint">
          {emptyMessage}
        </p>
      ) : (
        <ul className="flex flex-col">
          {items.map((item) => (
            <li
              key={item.id}
              data-testid={`${testId}-row`}
              data-category={item.category}
              data-status={item.status}
              className="border-b border-hairline last:border-b-0"
            >
              <button
                type="button"
                onClick={() => onOpen(item.id)}
                className="flex w-full items-center justify-between gap-3 py-[7px] text-left hover:bg-surface-2"
              >
                <span className="min-w-0 truncate text-[11.5px] text-text">{item.label}</span>
                <span className="flex shrink-0 items-center gap-[10px]">
                  <span className="font-mono text-[11.5px] font-semibold">
                    {formatCents(item.amountCents)}
                  </span>
                  <span className={`${CHIP_CLASS} ${LINE_ITEM_STATUS_TONE[item.status]}`}>
                    {LINE_ITEM_STATUS_LABEL[item.status]}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
