/**
 * The "+" popover: what is already on this claim, and how to add to it
 * (Story 2.4, AC 2 and AC 4).
 *
 * **Non-modal**, anchored to the button at the top right of the silhouette
 * panel, as in the prototype — a handler reads the diagram and the list at
 * the same time, and a modal would put the thing they are comparing against
 * behind a scrim.
 *
 * **Validation is inline and pre-flight, and neither replaces the other.**
 * The form refuses an empty injury type and a score outside 0-100 before it
 * sends anything, so the common mistakes cost no round trip; the command
 * refuses the same things and its 422 renders in the same place, so a
 * mistake the browser cannot see (a control character, a body key that is
 * not a region) reads identically. Neither is a dialog (NFR-3, UX-DR11) —
 * the prototype's own refusal for an empty type is a silent `focus()`, which
 * tells a handler nothing.
 *
 * **The ✕ sends the injury row's own version**, not the claim's. See
 * `useRemoveInjury`: a removal is compare-and-swapped on the row it
 * destroys, so it is not refused because somebody corrected an ICD-10 code
 * on the same claim a moment earlier.
 */
import * as React from "react";

import type { ClaimDetail, InjuryMarker } from "@/api/claims";
import { useAddInjury, useClaimWriteInFlight, useRemoveInjury } from "@/api/claims";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import { type FieldFeedback } from "../InlineEditField";
import { feedbackFromError } from "../useInlineEdits";

/** One line of the "injuries on this claim" list. */
function SummaryRow({
  marker,
  busy,
  onRemove,
}: {
  marker: InjuryMarker;
  busy: boolean;
  onRemove: () => void;
}) {
  return (
    <li
      data-testid="injury-summary-row"
      data-body-key={marker.bodyKey}
      className="flex items-start justify-between gap-2 border-b border-hairline py-[6px] text-[11px] last:border-b-0"
    >
      <span className="min-w-0">
        <b className="text-text">{marker.bodyPart}</b>
        {marker.injuryType ? ` — ${marker.injuryType}` : ""}
        {marker.primary && (
          <span
            data-testid="injury-primary-tag"
            className="ml-1 rounded-[2px] bg-brand-soft px-[4px] py-px text-[8.5px] font-bold tracking-[0.3px] text-brand uppercase"
          >
            Primary
          </span>
        )}
        <br />
        <span className="text-faint">Severity {marker.severityScore}/100</span>
      </span>
      {/* The primary has no ✕: it *is* the claim's own injury, and removing
          it would mean a claim with no injury on it. The server agrees —
          there is no `additional_injury` row to address, so `id` is null. */}
      {!marker.primary && marker.id !== null && (
        <button
          type="button"
          data-testid="injury-remove"
          data-injury-id={marker.id}
          aria-label={`Remove ${marker.bodyPart} injury`}
          disabled={busy}
          onClick={onRemove}
          className="shrink-0 rounded px-1 text-[12px] leading-none text-faint hover:text-error disabled:opacity-50"
        >
          ✕
        </button>
      )}
    </li>
  );
}

