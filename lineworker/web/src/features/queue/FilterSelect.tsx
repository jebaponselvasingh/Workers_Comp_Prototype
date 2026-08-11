/**
 * The eight operational filters (FR-Q-1) — the prototype's `#qfSelect`.
 *
 * The values are the API's snake_case enum; the labels are this component's,
 * per the Enums convention ("UI owns display labels"). That split is why
 * `high_risk` never appears on screen and "High risk" never appears on the
 * wire, and why adding a language later is a change to this table alone.
 *
 * Choosing one **refetches** — the pane's query key carries the filter — so
 * there is deliberately no local list to narrow here (AD-1). The dropdown
 * reports a choice and nothing else.
 */
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import type { QueueFilter } from "@/api/claims";

interface FilterOption {
  value: QueueFilter;
  label: string;
}

/** In the prototype's order (its `<option>` list, lines 489-498). */
export const FILTER_OPTIONS: readonly FilterOption[] = [
  { value: "all", label: "All claims" },
  { value: "active", label: "Active / In treatment" },
  { value: "high_risk", label: "High risk" },
  { value: "fraud", label: "Fraud alert" },
  { value: "litigation", label: "Litigation" },
  { value: "payment_due", label: "Payment due" },
  { value: "surgery", label: "Surgery / complex medical" },
  { value: "siu", label: "SIU review" },
];

interface FilterSelectProps {
  value: QueueFilter;
  onChange: (value: QueueFilter) => void;
}

export function FilterSelect({ value, onChange }: FilterSelectProps) {
  return (
    <Select value={value} onValueChange={(next) => onChange(next as QueueFilter)}>
      <SelectTrigger
        size="sm"
        data-testid="queue-filter"
        aria-label="Filter claims"
        className="h-7 w-full rounded-[4px] border-border bg-surface text-[11.5px]"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {FILTER_OPTIONS.map((option) => (
          <SelectItem
            key={option.value}
            value={option.value}
            data-testid={`queue-filter-option-${option.value}`}
            className="text-[11.5px]"
          >
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
