/**
 * The branded employee ID card (Story 2.5, AC 3) — the prototype's `.idcard`.
 *
 * A stylised badge rather than another `Kv` list, and deliberately so: it is
 * the one surface on the case file that shows *the worker* rather than the
 * claim, and the prototype gives it a masthead, an initials avatar, a
 * monospace ID grid and a footer strip for exactly that reason. Ported onto
 * tokens; the "LINEWORKER MFG CO." / "WC-VERIFIED" masthead is the
 * prototype's own wording.
 *
 * **The initials are computed here, and that is not a derivation.** It is a
 * rendering of a string the server sent — first letter of each word, capped at
 * two — with no rule, no threshold and no payload arithmetic behind it, which
 * is the line `noDerivation.test.ts` draws. (The prototype does the same
 * thing, in the same place.)
 *
 * Every field arrives in one `idCard` block rather than being stitched from
 * the header and a stage variant: `employeeBusinessId` and `plant` live on the
 * *intake* variant, so a client assembling this card itself would render a
 * different card once a claim moved to treatment.
 */
import type { EmployeeIdCardData } from "@/api/claims";

import { formatDate } from "../Cards";

/** First letters of the first two words, uppercased — the prototype's `ii`. */
function initialsOf(name: string): string {
  return name
    .split(" ")
    .map((part) => part[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function IdField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-[9px] tracking-[0.3px] text-faint uppercase">{label}</dt>
      <dd className="font-mono text-[10px] text-text">{children}</dd>
    </>
  );
}

export function EmployeeIdCard({ card }: { card: EmployeeIdCardData }) {
  return (
    <section
      data-testid="employee-id-card"
      className="mb-[10px] rounded-lg border border-border bg-surface p-3"
    >
      <h3 className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase">
        Employee ID card
      </h3>

      <div className="overflow-hidden rounded-md border border-border bg-surface-2">
        <div className="flex items-center justify-between border-b border-hairline px-3 py-2">
          <span className="inline-flex items-center gap-[6px] font-display text-[10px] font-bold tracking-[0.5px] text-text uppercase">
            <span aria-hidden className="inline-block size-[9px] rounded-[2px] bg-brand" />
            LINEWORKER MFG CO.
          </span>
          <span className="font-mono text-[9px] text-faint">WC-VERIFIED</span>
        </div>

        <div className="flex items-start gap-3 p-3">
          <span
            aria-hidden
            data-testid="employee-id-initials"
            className="inline-flex size-[42px] shrink-0 items-center justify-center rounded-md bg-steel-soft font-display text-[15px] font-bold text-steel"
          >
            {initialsOf(card.workerName)}
          </span>
          <div className="min-w-0 flex-1">
            <p data-testid="employee-id-name" className="text-[13px] font-bold text-text">
              {card.workerName}
            </p>
            <p className="mb-2 text-[11px] text-muted-text">{card.workerRole}</p>
            <dl className="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-[3px]">
              <IdField label="Employee ID">{card.employeeBusinessId}</IdField>
              <IdField label="Policy">{card.policyNum}</IdField>
              <IdField label="DOI">{formatDate(card.doi)}</IdField>
              {/* A name, not an identifier — set in the body font so it does
                  not read as another code in the column above it. */}
              <dt className="text-[9px] tracking-[0.3px] text-faint uppercase">Handler</dt>
              <dd className="text-[9px] font-semibold text-text">{card.handlerName}</dd>
            </dl>
          </div>
        </div>

        <div aria-hidden className="h-[3px] bg-brand" />
        <div className="flex items-center justify-between px-3 py-[6px] text-[10px] text-muted-text">
          <span data-testid="employee-id-plant">{card.plant}</span>
          <span>
            {card.state} · {card.region}
          </span>
        </div>
      </div>
    </section>
  );
}
