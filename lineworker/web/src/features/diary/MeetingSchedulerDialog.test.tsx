/**
 * Story 4.1 AC 1 and AC 2 — the scheduler modal, its ten options, and the two
 * ways a date refusal can arrive.
 *
 * AC 2 is the reason this file exists. The prototype's `saveMeeting` calls
 * `alert('Please select a date.')`, which blocks the page, cannot be styled or
 * announced, and is dismissed only by a click — so the assertion that matters
 * is not just "a message appears" but "`window.alert` was never called". Both
 * are here, and so is the server's own 422 landing in the same place, because
 * a handler must not have to look in two places for the same kind of answer.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import {
  MEETING_LOCATION_LENGTH_CAP,
  MEETING_NOTES_LENGTH_CAP,
} from "@/api/fieldLimits";
import { createQueryClient } from "@/api/queryClient";
import { todayIso } from "@/lib/clock";
import { ME_HANDLER, MEETING_CREATED, stubApi } from "@/test/api-mock";

import { MEETING_TYPE_ORDER, PARTICIPANT_ORDER } from "./labels";
import {
  MISSING_CLAIM_MESSAGE,
  MISSING_DATE_MESSAGE,
  MeetingSchedulerDialog,
} from "./MeetingSchedulerDialog";

/** Every URL the stub was asked for — `ActionsCard.test.tsx`'s unwrap. */
function requested(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input)
    .filter((input): input is Request => input instanceof Request);
}

function renderDialog(
  routes: Parameters<typeof stubApi>[0] = {},
  props: {
    claimId?: string | null;
    workerName?: string | null;
    onClose?: () => void;
    onScheduled?: () => void;
  } = {},
) {
  stubApi({ me: ME_HANDLER, ...routes });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MeetingSchedulerDialog
        open
        claimId={props.claimId === undefined ? "WC-20017" : props.claimId}
        workerName={props.workerName === undefined ? "Marcus Delgado" : props.workerName}
        onClose={props.onClose ?? (() => {})}
        onScheduled={props.onScheduled ?? (() => {})}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the modal offers all ten meeting types and all six participants", async () => {
  renderDialog();

  const options = await screen.findAllByRole("option");
  expect(options).toHaveLength(MEETING_TYPE_ORDER.length);
  // By value and in order: the select's order is a design decision (the
  // prototype's), and a set comparison would pass against a shuffled list.
  expect(options.map((option) => (option as HTMLOptionElement).value)).toEqual([
    ...MEETING_TYPE_ORDER,
  ]);

  const boxes = screen.getAllByTestId("scheduler-participant");
  expect(boxes.map((box) => box.dataset.participant)).toEqual([...PARTICIPANT_ORDER]);
});

test("Employee is pre-checked and the linked claim cannot be retargeted", async () => {
  renderDialog();

  const employee = (await screen.findAllByTestId("scheduler-participant")).find(
    (box) => box.dataset.participant === "employee",
  );
  expect(employee).toHaveAttribute("data-state", "checked");

  const claim = screen.getByTestId("scheduler-claim");
  expect(claim).toHaveValue("WC-20017 — Marcus Delgado");
  // Read-only rather than a select: the modal opens *from* a claim, and an
  // editable field here would offer choices the server then has to refuse.
  expect(claim).toHaveAttribute("readonly");
});

test("a missing date is refused inline and never through a native dialog", async () => {
  const alert = vi.fn();
  vi.stubGlobal("alert", alert);
  renderDialog();

  // `<input type="date">` cannot be typed into character by character in
  // jsdom; clearing it is what produces the empty value the check is about.
  await userEvent.clear(await screen.findByTestId("scheduler-date"));
  await userEvent.click(screen.getByTestId("scheduler-save"));

  expect(await screen.findByTestId("scheduler-error")).toHaveTextContent(
    MISSING_DATE_MESSAGE,
  );
  // The whole of AC 2: the prototype's `alert()` does not survive.
  expect(alert).not.toHaveBeenCalled();
  // …and nothing was sent, so the refusal is the client's own.
  expect(requested().filter((request) => request.method === "POST")).toHaveLength(0);
});

