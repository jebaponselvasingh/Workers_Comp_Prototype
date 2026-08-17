/**
 * Story 4.1 AC 3-5 — the meetings list, its four states, and its three
 * controls.
 *
 * Most assertions here are of one kind: the payload said X, the screen says X.
 * That is the point of a list whose sort and whose Upcoming/Done status are
 * both the server's (AD-1, AD-10) — what a component test can prove is that
 * neither was recomputed on the way to the DOM. The fixture is deliberately
 * awkward for that: `MEETING_DONE` is dated in the **future** and carries
 * `status: "done"`, a combination only the server can produce, so a card that
 * re-derived the status from `meetingDate` renders the wrong glyph and fails.
 *
 * The rest are the states the server cannot describe — loading, error, empty
 * and a refusal (NFR-3) — plus the ✉ seam, which is AC 5 in full.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { todayIso } from "@/lib/clock";
import {
  ME_HANDLER,
  MEETINGS,
  MEETINGS_EMPTY,
  MEETING_COMPLETED,
  MEETING_CONFLICT,
  MEETING_DONE,
  MEETING_UPCOMING,
  type StubRoutes,
  stubApi,
} from "@/test/api-mock";

import { DiaryNavProvider } from "./DiaryNav";
import { EMAIL_SEAM_REASON } from "./MeetingCard";
import { MeetingsSubTab } from "./MeetingsSubTab";

function requested(): Request[] {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => input)
    .filter((input): input is Request => input instanceof Request);
}

function renderSubTab(routes: StubRoutes = {}) {
  stubApi({ me: ME_HANDLER, meetings: MEETINGS, ...routes });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <DiaryNavProvider>
        <MeetingsSubTab claimId="WC-20017" workerName="Marcus Delgado" />
      </DiaryNavProvider>
    </QueryClientProvider>,
  );
}

/** One card by its server-assigned id — never by index (Story 3.5's rule). */
function card(id: number): HTMLElement {
  const found = screen
    .getAllByTestId("meeting-card")
    .find((element) => element.dataset.meetingId === String(id));
  if (!found) throw new Error(`no meeting card ${id} is rendered`);
  return found;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("each card renders the server's status, not a date comparison of its own", async () => {
  renderSubTab();
  await screen.findAllByTestId("meeting-card");

  // Both meetings are dated in 2099. Only one of them is `done`, and it is
  // done because the server said so — a card that compared `meetingDate` with
  // today would call both of them upcoming.
  expect(card(501)).toHaveAttribute("data-status", "upcoming");
  expect(within(card(501)).getByTestId("meeting-title")).toHaveTextContent("📅 RTW Conference");
  expect(card(502)).toHaveAttribute("data-status", "done");
  expect(within(card(502)).getByTestId("meeting-title")).toHaveTextContent(
    "✓ Claim Review — Supervisor",
  );
});

test("a card carries its time, location, claim, agenda and participant tags", async () => {
  renderSubTab();
  await screen.findAllByTestId("meeting-card");

  const upcoming = card(501);
  expect(within(upcoming).getByTestId("meeting-when")).toHaveTextContent("Phone");
  expect(within(upcoming).getByTestId("meeting-claim-ref")).toHaveTextContent(
    "WC-20017 — Marcus Webb",
  );
  expect(within(upcoming).getByTestId("meeting-notes")).toHaveTextContent("light-duty");
  expect(
    within(upcoming)
      .getAllByTestId("meeting-participant-tag")
      .map((tag) => tag.dataset.participant),
  ).toEqual(["employee", "employer_hr"]);
});

test("the email control ships disabled and states the story that enables it", async () => {
  // AC 5, in full: disabled, and the reason reachable without a pointer.
  renderSubTab();
  await screen.findAllByTestId("meeting-card");

  const email = within(card(501)).getByTestId("meeting-email");
  expect(email).toBeDisabled();
  expect(email).toHaveAttribute("title", EMAIL_SEAM_REASON);
  const describedBy = email.getAttribute("aria-describedby");
  expect(describedBy).not.toBeNull();
  expect(document.getElementById(describedBy!)).toHaveTextContent(EMAIL_SEAM_REASON);
});

test("a meeting already done offers no ✓, because there is no un-complete", async () => {
  renderSubTab();
  await screen.findAllByTestId("meeting-card");

  expect(within(card(501)).getByTestId("meeting-done")).toBeInTheDocument();
  expect(within(card(502)).queryByTestId("meeting-done")).not.toBeInTheDocument();
});

test("✓ Done sends the row's own version and announces the change politely", async () => {
  renderSubTab({ completeMeeting: MEETING_COMPLETED });
  await screen.findAllByTestId("meeting-card");

  await userEvent.click(within(card(501)).getByTestId("meeting-done"));

  await waitFor(() =>
    expect(screen.getByTestId("meetings-status")).toHaveTextContent("Meeting marked done."),
  );
  const patch = requested().find((request) => request.method === "PATCH");
  expect(patch).toBeDefined();
  // The **meeting's** version, off the payload — not the claim's, and not a
  // number this component invented.
  expect(JSON.parse(await patch!.clone().text())).toEqual({ expectedVersion: 1 });
});

test("Delete sends the version in the query string, where a proxy cannot drop it", async () => {
  renderSubTab({ deleteMeeting: { status: 204, body: null } });
  await screen.findAllByTestId("meeting-card");

  await userEvent.click(within(card(501)).getByTestId("meeting-delete"));

  await waitFor(() =>
    expect(screen.getByTestId("meetings-status")).toHaveTextContent("Meeting deleted."),
  );
  const removed = requested().find((request) => request.method === "DELETE");
  expect(removed?.url).toContain("expectedVersion=1");
});

test("a 409 refuses in place, on the card that caused it, and never retries", async () => {
  // The list answers the *pre-conflict* state throughout, so the only way the
  // card can end up `done` is the fresh entity the problem document carried —
  // which is exactly AD-9's rule: roll back, render what came back, do not
  // re-send.
  renderSubTab({ completeMeeting: MEETING_CONFLICT });
  await screen.findAllByTestId("meeting-card");

  await userEvent.click(within(card(501)).getByTestId("meeting-done"));

  const refusal = await within(card(501)).findByTestId("meeting-error");
  expect(refusal).toHaveTextContent("Changed by someone else");
  // The refusal belongs to *that* card: the other one is untouched.
  expect(within(card(502)).queryByTestId("meeting-error")).not.toBeInTheDocument();
  // One attempt, not two. Re-sending a completion against a row somebody has
  // already completed is the one thing this must never do.
  expect(requested().filter((request) => request.method === "PATCH")).toHaveLength(1);
  // …and the list is re-read, which is the half Stories 3.4 and 3.5 each got
  // wrong once: a 409 means the entity moved, so the list that offered the
  // button was generated from where it used to be.
  await waitFor(() =>
    expect(
      requested().filter(
        (request) => request.method === "GET" && request.url.includes("/claims-diary/meetings"),
      ).length,
    ).toBeGreaterThan(1),
  );
});

test("the fresh entity a 409 carries is what the card renders", async () => {
  // The same conflict, with the list answering once and then hanging: the
  // refetch the refusal triggers never settles, so the only thing that can
  // have moved the card to `done` is the problem document's `meeting` member.
  // Split from the test above because that one is about *not retrying* and
  // this one is about the entity — one failure, one reason.
  let served = 0;
  renderSubTab({
    completeMeeting: MEETING_CONFLICT,
    meetings: () => (served++ === 0 ? MEETINGS : "pending"),
  });
  await screen.findAllByTestId("meeting-card");

  await userEvent.click(within(card(501)).getByTestId("meeting-done"));

  await within(card(501)).findByTestId("meeting-error");
  expect(card(501)).toHaveAttribute("data-status", "done");
});

test("the count beside the list is the server's total, not the page's length", async () => {
  // `total` is the size of the whole diary. A page that showed two cards and
  // said "2" over a diary of sixty would be a list quietly lying about what it
  // holds — which is what rendering `items.length` here would do.
  renderSubTab({
    // `upcomingCount` is on every envelope the server can produce, so a fixture
    // without it is a response no server sends — and the greeting one sub-tab
    // over reads it. Two inline fixtures in this file omitted it.
    meetings: {
      status: 200,
      body: { items: [MEETING_UPCOMING], nextCursor: "cur", total: 60, upcomingCount: 41 },
    },
  });

  expect(await screen.findByTestId("meetings-count")).toHaveTextContent("60 meetings");
  expect(screen.getAllByTestId("meeting-card")).toHaveLength(1);
});

test("Show more appends the next page and then takes itself away", async () => {
  // The sort is ascending, so the rows past the first page are the *newest*
  // ones: a single-page read did not merely show less of a busy diary, it
  // dropped the meeting a handler had just scheduled off the end.
  renderSubTab({
    meetings: (url) =>
      url.includes("cursor=")
        ? { status: 200, body: { items: [MEETING_DONE], nextCursor: null, total: 2, upcomingCount: 1 } }
        : {
            status: 200,
            body: { items: [MEETING_UPCOMING], nextCursor: "cur", total: 2, upcomingCount: 1 },
          },
  });

  await screen.findAllByTestId("meeting-card");
  expect(screen.getAllByTestId("meeting-card")).toHaveLength(1);

  await userEvent.click(screen.getByTestId("meetings-more"));

  await waitFor(() => expect(screen.getAllByTestId("meeting-card")).toHaveLength(2));
  // Both pages are on screen at once — the second is appended, not swapped in.
  expect(card(501)).toBeInTheDocument();
  expect(card(502)).toBeInTheDocument();
  // …and the control is gone, because the server stopped issuing cursors.
  expect(screen.queryByTestId("meetings-more")).not.toBeInTheDocument();
});

test("a complete list offers no Show more", async () => {
  renderSubTab();
  await screen.findAllByTestId("meeting-card");

  expect(screen.queryByTestId("meetings-more")).not.toBeInTheDocument();
});

test("the live region is emptied when the scheduler is opened after a ✓", async () => {
  // `isSuccess` is sticky until that same mutation runs again, so a region
  // that was only cleared by the *sibling* reset kept announcing "Meeting
  // marked done." through an open-and-cancel of an unrelated modal.
  renderSubTab({ completeMeeting: MEETING_COMPLETED });
  await screen.findAllByTestId("meeting-card");

  await userEvent.click(within(card(501)).getByTestId("meeting-done"));
  await waitFor(() =>
    expect(screen.getByTestId("meetings-status")).toHaveTextContent("Meeting marked done."),
  );

  await userEvent.click(screen.getByTestId("meeting-schedule-open"));

  expect(screen.getByTestId("meetings-status")).toHaveTextContent("");
});

test("an empty diary says so, with the way to fix it underneath", async () => {
  renderSubTab({ meetings: MEETINGS_EMPTY });

  expect(await screen.findByTestId("meetings-empty")).toHaveTextContent("No meetings scheduled");
  expect(screen.getByTestId("meeting-schedule-open")).toBeInTheDocument();
});

test("loading and failure are two different pictures, and neither is a blank list", async () => {
  const { unmount } = renderSubTab({ meetings: "pending" });
  expect(await screen.findByTestId("meetings-loading")).toBeInTheDocument();
  unmount();
  vi.unstubAllGlobals();

  // A 4xx rather than a 500, so the assertion is about the branch and not
  // about the retry policy: `createQueryClient` retries 5xx twice with
  // backoff, which pushes the error state past the default `findBy` timeout.
  renderSubTab({
    meetings: {
      status: 400,
      body: {
        type: "/problems/invalid-cursor",
        title: "Bad Request",
        status: 400,
        detail: "The pagination cursor is not readable.",
      },
    },
  });
  expect(await screen.findByTestId("meetings-error")).toHaveTextContent("could not be loaded");
});

test("the ＋ button opens the scheduler with the workspace's claim already in it", async () => {
  renderSubTab();
  await screen.findAllByTestId("meeting-card");

  await userEvent.click(screen.getByTestId("meeting-schedule-open"));

  expect(await screen.findByTestId("meeting-scheduler")).toBeInTheDocument();
  expect(screen.getByTestId("scheduler-claim")).toHaveValue("WC-20017 — Marcus Delgado");
});

// --- the clock this list is judged at ------------------------------------

test("the list asks the server to judge it at the viewer's own day", async () => {
  // The unfiltered read had no clock at all: the server fell back to its own
  // UTC date while the Notes summary one sub-tab away sent the viewer's local
  // day, so the same meeting rendered `upcoming` with a ✓ Done control there
  // and greyed-out here for any handler whose date differs from UTC's. Nothing
  // asserted the parameter, and the stub ignored the query string, so deleting
  // it again would be silent.
  renderSubTab();
  await screen.findAllByTestId("meeting-card");

  const today = todayIso(new Date());
  const urls = requested().map((request) => request.url);
  expect(urls).not.toHaveLength(0);
  for (const url of urls) {
    expect(url).toContain(`asOf=${today}`);
    // …and **not** a `day`: this sub-tab reads the whole book.
    expect(url).not.toContain("day=");
  }
});

// --- errored with rows in hand -------------------------------------------

test("a failed refresh keeps the meetings already on screen", async () => {
  // TanStack keeps `data` when a refetch fails, and 4.2 made ✓ Done refetch on
  // 200 — so testing `isError` before the cache wiped the list the handler had
  // just ticked a row in, a fraction of a second after the tick succeeded.
  let calls = 0;
  renderSubTab({
    meetings: () => {
      calls += 1;
      // A 4xx rather than a 5xx: `createQueryClient` retries 5xx twice with
      // backoff, which pushes the failed state past the default `findBy`
      // timeout — this file's own note, one test up.
      return calls === 1
        ? MEETINGS
        : {
            status: 400,
            body: {
              type: "/problems/invalid-cursor",
              title: "Bad Request",
              status: 400,
              detail: "The pagination cursor is not readable.",
            },
          };
    },
  });
  await screen.findAllByTestId("meeting-card");

  await userEvent.click(within(card(501)).getByTestId("meeting-done"));

  expect(await screen.findByTestId("meetings-stale")).toBeInTheDocument();
  expect(screen.getAllByTestId("meeting-card")).not.toHaveLength(0);
  expect(screen.queryByTestId("meetings-error")).not.toBeInTheDocument();
});

// --- a row somebody else removed -----------------------------------------

test("a 404 on ✓ Done re-reads the list and does not invite a retry", async () => {
  // Fresh state was installed only when the error carried a `meeting`
  // extension, which only a 409 does — so a meeting deleted in another session
  // left a phantom card that 404s on every click, for ever, because
  // `refetchOnWindowFocus` is off and nothing else was going to ask.
  let calls = 0;
  renderSubTab({
    meetings: () => {
      calls += 1;
      // The row is gone from the server's second answer.
      return calls === 1
        ? MEETINGS
        : {
            status: 200,
            body: { items: [MEETING_DONE], nextCursor: null, total: 1, upcomingCount: 0 },
          };
    },
    completeMeeting: {
      status: 404,
      body: {
        type: "/problems/meeting-not-found",
        title: "Not Found",
        status: 404,
        detail: "No meeting 501 in your diary.",
      },
    },
  });
  await screen.findAllByTestId("meeting-card");

  await userEvent.click(within(card(501)).getByTestId("meeting-done"));

  // The card goes, because the refusal made the list re-read…
  await waitFor(() =>
    expect(
      screen.queryAllByTestId("meeting-card").some((node) => node.dataset.meetingId === "501"),
    ).toBe(false),
  );
  // …and while it was still there, what it said was not "try again".
  expect(screen.queryByText(/Try again in a moment/)).not.toBeInTheDocument();
});
