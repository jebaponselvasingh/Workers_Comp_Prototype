/**
 * The inline-edit interaction, shared by every card that offers one.
 *
 * Extracted from the investigation card during the Story 2.3 code review,
 * because the treatment overview now edits the recovery window too and two
 * copies of this logic would be two answers to "what happens on a 409".
 *
 * **Feedback is per field, and it survives editing another one.** The first
 * version held a single `{field, value}` slot cleared at the top of every
 * commit, so a 422's "we kept what you typed" guarantee evaporated the moment
 * the handler touched a different input — with no trace that anything had
 * been refused. A map keyed by field is what makes the promise true.
 *
 * **One commit at a time.** `expectedVersion` is read from the cached case
 * file, and an optimistic update deliberately does not advance it, so a
 * second field committed before the first settles sends a version the first
 * has already consumed: the server 409s, and the handler is told somebody
 * else changed the claim — about their own edit, whose keystrokes are then
 * unrecoverable. TanStack also detaches the first call's callbacks when the
 * second `mutate()` runs, so that rollback happens silently. The card
 * disables its inputs while a commit is in flight, which is what its
 * docstring always claimed and is now true.
 */
import { useCallback, useState } from "react";

import type { ClaimDetail, EditableField, FieldEdits } from "@/api/claims";
import { useClaimWriteInFlight, useEditClaimFields } from "@/api/claims";
import { ApiError, isConflict, isInvalidPatch, isNotFound, problemType } from "@/api/errors";

import type { FieldFeedback } from "./InlineEditField";

export const CONFLICT_MESSAGE = "Updated by someone else — showing latest.";
export const FAILED_MESSAGE = "Could not save. Try again in a moment.";

export interface InlineEdits {
  /** Send one commit. `edits` is one field, or the ICD pair. */
  commit: (edits: FieldEdits) => void;
  /**
   * True while **any** command against this claim is in flight — the field
   * patch, the severity score, or an injury add/remove. Every editable
   * control on the case file disables itself with it.
   */
  busy: boolean;
  feedbackFor: (field: EditableField) => FieldFeedback | undefined;
}

/** What the handler typed, for the field a refusal is shown against. */
function attempted(edits: FieldEdits, field: EditableField): string | undefined {
  const value = edits[field];
  return typeof value === "string" ? value : undefined;
}

/**
 * One rejected mutation → one piece of inline feedback.
 *
 * Exported since Story 2.4, whose severity, add and remove mutations are
 * three more commands answering the same 409/422/anything-else. Extracted
 * rather than copied for the reason this hook was extracted in the first
 * place: four components deciding independently what a conflict looks like
 * is four chances to tell a handler that somebody else changed the claim
 * when what really happened is that they typed 101.
 *
 * `attempt` is what was submitted, kept only for the `invalid` case so the
 * handler can correct it rather than retype it.
 *
 * **The 404 branch is Story 4.2's**, and it closes a deferred item the 4.1 code
 * review opened. Everything that was not a 409 or a 422 fell through to "Could
 * not save. Try again in a moment." — including a claim tag outside the caller's
 * book, which will answer 404 for ever, and (since 4.2) a note that *was* saved
 * and could not be read back, where inviting a retry means duplicating a row in
 * an append-only table. Both carry a server sentence written for a person and
 * naming no PHI, so the honest answer is to show it.
 *
 * **It is gated on the problem `type`, not on the status**, which is the
 * correction. `isNotFound` is a bare `status === 404`, and `api/client.ts`
 * synthesises `detail = "The server answered 404."` for a response with no
 * problem envelope — so a proxy 404, a deploy-skew 404 against a route this
 * build knows and that build does not, or an offline fetch resolved by a
 * captive portal, all rendered that machine sentence under a severity-score
 * input as a refusal that will never succeed. The set below is the 404s this
 * console actually authors and whose `detail` is written for a person to read;
 * everything else is a failure, which is what `failed` means and what "try
 * again in a moment" is honest about.
 */
const READABLE_NOT_FOUND: ReadonlySet<string> = new Set([
  "/problems/claim-not-found",
  "/problems/note-claim-not-found",
  "/problems/note-not-readable",
  "/problems/meeting-claim-not-found",
  "/problems/meeting-not-found",
  "/problems/meeting-not-readable",
]);

export function feedbackFromError(error: unknown, attempt?: string): FieldFeedback {
  if (isConflict(error)) return { kind: "conflict", message: CONFLICT_MESSAGE };
  if (isInvalidPatch(error)) {
    return {
      kind: "invalid",
      // The server's own words: they name the field and the rule and never
      // echo the submitted value (AD-11).
      message: error instanceof ApiError ? error.problem.detail : FAILED_MESSAGE,
      attempted: attempt,
    };
  }
  if (isNotFound(error) && READABLE_NOT_FOUND.has(problemType(error) ?? "")) {
    return {
      kind: "notFound",
      message: error instanceof ApiError ? error.problem.detail : FAILED_MESSAGE,
    };
  }
  return { kind: "failed", message: FAILED_MESSAGE };
}

export function useInlineEdits(claim: ClaimDetail): InlineEdits {
  const edit = useEditClaimFields(claim.claimId);
  // Every command against this claim, not just this hook's — see
  // `useClaimWriteInFlight`.
  const busy = useClaimWriteInFlight(claim.claimId);
  const [feedback, setFeedback] = useState<Partial<Record<EditableField, FieldFeedback>>>({});

  const commit = useCallback(
    (edits: FieldEdits) => {
      const fields = Object.keys(edits) as EditableField[];
      // Clear only the fields being committed. Another field's unresolved
      // 422 is still unresolved, and its message is the only record that the
      // handler typed something the server would not take.
      setFeedback((current) => {
        const next = { ...current };
        for (const field of fields) delete next[field];
        return next;
      });

      edit.mutate(
        // The version is read at commit time rather than captured when the
        // card rendered: a card left open across somebody else's edit would
        // otherwise send a version it has already been told is stale.
        { edits, expectedVersion: claim.version },
        {
          onError: (error) => {
            // **Per field, not one value reused across the commit** (code
            // review, 2026-08-12). The only two-field commit is the ICD
            // pair, and its key order is always `icd, icdDesc` — so a single
            // `attempted` taken from `fields[0]` put the *code* into the
            // *description* input: clear the description, and the 422 came
            // back rendering `S39.012A` in the field the handler had just
            // emptied, ready to be committed as the description if they
            // typed on top of it. Each field now shows what was submitted
            // for that field, which for the untouched half of the pair is
            // the value it already had.
            setFeedback((current) =>
              Object.fromEntries([
                ...Object.entries(current),
                ...fields.map(
                  (field) =>
                    [field, feedbackFromError(error, attempted(edits, field))] as const,
                ),
              ]),
            );
          },
        },
      );
    },
    [claim.claimId, claim.version, edit],
  );

  const feedbackFor = useCallback(
    (field: EditableField): FieldFeedback | undefined => feedback[field],
    [feedback],
  );

  return { commit, busy, feedbackFor };
}
