/**
 * Story 4.3 AC 1-3 and AC 5 — the composer, its two inline refusals, and the
 * one thing this modal must never do.
 *
 * AC 3 is the reason half of this file exists. The prototype's `sendEmail` calls
 * `alert('Please enter a subject.')`, which blocks the page, cannot be styled or
 * announced, and is dismissed only by a click — so the assertion that matters is
 * not merely "a message appears" but "`window.alert` was never called", and that
 * the message is attached to the control it is about.
 *
 * The other half is AD-1. **The SPA merges nothing**: a template button fetches
 * the server's merged letter and the recipient checkboxes become *exactly* that
 * template's set. The fixtures are built so a browser-side merge or a union of
 * the two recipient sets would fail rather than coincide — `MERGED_RTW_OFFER`
 * and `MERGED_STATUS_UPDATE` disagree with each other and with the blank
 * default, and both carry square-bracketed handler-fill text that must survive
 * byte for byte (AD-2).
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  EMAIL_CLAIM_NOT_FOUND,
  EMAIL_CREATED,
  EMAIL_INVALID,
  EMAIL_TEMPLATES,
  EMAIL_WRITTEN_NOT_READABLE,
  MEETING_EMAIL_DRAFT,
  MERGED_RTW_OFFER,
  MERGED_STATUS_UPDATE,
  ME_HANDLER,
  type StubRoutes,
  stubApi,
} from "@/test/api-mock";

import type { ComposerPrefill } from "./DiaryNav";
import {
  DRAFT_FAILED_MESSAGE,
  EmailComposerDialog,
  MERGE_FAILED_MESSAGE,
  MISSING_SUBJECT_MESSAGE,
  NO_CLAIM_TEMPLATE_REASON,
  NO_RECIPIENT_MESSAGE,
  TEMPLATES_EMPTY_MESSAGE,
  TEMPLATES_LOADING_MESSAGE,
} from "./EmailComposerDialog";

/** Every request the stub was asked for — `ActionsCard.test.tsx`'s unwrap. */
function requested(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input)
    .filter((input): input is Request => input instanceof Request);
}

function renderDialog(
  routes: StubRoutes = {},
  props: {
    claimId?: string | null;
    workerName?: string | null;
    prefill?: ComposerPrefill;
    onClose?: () => void;
    onSent?: (recipients: readonly string[]) => void;
  } = {},
) {
  stubApi({ me: ME_HANDLER, emailTemplates: EMAIL_TEMPLATES, ...routes });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <EmailComposerDialog
        open
        claimId={props.claimId === undefined ? "WC-20017" : props.claimId}
        workerName={props.workerName === undefined ? "Marcus Delgado" : props.workerName}
        prefill={props.prefill ?? { kind: "blank" }}
        onClose={props.onClose ?? (() => {})}
        onSent={props.onSent ?? (() => {})}
      />
    </QueryClientProvider>,
  );
}