test("a valid save posts what the form collected and closes", async () => {
  const onClose = vi.fn();
  const onScheduled = vi.fn();
  renderDialog({ scheduleMeeting: MEETING_CREATED }, { onClose, onScheduled });

  await userEvent.selectOptions(await screen.findByTestId("scheduler-type"), "ime_preparation");
  await userEvent.type(screen.getByTestId("scheduler-location"), "Plant office");
  await userEvent.click(screen.getByTestId("scheduler-save"));

  await waitFor(() => expect(onScheduled).toHaveBeenCalled());
  expect(onClose).toHaveBeenCalled();

  const post = requested().find((request) => request.method === "POST");
  expect(post).toBeDefined();
  const body = JSON.parse(await post!.clone().text()) as Record<string, unknown>;
  expect(body.claimId).toBe("WC-20017");
  expect(body.meetingType).toBe("ime_preparation");
  expect(body.location).toBe("Plant office");
  // Employee only, because that is the one box the modal opens with checked.
  expect(body.participants).toEqual(["employee"]);
  // Empty free text is sent as null rather than "", so "not set" has one
  // representation on the wire and the card's `notes !== null` branch cannot
  // be fooled by whitespace.
  expect(body.notes).toBeNull();
});

test("the server's own refusal lands at the same place the client's does", async () => {
  // A control character, which is the refusal the route really does answer
  // with `/problems/invalid-patch`: `notes` declares `maxLength` on the wire,
  // so an over-long agenda never reaches the command — Pydantic refuses it
  // first with `/problems/validation-error`. Stubbing the wrong pairing made
  // this test assert against a response the server cannot produce.
  renderDialog({
    scheduleMeeting: {
      status: 422,
      body: {
        type: "/problems/invalid-patch",
        title: "Unprocessable Content",
        status: 422,
        detail: "notes contains characters that cannot be stored",
      },
    },
  });

  await userEvent.click(await screen.findByTestId("scheduler-save"));

  // The server's `detail` verbatim — it names the field and the rule and never
  // echoes the value (AD-11), so it is safe to show.
  expect(await screen.findByTestId("scheduler-error")).toHaveTextContent(
    "notes contains characters that cannot be stored",
  );
});

test("a refusal about another field does not mark the date invalid", async () => {
  // `aria-invalid` is a statement about one input. A 422 about the agenda, a
  // 404 or a dropped connection all used to point it at the date, which tells
  // a screen-reader user to fix the one control that was fine.
  renderDialog({
    scheduleMeeting: {
      status: 422,
      body: {
        type: "/problems/invalid-patch",
        title: "Unprocessable Content",
        status: 422,
        detail: "notes contains characters that cannot be stored",
      },
    },
  });

  await userEvent.click(await screen.findByTestId("scheduler-save"));
  await screen.findByTestId("scheduler-error");

  const date = screen.getByTestId("scheduler-date");
  expect(date).toHaveAttribute("aria-invalid", "false");
  expect(date).not.toHaveAttribute("aria-describedby");
});

test("the missing-date refusal is the one that does mark it invalid", async () => {
  renderDialog();

  await userEvent.clear(await screen.findByTestId("scheduler-date"));
  await userEvent.click(screen.getByTestId("scheduler-save"));

  const date = await screen.findByTestId("scheduler-date");
  expect(date).toHaveAttribute("aria-invalid", "true");
  // …and it points at the alert carrying the sentence, so the two are read
  // together rather than as an input that is wrong for no stated reason.
  const describedBy = date.getAttribute("aria-describedby");
  expect(describedBy).not.toBeNull();
  expect(document.getElementById(describedBy!)).toHaveTextContent(MISSING_DATE_MESSAGE);
});

test("Cancel goes through the close path that clears the refusal", async () => {
  // Not `onClose` directly: Radix fires `onOpenChange` only for closes it
  // observes, so a Cancel that flipped the `open` prop from outside skipped
  // the reset every other close performs. Asserted through the observable half
  // — the caller is told to close, and the refusal is gone from the DOM.
  const onClose = vi.fn();
  renderDialog({}, { onClose });

  await userEvent.clear(await screen.findByTestId("scheduler-date"));
  await userEvent.click(screen.getByTestId("scheduler-save"));
  expect(await screen.findByTestId("scheduler-error")).toBeInTheDocument();

  await userEvent.click(screen.getByTestId("scheduler-cancel"));

  expect(onClose).toHaveBeenCalled();
  expect(screen.queryByTestId("scheduler-error")).not.toBeInTheDocument();
});

test("with no claim selected the read-only field says so rather than sitting empty", async () => {
  renderDialog({}, { claimId: null });

  expect(await screen.findByTestId("scheduler-claim")).toHaveValue("No claim selected");
});