export function AddInjuryPopover({ claim }: { claim: ClaimDetail }) {
  const add = useAddInjury(claim.claimId);
  const remove = useRemoveInjury(claim.claimId);
  const [open, setOpen] = React.useState(false);
  const [feedback, setFeedback] = React.useState<FieldFeedback | undefined>();

  const { markers, defaultSeverityScore, severityMin, severityMax } = claim.injury;
  const [bodyKey, setBodyKey] = React.useState(claim.editOptions.bodyParts[0]?.key ?? "");
  const [injuryType, setInjuryType] = React.useState("");
  const [severity, setSeverity] = React.useState(String(defaultSeverityScore));

  // Every command against this claim, not just this component's two: the
  // severity field and the body-part select send the same `expectedVersion`
  // from the same cache entry (code review, 2026-08-12).
  const busy = useClaimWriteInFlight(claim.claimId);

  const reset = (): void => {
    setInjuryType("");
    setSeverity(String(defaultSeverityScore));
    setFeedback(undefined);
  };

  const submit = (event: React.FormEvent): void => {
    event.preventDefault();
    setFeedback(undefined);

    const type = injuryType.trim();
    if (!type) {
      // The prototype's silent `typeEl.focus()`, made a sentence (UX-DR11).
      setFeedback({ kind: "invalid", message: "Injury type is required." });
      return;
    }
    const score = Number(severity);
    if (severity.trim() === "" || !Number.isInteger(score)) {
      setFeedback({ kind: "invalid", message: "Severity must be a whole number." });
      return;
    }
    if (score < severityMin || score > severityMax) {
      setFeedback({
        kind: "invalid",
        message: `Severity must be between ${severityMin} and ${severityMax}.`,
      });
      return;
    }

    add.mutate(
      {
        injury: { bodyKey, injuryType: type, severityScore: score },
        expectedVersion: claim.version,
      },
      {
        onSuccess: reset,
        onError: (error) => setFeedback(feedbackFromError(error)),
      },
    );
  };

  const field =
    "w-full rounded-[3px] border border-border bg-surface px-[5px] py-[3px] text-[11px] text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand disabled:opacity-60";

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <PopoverTrigger
        data-testid="injury-add-open"
        aria-label="Injuries on this claim"
        className="absolute top-[10px] right-[10px] flex size-6 items-center justify-center rounded-full border border-border bg-surface text-[14px] leading-none text-muted-text hover:border-brand hover:text-brand"
      >
        +
      </PopoverTrigger>
      <PopoverContent
        align="end"
        data-testid="injury-popover"
        className="w-[260px] p-3 text-text"
      >
        <p className="mb-1 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase">
          Injuries on this claim ({markers.length})
        </p>
        <ul className="mb-2 flex flex-col">
          {markers.map((marker, index) => (
            <SummaryRow
              key={marker.id ?? `primary-${index}`}
              marker={marker}
              busy={busy}
              onRemove={() =>
                marker.id !== null &&
                marker.version !== null &&
                remove.mutate(
                  { injuryId: marker.id, expectedVersion: marker.version },
                  { onError: (error) => setFeedback(feedbackFromError(error)) },
                )
              }
            />
          ))}
        </ul>

        <hr className="mb-2 border-border" />
        <p className="mb-1 text-[11px] font-bold text-text">+ Add another injury</p>

        <form className="flex flex-col gap-[6px]" onSubmit={submit} noValidate>
          <select
            data-testid="injury-new-body-key"
            aria-label="Body part"
            className={field}
            value={bodyKey}
            disabled={busy}
            onChange={(event) => setBodyKey(event.target.value)}
          >
            {/* Built from the server's vocabulary, like every other select in
                the case file: an option this cannot offer is an option the
                command would refuse. */}
            {claim.editOptions.bodyParts.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
          <input
            type="text"
            data-testid="injury-new-type"
            aria-label="Injury type"
            placeholder="Injury type — e.g. Laceration"
            className={field}
            value={injuryType}
            disabled={busy}
            onChange={(event) => setInjuryType(event.target.value)}
          />
          <input
            type="number"
            data-testid="injury-new-severity"
            aria-label="Severity"
            min={severityMin}
            max={severityMax}
            className={field}
            value={severity}
            disabled={busy}
            onChange={(event) => setSeverity(event.target.value)}
          />
          {feedback && (
            <p
              data-testid={`injury-add-${feedback.kind}`}
              role={feedback.kind === "conflict" ? "status" : "alert"}
              className={`text-[10px] ${
                feedback.kind === "conflict" ? "text-warn" : "text-error"
              }`}
            >
              {feedback.message}
            </p>
          )}
          <button
            type="submit"
            data-testid="injury-add-submit"
            disabled={busy}
            className="rounded-[3px] bg-brand px-2 py-[4px] text-[11px] font-bold text-white hover:bg-brand-strong disabled:opacity-60"
          >
            Add injury
          </button>
        </form>
      </PopoverContent>
    </Popover>
  );
}