/** The recipient checkboxes that are ticked, in the vocabulary's order. */
function checked(): string[] {
  return screen
    .getAllByTestId("composer-recipient")
    .filter((box) => box.dataset.state === "checked")
    .map((box) => box.dataset.recipient!);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- AC 1: what the modal offers -----------------------------------------

test("the modal offers six recipients, six templates, a body and three priorities", async () => {
  renderDialog();

  const boxes = await screen.findAllByTestId("composer-recipient");
  // By token and in order, **written out** rather than compared against the
  // constant the component maps over: `toEqual([...RECIPIENT_ORDER])` is
  // satisfied by any order at all, since re-ordering the constant re-orders both
  // sides of the assertion. This is the wire enum's order and the prototype's,
  // and it is a design decision — so the test states it.
  expect(boxes.map((box) => box.dataset.recipient)).toEqual([
    "employee",
    "employer_hr",
    "ncm",
    "treating_physician",
    "supervisor",
    "attorney",
  ]);

  const buttons = await screen.findAllByTestId("composer-template");
  expect(buttons.map((button) => button.textContent)).toEqual([
    "3-Point Contact",
    "RTW Offer",
    "NCM Referral",
    "Status Update",
    "IME Request",
    "Settlement Notice",
  ]);

  expect(screen.getByTestId("composer-subject")).toHaveAttribute(
    "placeholder",
    "Subject line…",
  );
  expect(screen.getByTestId("composer-body")).toHaveAttribute("placeholder", "Email body…");
  // Written out for the recipients' reason — the constant on both sides of an
  // assertion proves only that the component read *a* list.
  expect(
    Array.from(screen.getByTestId("composer-priority").querySelectorAll("option")).map(
      (option) => option.value,
    ),
  ).toEqual(["normal", "high", "urgent"]);
  expect(screen.getByTestId("composer-send")).toHaveTextContent("✉ Send Email (logged)");
  expect(screen.getByTestId("composer-cancel")).toHaveTextContent("Cancel");
});

test("Employee is pre-checked on a blank compose, and nothing else is", async () => {
  renderDialog();
  await screen.findAllByTestId("composer-recipient");

  expect(checked()).toEqual(["employee"]);
});

test("the claim reference is read-only and cannot be retargeted", async () => {
  renderDialog();

  const claim = await screen.findByTestId("composer-claim");
  expect(claim).toHaveValue("WC-20017 — Marcus Delgado");
  expect(claim).toHaveAttribute("readonly");
});

test("with no claim selected the field says so and the templates say why", async () => {
  // Free composition still works — the column is nullable — but a *template* is
  // claim-aware by definition, and the prototype's claim-less render produced
  // letters full of holes.
  renderDialog({}, { claimId: null, workerName: null });

  expect(await screen.findByTestId("composer-claim")).toHaveValue("No claim selected");
  for (const button of await screen.findAllByTestId("composer-template")) {
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", NO_CLAIM_TEMPLATE_REASON);
  }
  // …and the reason is on screen, not only in a tooltip a pointer can reach.
  expect(screen.getByTestId("composer-template-reason")).toHaveTextContent(
    NO_CLAIM_TEMPLATE_REASON,
  );
});

test("a claim whose worker has not loaded shows the id, not a dangling em dash", async () => {
  renderDialog({}, { workerName: null });

  expect(await screen.findByTestId("composer-claim")).toHaveValue("WC-20017");
});

test("the claim reference survives a navigation behind the open modal", async () => {
  // The workspace selection can move under an open composer — Back, Forward, or
  // "Open Claim" on a card in the pane behind — and the payload does not move
  // with it, because the claim is captured at mount. The field used to compare
  // the composed claim against the *live* prop, so the label silently dropped
  // the worker's name the moment the selection changed: a read-only field whose
  // whole promise is "what this shows is what the send carries", quietly showing
  // less than it had. The worker name is the other half — it must not follow the
  // selection either, or WC-20017 would acquire Ana Ruiz.
  stubApi({ me: ME_HANDLER, emailTemplates: EMAIL_TEMPLATES });
  const client = createQueryClient();
  const view = (claimId: string | null, workerName: string | null) => (
    <QueryClientProvider client={client}>
      <EmailComposerDialog
        open
        claimId={claimId}
        workerName={workerName}
        prefill={{ kind: "blank" }}
        onClose={() => {}}
        onSent={() => {}}
      />
    </QueryClientProvider>
  );

  const { rerender } = render(view("WC-20017", "Marcus Delgado"));
  expect(await screen.findByTestId("composer-claim")).toHaveValue("WC-20017 — Marcus Delgado");

  rerender(view("WC-20044", "Ana Ruiz"));

  const claim = screen.getByTestId("composer-claim");
  expect(claim).toHaveValue("WC-20017 — Marcus Delgado");
  expect(claim).toHaveAttribute("data-claim-id", "WC-20017");
});

test("a worker name that arrives after the modal opened still fills the field in", async () => {
  // The case file is a separate request, so `workerName` is frequently null at
  // mount — which is why the *name* is not simply captured the way the claim is.
  // While the workspace is still on the claim this composer opened on, the live
  // prop is the honest value and the field takes it.
  stubApi({ me: ME_HANDLER, emailTemplates: EMAIL_TEMPLATES });
  const client = createQueryClient();
  const view = (workerName: string | null) => (
    <QueryClientProvider client={client}>
      <EmailComposerDialog
        open
        claimId="WC-20017"
        workerName={workerName}
        prefill={{ kind: "blank" }}
        onClose={() => {}}
        onSent={() => {}}
      />
    </QueryClientProvider>
  );

  const { rerender } = render(view(null));
  expect(await screen.findByTestId("composer-claim")).toHaveValue("WC-20017");

  rerender(view("Marcus Delgado"));

  expect(screen.getByTestId("composer-claim")).toHaveValue("WC-20017 — Marcus Delgado");
});

// --- AC 2: the template merge is the server's ----------------------------

test("a template fills the subject and body from the server, verbatim", async () => {
  renderDialog({ mergedTemplate: MERGED_RTW_OFFER });
  await screen.findAllByTestId("composer-template");

  await userEvent.click(screen.getByText("RTW Offer"));

  await waitFor(() =>
    expect(screen.getByTestId("composer-subject")).toHaveValue(MERGED_RTW_OFFER.body.subject),
  );
  const body = screen.getByTestId("composer-body") as HTMLTextAreaElement;
  expect(body).toHaveValue(MERGED_RTW_OFFER.body.body);
  // No `{{…}}` survives a merge…
  expect(body.value).not.toContain("{{");
  // …and the square-bracketed handler-fill prompt does, byte for byte (AD-2).
  expect(body.value).toContain("[Transitional Duty — to be completed by supervisor]");
  // The request named the claim, because the merge is claim-aware. It does
  // **not** show which template was asked for, and no assertion here could: the
  // stub answers `MERGED_RTW_OFFER` for any `/merged` URL, so what this test
  // pins is that the letter on screen is the server's answer verbatim. Which
  // key travels is pinned by the next test but one, whose stub branches on the
  // URL and would hand back the wrong letter if the wrong key were requested.
  expect(
    requested().some((request) => request.url.includes("claimId=WC-20017")),
  ).toBe(true);
});

test("a template replaces the recipient set — it never unions with what is ticked", async () => {
  renderDialog({ mergedTemplate: MERGED_RTW_OFFER });
  await screen.findAllByTestId("composer-template");

  // Tick something the template does not address, so a union would be visible.
  await userEvent.click(
    screen.getAllByTestId("composer-recipient").find((box) => box.dataset.recipient === "attorney")!,
  );
  expect(checked()).toEqual(["employee", "attorney"]);

  await userEvent.click(screen.getByText("RTW Offer"));

  await waitFor(() => expect(checked()).toEqual(["employee", "employer_hr", "ncm"]));
});

test("a second template replaces the first, subject, body and recipients together", async () => {
  renderDialog({
    mergedTemplate: (url) =>
      url.includes("status_update") ? MERGED_STATUS_UPDATE : MERGED_RTW_OFFER,
  });
  await screen.findAllByTestId("composer-template");

  await userEvent.click(screen.getByText("RTW Offer"));
  await waitFor(() => expect(checked()).toEqual(["employee", "employer_hr", "ncm"]));

  await userEvent.click(screen.getByText("Status Update"));

  await waitFor(() =>
    expect(screen.getByTestId("composer-subject")).toHaveValue(
      MERGED_STATUS_UPDATE.body.subject,
    ),
  );
  expect(checked()).toEqual(["employer_hr", "supervisor"]);
  // The whole letter is replaced, body included — a template is a fresh
  // composition, not a patch over the last one. (That the handler's *typing*
  // goes with the letter it was an edit to is the next test's claim; nothing is
  // typed here.)
  expect(screen.getByTestId("composer-body")).toHaveValue(MERGED_STATUS_UPDATE.body.body);
});

test("typing survives the template that filled it, and is what gets sent", async () => {
  renderDialog({ mergedTemplate: MERGED_RTW_OFFER, sendEmail: EMAIL_CREATED });
  await screen.findAllByTestId("composer-template");

  await userEvent.click(screen.getByText("RTW Offer"));
  await waitFor(() => expect(screen.getByTestId("composer-subject")).not.toHaveValue(""));

  await userEvent.clear(screen.getByTestId("composer-subject"));
  await userEvent.type(screen.getByTestId("composer-subject"), "My own subject");
  await userEvent.click(screen.getByTestId("composer-send"));

  await waitFor(() => expect(requested().some((r) => r.method === "POST")).toBe(true));
  const post = requested().find((request) => request.method === "POST")!;
  const body = (await post.clone().json()) as Record<string, unknown>;
  expect(body.subject).toBe("My own subject");
  // The body is still the merged one, and the provenance is recorded.
  expect(body.body).toBe(MERGED_RTW_OFFER.body.body);
  expect(body.templateKey).toBe("rtw_offer");
});

test("a merge that fails says so and leaves the composer usable", async () => {
  renderDialog({
    mergedTemplate: {
      status: 404,
      body: {
        type: "/problems/email-claim-not-found",
        title: "Not Found",
        status: 404,
        detail: "No claim WC-20017 in your caseload.",
      },
    },
  });
  await screen.findAllByTestId("composer-template");

  await userEvent.click(screen.getByText("RTW Offer"));

  expect(await screen.findByTestId("composer-merge-error")).toHaveTextContent(
    MERGE_FAILED_MESSAGE,
  );
  // Free composition still works — the failure is about the template, not the
  // modal.
  expect(screen.getByTestId("composer-subject")).toBeEnabled();
});

test("a meeting's letter that could not be prepared is not blamed on a template", async () => {
  // The handler pressed ✉ on a meeting card and chose no template at all, so
  // "That template could not be merged" — the one message this modal used to
  // have — told them to retry something they had not done and pointed at a row
  // of buttons that has nothing to do with the failure.
  renderDialog(
    {
      meetingEmailDraft: {
        status: 404,
        body: {
          type: "/problems/meeting-not-found",
          title: "Not Found",
          status: 404,
          detail: "No meeting 501 in your diary.",
        },
      },
    },
    { prefill: { kind: "meeting", meetingId: 501 } },
  );

  expect(await screen.findByTestId("composer-merge-error")).toHaveTextContent(
    DRAFT_FAILED_MESSAGE,
  );
  expect(screen.getByTestId("composer-merge-error")).not.toHaveTextContent(
    MERGE_FAILED_MESSAGE,
  );
});

test("Send is disabled while a letter is being merged", async () => {
  // The window is small and what falls into it is unrecoverable: `fill` moves on
  // the click while the form still shows the previous letter (`placeholderData`
  // holds it), so a send inside the round trip logs one template's key against
  // another's subject, body and claim — into a table with no edit and no delete.
  renderDialog({ mergedTemplate: "pending" });
  await screen.findAllByTestId("composer-template");
  expect(screen.getByTestId("composer-send")).toBeEnabled();

  await userEvent.click(screen.getByText("RTW Offer"));

  expect(await screen.findByTestId("composer-merging")).toBeInTheDocument();
  expect(screen.getByTestId("composer-send")).toBeDisabled();
  // …and the templates are disabled with it, so the merge in flight is the only
  // one there can be.
  for (const button of screen.getAllByTestId("composer-template")) {
    expect(button).toBeDisabled();
  }
});

// --- AC 3: the two refusals, both inline ---------------------------------

test("an empty subject is refused at the subject field, never through alert()", async () => {
  const alerted = vi.fn();
  vi.stubGlobal("alert", alerted);
  renderDialog();
  await screen.findByTestId("email-composer");

  await userEvent.click(screen.getByTestId("composer-send"));

  const refusal = await screen.findByTestId("composer-error");
  expect(refusal).toHaveTextContent(MISSING_SUBJECT_MESSAGE);
  const subject = screen.getByTestId("composer-subject");
  expect(subject).toHaveAttribute("aria-invalid", "true");
  expect(document.getElementById(subject.getAttribute("aria-describedby")!)).toBe(refusal);
  // The whole of AC 3: the prototype's `alert()` does not survive…
  expect(alerted).not.toHaveBeenCalled();
  // …and nothing was written.
  expect(requested().filter((request) => request.method === "POST")).toHaveLength(0);
});

test("typing a subject clears the refusal about it not being there", async () => {
  renderDialog();
  await screen.findByTestId("email-composer");

  await userEvent.click(screen.getByTestId("composer-send"));
  await screen.findByTestId("composer-error");

  await userEvent.type(screen.getByTestId("composer-subject"), "A");

  expect(screen.queryByTestId("composer-error")).not.toBeInTheDocument();
});

test("a send addressed to nobody is refused at the recipient fieldset", async () => {
  // The prototype logs a send with zero recipients; an email addressed to
  // nobody records nothing about who was told, so this is one of the two
  // prototype behaviours deliberately not ported.
  const alerted = vi.fn();
  vi.stubGlobal("alert", alerted);
  renderDialog();
  await screen.findAllByTestId("composer-recipient");

  await userEvent.type(screen.getByTestId("composer-subject"), "Something");
  await userEvent.click(
    screen.getAllByTestId("composer-recipient").find((box) => box.dataset.recipient === "employee")!,
  );
  expect(checked()).toEqual([]);

  await userEvent.click(screen.getByTestId("composer-send"));

  const refusal = await screen.findByTestId("composer-error");
  expect(refusal).toHaveTextContent(NO_RECIPIENT_MESSAGE);
  // **Marked on the controls, the way the subject's twin is.** The invalid state
  // shipped on the `<fieldset>`, which maps to `role="group"` and does not
  // support `aria-invalid` — so the refusal reached a screen reader only through
  // the alert, and the thing the handler has to fix was never marked at all. All
  // six carry it, because "addressed to nobody" is a statement about the set.
  for (const box of screen.getAllByTestId("composer-recipient")) {
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(document.getElementById(box.getAttribute("aria-describedby")!)).toBe(refusal);
  }
  expect(alerted).not.toHaveBeenCalled();
  expect(requested().filter((request) => request.method === "POST")).toHaveLength(0);
});

test("a refusal about something else does not mark the recipients invalid", async () => {
  renderDialog({ sendEmail: EMAIL_INVALID });
  await screen.findAllByTestId("composer-recipient");

  await userEvent.type(screen.getByTestId("composer-subject"), "Something");
  await userEvent.click(screen.getByTestId("composer-send"));
  await screen.findByTestId("composer-error");

  for (const box of screen.getAllByTestId("composer-recipient")) {
    expect(box).toHaveAttribute("aria-invalid", "false");
    expect(box).not.toHaveAttribute("aria-describedby");
  }
});

test("the server's own 422 lands where the client's refusals do", async () => {
  renderDialog({ sendEmail: EMAIL_INVALID });
  await screen.findByTestId("email-composer");

  await userEvent.type(screen.getByTestId("composer-subject"), "Something");
  await userEvent.click(screen.getByTestId("composer-send"));

  // The server's `detail` verbatim — it names the field and the rule and never
  // echoes the value (AD-11), so it is safe to show.
  expect(await screen.findByTestId("composer-error")).toHaveTextContent("Subject cannot be empty");
});

test("a refusal about something else does not mark the subject invalid", async () => {
  // `aria-invalid` is a statement about one input, and a 422 about the body or a
  // dropped connection is not about the subject.
  renderDialog({
    sendEmail: {
      status: 422,
      body: {
        type: "/problems/invalid-patch",
        title: "Unprocessable Content",
        status: 422,
        detail: "body contains characters that cannot be stored",
      },
    },
  });
  await screen.findByTestId("email-composer");

  await userEvent.type(screen.getByTestId("composer-subject"), "Something");
  await userEvent.click(screen.getByTestId("composer-send"));
  await screen.findByTestId("composer-error");

  const subject = screen.getByTestId("composer-subject");
  expect(subject).toHaveAttribute("aria-invalid", "false");
  expect(subject).not.toHaveAttribute("aria-describedby");
});

// --- the three 404s this route authors -----------------------------------

test("a send the server logged but could not read back is a completed send", async () => {
  // `/problems/email-not-readable` is answered *after* the row is committed and
  // audited — its own `detail` says "Do not send it again; reload the list." —
  // and `email_log` has no edit and no delete. Treated as a refusal, this modal
  // stayed open over an intact draft with Send enabled, which is the console
  // inviting exactly the duplicate the server's sentence forbids.
  const onClose = vi.fn();
  const sent: string[][] = [];
  renderDialog(
    { sendEmail: EMAIL_WRITTEN_NOT_READABLE },
    { onClose, onSent: (recipients) => sent.push([...recipients]) },
  );
  await screen.findByTestId("email-composer");

  await userEvent.type(screen.getByTestId("composer-subject"), "Settlement notice");
  await userEvent.click(screen.getByTestId("composer-send"));

  // The success path exactly: the caller is told who was addressed (its toast
  // and its switch to ✉ Emails), and the modal closes.
  await waitFor(() => expect(onClose).toHaveBeenCalled());
  expect(sent).toEqual([["employee"]]);
  expect(screen.queryByTestId("composer-error")).not.toBeInTheDocument();
});

test("a 404 this console authors keeps the server's own sentence", async () => {
  // `READABLE_NOT_FOUND` had none of Story 4.3's three problem types in it, so
  // every email 404 fell through to "Could not save. Try again in a moment." —
  // a retry invitation for a claim reference that will answer 404 for ever.
  renderDialog({ sendEmail: EMAIL_CLAIM_NOT_FOUND });
  await screen.findByTestId("email-composer");

  await userEvent.type(screen.getByTestId("composer-subject"), "Status update");
  await userEvent.click(screen.getByTestId("composer-send"));

  const refusal = await screen.findByTestId("composer-error");
  expect(refusal).toHaveTextContent("No claim WC-20017 in your caseload.");
  expect(refusal).not.toHaveTextContent("Try again in a moment");
});

// --- AC 4: what a valid send actually sends ------------------------------

test("a free composition sends without a claim and without a template", async () => {
  const onClose = vi.fn();
  const sent: string[][] = [];
  renderDialog(
    { sendEmail: EMAIL_CREATED },
    { claimId: null, workerName: null, onClose, onSent: (r) => sent.push([...r]) },
  );
  await screen.findByTestId("email-composer");

  await userEvent.type(screen.getByTestId("composer-subject"), "Plant walkthrough");
  await userEvent.selectOptions(screen.getByTestId("composer-priority"), "urgent");
  await userEvent.click(screen.getByTestId("composer-send"));

  await waitFor(() => expect(onClose).toHaveBeenCalled());
  const post = requested().find((request) => request.method === "POST")!;
  const body = (await post.clone().json()) as Record<string, unknown>;
  expect(body.claimId).toBeNull();
  expect(body.templateKey).toBeNull();
  expect(body.priority).toBe("urgent");
  expect(body.recipients).toEqual(["employee"]);
  // Empty free text is sent as null rather than "", so "not written" has one
  // representation on the wire.
  expect(body.body).toBeNull();
  // The roles go back to the caller, which is what the toast names.
  expect(sent).toEqual([["employee"]]);
});

test("recipients are sent in the vocabulary's order, whatever order they were ticked", async () => {
  renderDialog({ sendEmail: EMAIL_CREATED });
  await screen.findAllByTestId("composer-recipient");

  const box = (role: string) =>
    screen.getAllByTestId("composer-recipient").find((node) => node.dataset.recipient === role)!;
  await userEvent.click(box("attorney"));
  await userEvent.click(box("ncm"));
  await userEvent.type(screen.getByTestId("composer-subject"), "Something");
  await userEvent.click(screen.getByTestId("composer-send"));

  await waitFor(() => expect(requested().some((r) => r.method === "POST")).toBe(true));
  const post = requested().find((request) => request.method === "POST")!;
  expect((await post.clone().json()).recipients).toEqual(["employee", "ncm", "attorney"]);
});

// --- AC 5: convert-to-email ----------------------------------------------

test("opening from a meeting fills the composer from the server's own letter", async () => {
  renderDialog(
    { meetingEmailDraft: MEETING_EMAIL_DRAFT },
    { prefill: { kind: "meeting", meetingId: 501 } },
  );

  await waitFor(() =>
    expect(screen.getByTestId("composer-subject")).toHaveValue(
      MEETING_EMAIL_DRAFT.body.subject,
    ),
  );
  expect(screen.getByTestId("composer-body")).toHaveValue(MEETING_EMAIL_DRAFT.body.body);
  // The meeting's participants, mapped one to one — no substring match on
  // labels, which is how the prototype did it.
  expect(checked()).toEqual(["employee", "employer_hr"]);
  // …and the letter came from the meeting's own draft endpoint rather than from
  // any template. What goes on the wire *as* `templateKey` is not asserted here
  // — this test makes no POST — and it is not left unasserted either: the send
  // test below reads it out of a real request body.
  expect(
    requested().some((request) => request.url.includes("/meetings/501/email-draft")),
  ).toBe(true);
});

test("a meeting's letter is logged against the meeting's claim, not the selection", async () => {
  // `MergedEmailResponse.claimId` is echoed back precisely so the send carries
  // the claim the *merge* resolved: a browser that re-read its own selection in
  // between could compose against one claim and log against another.
  renderDialog(
    { meetingEmailDraft: MEETING_EMAIL_DRAFT, sendEmail: EMAIL_CREATED },
    { prefill: { kind: "meeting", meetingId: 501 }, claimId: "WC-20044", workerName: "Ana Ruiz" },
  );
  await waitFor(() => expect(screen.getByTestId("composer-subject")).not.toHaveValue(""));

  expect(screen.getByTestId("composer-claim")).toHaveAttribute("data-claim-id", "WC-20017");
  await userEvent.click(screen.getByTestId("composer-send"));

  await waitFor(() => expect(requested().some((r) => r.method === "POST")).toBe(true));
  const post = requested().find((request) => request.method === "POST")!;
  const body = (await post.clone().json()) as Record<string, unknown>;
  expect(body.claimId).toBe("WC-20017");
  // …and `templateKey: null`, on the wire, for real: a meeting's letter starts
  // from no template at all.
  expect(body.templateKey).toBeNull();
});

// --- the caps, on the controls -------------------------------------------

test("the subject and the body declare the server's caps", async () => {
  // Without them an over-long value is refused by Pydantic with "The request
  // body or parameters failed validation." — no field named, no limit shown.
  //
  // **The server's numbers, written out.** This asserted against
  // `EMAIL_SUBJECT_MAX`/`EMAIL_BODY_MAX` — the same constants the component
  // renders — so the title's claim about *the server's* caps was checked by
  // nothing: set `EMAIL_BODY_MAX` to 50 and the suite stayed green while the
  // textarea silently truncated a merged settlement notice. 200 and 10,000 are
  // `services/claims/emails.py::MAX_SUBJECT_LENGTH`/`MAX_BODY_LENGTH`, and this
  // file is the place they have to be restated to be checked at all.
  renderDialog();
  await screen.findByTestId("email-composer");

  expect(screen.getByTestId("composer-subject")).toHaveAttribute("maxlength", "200");
  expect(screen.getByTestId("composer-body")).toHaveAttribute("maxlength", "10000");
  expect(screen.getByTestId("composer-body-length")).toHaveTextContent(
    "0 of 10000 characters",
  );
});

test("Cancel goes through the close path that clears the refusal", async () => {
  const onClose = vi.fn();
  renderDialog({}, { onClose });
  await screen.findByTestId("email-composer");

  await userEvent.click(screen.getByTestId("composer-send"));
  expect(await screen.findByTestId("composer-error")).toBeInTheDocument();

  await userEvent.click(screen.getByTestId("composer-cancel"));

  expect(onClose).toHaveBeenCalled();
  expect(screen.queryByTestId("composer-error")).not.toBeInTheDocument();
});

test("templates that fail to load leave the letter writable by hand", async () => {
  renderDialog({
    emailTemplates: {
      status: 400,
      body: {
        type: "about:blank",
        title: "Bad Request",
        status: 400,
        detail: "The server answered 400.",
      },
    },
  });

  expect(await screen.findByTestId("composer-templates-error")).toBeInTheDocument();
  expect(
    within(screen.getByTestId("email-composer")).getByTestId("composer-subject"),
  ).toBeEnabled();
});

test("the template row says it is loading, and says when there are none", async () => {
  // Four states, not two. The row handled `isError` alone, so "we have not been
  // answered yet" and "the answer carried no templates" were both drawn as a
  // heading over an empty strip — and the second is indistinguishable from a row
  // that failed to paint.
  const { unmount } = renderDialog({ emailTemplates: "pending" });

  expect(await screen.findByTestId("composer-templates-loading")).toHaveTextContent(
    TEMPLATES_LOADING_MESSAGE,
  );
  expect(screen.queryByTestId("composer-templates")).not.toBeInTheDocument();

  unmount();
  vi.unstubAllGlobals();

  // A 200 carrying nothing — a migration applied without its seed.
  renderDialog({ emailTemplates: { status: 200, body: { items: [] } } });

  expect(await screen.findByTestId("composer-templates-empty")).toHaveTextContent(
    TEMPLATES_EMPTY_MESSAGE,
  );
  expect(screen.queryAllByTestId("composer-template")).toHaveLength(0);
  // …and the letter is still writable by hand, which is what the message says.
  expect(screen.getByTestId("composer-subject")).toBeEnabled();
});

test("the templates are not fetched until the composer is open", async () => {
  // `DiaryTab` mounts this dialog outside the sub-tab switch — deliberately, so
  // ✉ on a meeting card works from any sub-tab — which meant an ungated query
  // fetched the six on every workspace load, for every handler who never
  // composed anything and never left 📓 Notes.
  stubApi({ me: ME_HANDLER, emailTemplates: EMAIL_TEMPLATES });
  render(
    <QueryClientProvider client={createQueryClient()}>
      <EmailComposerDialog
        open={false}
        claimId="WC-20017"
        workerName="Marcus Delgado"
        prefill={{ kind: "blank" }}
        onClose={() => {}}
        onSent={() => {}}
      />
    </QueryClientProvider>,
  );

  await waitFor(() => expect(screen.queryByTestId("email-composer")).not.toBeInTheDocument());
  expect(requested().some((request) => request.url.includes("/email-templates"))).toBe(false);
});