test("a claim whose worker has not loaded shows the id, not a dangling em dash", async () => {
  // `useClaimDetail` is a second request: between opening the modal and its
  // arrival — or if it fails outright — `workerName` is null, and the field
  // used to read `WC-20017 — ` with nothing after it. `CopilotPane`'s sub-line
  // handles the identical null in three states, and this now mirrors it.
  renderDialog({}, { workerName: null });

  expect(await screen.findByTestId("scheduler-claim")).toHaveValue("WC-20017");
});

// --- the claim, captured rather than read at submit ----------------------

test("the linked claim is the one the modal opened on, not the one selected now", async () => {
  // The file's own docstring says the claim "cannot be retargeted", and the
  // submit read the live `?claim=` prop. Open the modal before auto-select
  // lands, or press Back while it is open, and the read-only field changes
  // under the handler — into a table with no `update_meeting`.
  const { rerender } = renderDialog();
  await screen.findByTestId("meeting-scheduler");
  expect(screen.getByTestId("scheduler-claim")).toHaveAttribute("data-claim-id", "WC-20017");

  // The workspace moves underneath the open modal.
  rerender(
    <QueryClientProvider client={createQueryClient()}>
      <MeetingSchedulerDialog
        open
        claimId="WC-20099"
        workerName="Someone Else"
        onClose={() => {}}
        onScheduled={() => {}}
      />
    </QueryClientProvider>,
  );

  // The field still names the claim it opened on, and so does the request.
  expect(screen.getByTestId("scheduler-claim")).toHaveAttribute("data-claim-id", "WC-20017");
  await userEvent.click(screen.getByTestId("scheduler-save"));

  await waitFor(() => expect(requested().some((r) => r.method === "POST")).toBe(true));
  const post = requested().find((r) => r.method === "POST")!;
  expect(await post.clone().json()).toMatchObject({ claimId: "WC-20017" });
});

test("saving with no claim selected is refused inline, and nothing is sent", async () => {
  // A 201 with `claimId: null` is an orphan meeting that can never be attached
  // to anything — there is no `update_meeting`, so the only correction is
  // delete-and-recreate.
  const alerted = vi.fn();
  vi.stubGlobal("alert", alerted);
  renderDialog({}, { claimId: null, workerName: null });
  await screen.findByTestId("meeting-scheduler");

  await userEvent.click(screen.getByTestId("scheduler-save"));

  expect(await screen.findByTestId("scheduler-error")).toHaveTextContent(MISSING_CLAIM_MESSAGE);
  expect(screen.getByTestId("scheduler-claim")).toHaveAttribute("aria-invalid", "true");
  expect(requested().some((request) => request.method === "POST")).toBe(false);
  expect(alerted).not.toHaveBeenCalled();
});

// --- the caps, on the controls -------------------------------------------

test("the agenda and the location declare the server's caps and show them", async () => {
  // Neither had one, so a pasted agenda past the cap was refused by Pydantic
  // with "The request body or parameters failed validation." — no field named,
  // no limit shown, nothing marked invalid.
  renderDialog();
  await screen.findByTestId("meeting-scheduler");

  expect(screen.getByTestId("scheduler-notes")).toHaveAttribute(
    "maxlength",
    String(MEETING_NOTES_LENGTH_CAP),
  );
  expect(screen.getByTestId("scheduler-location")).toHaveAttribute(
    "maxlength",
    String(MEETING_LOCATION_LENGTH_CAP),
  );
  expect(screen.getByTestId("scheduler-notes-length")).toHaveTextContent(
    `0 of ${MEETING_NOTES_LENGTH_CAP} characters`,
  );
  expect(screen.getByTestId("scheduler-location-length")).toHaveTextContent(
    `0 of ${MEETING_LOCATION_LENGTH_CAP} characters`,
  );

  await userEvent.type(screen.getByTestId("scheduler-location"), "Plant 3");
  expect(screen.getByTestId("scheduler-location-length")).toHaveTextContent(
    `7 of ${MEETING_LOCATION_LENGTH_CAP} characters`,
  );
});

// --- the created meeting is judged at the viewer's day -------------------

test("the create request carries the viewer's day so the 201's status is right", async () => {
  // The scheduler pre-fills the handler's *local* today, and the server judged
  // the created row at its own — so a meeting scheduled for this afternoon came
  // back marked `done` for anybody behind UTC.
  renderDialog({ scheduleMeeting: MEETING_CREATED });
  await screen.findByTestId("meeting-scheduler");

  await userEvent.click(screen.getByTestId("scheduler-save"));

  await waitFor(() => expect(requested().some((r) => r.method === "POST")).toBe(true));
  const post = requested().find((request) => request.method === "POST")!;
  expect(post.url).toContain(`asOf=${todayIso(new Date())}`);
});
