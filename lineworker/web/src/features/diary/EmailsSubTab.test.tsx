/**
 * Story 4.3 AC 4 — the sent log, its four states, and its one control.
 *
 * Most assertions here are of one kind: the payload said X, the screen says X.
 * That is the point of a list whose order, count and every field arrived decided
 * (AD-1) — what a component test can prove is that none of it was recomputed on
 * the way to the DOM. The fixture is deliberately awkward for that: `total` is
 * published separately from `items`, one row is claim-linked and one is not, one
 * has a null body, and the "Sent" badge is a formatted instant rather than a
 * delivery state.
 *
 * The rest are the states the server cannot describe — loading, error, empty and
 * stale (NFR-3) — plus the ＋ button, which is the only thing on this surface a
 * handler can press.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  EMAIL_LOGS,
  EMAIL_LOGS_EMPTY,
  EMAIL_LOG_FREE,
  EMAIL_LOG_TAGGED,
  ME_HANDLER,
  type StubRoutes,
  stubApi,
} from "@/test/api-mock";

import { DiaryNavProvider, useDiaryNav } from "./DiaryNav";
import { EmailsSubTab } from "./EmailsSubTab";

/** A 4xx rather than a 5xx: `createQueryClient` retries 5xx twice with backoff,
 * which pushes the error state past the default `findBy` timeout. */
const BAD_REQUEST = {
  status: 400,
  body: {
    type: "/problems/invalid-cursor",
    title: "Bad Request",
    status: 400,
    detail: "The pagination cursor is not readable.",
  },
};

/** The composer state as text — the ＋ button's observable effect from here. */
function ComposerProbe() {
  const { composer } = useDiaryNav();
  return <p data-testid="composer-state">{composer.open ? "open" : "closed"}</p>;
}

function renderSubTab(routes: StubRoutes = {}) {
  stubApi({ me: ME_HANDLER, emails: EMAIL_LOGS, ...routes });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <DiaryNavProvider>
        <EmailsSubTab />
        <ComposerProbe />
      </DiaryNavProvider>
    </QueryClientProvider>,
  );
}

/**
 * What the "Sent" badge must read, **restated rather than borrowed**.
 *
 * The assertion below compared the DOM against `formatSentAt(…)` — the
 * production formatter the card itself calls — so the two sides moved together
 * and a `formatSentAt` returning `""` passed. This is the same instant put
 * through `Intl` independently, which is what an oracle is: it disagrees with a
 * broken formatter, and it still does not name a timezone the suite may not be
 * running in. `sentAt` is a UTC instant and the badge renders it in local time,
 * which is the behaviour — the card is read by the person who sent the email.
 */
const SENT_BADGE = new Date(EMAIL_LOG_TAGGED.sentAt).toLocaleString("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/** One card by its server-assigned id — never by index (Story 3.5's rule). */
function card(id: number): HTMLElement {
  const found = screen
    .getAllByTestId("email-card")
    .find((element) => element.dataset.emailId === String(id));
  if (!found) throw new Error(`no email card ${id} is rendered`);
  return found;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("a card carries the subject, the sent badge, the recipients and a snippet", async () => {
  renderSubTab();
  await screen.findAllByTestId("email-card");

  const tagged = card(801);
  expect(within(tagged).getByTestId("email-subject")).toHaveTextContent(
    `✉ ${EMAIL_LOG_TAGGED.subject}`,
  );
  // The badge is `sentAt` formatted — **not** a delivery state. Compared against
  // an independent restatement of the format rather than against the formatter
  // under test; see `SENT_BADGE`.
  expect(within(tagged).getByTestId("email-sent-badge")).toHaveTextContent(
    `Sent ${SENT_BADGE}`,
  );
  // The badge really says something: an empty format would satisfy a comparison
  // against the production formatter, which is what this assertion used to be.
  expect(SENT_BADGE).toContain("Aug");
  // `To: …` with the claim's worker after a middot when the row names a claim.
  expect(within(tagged).getByTestId("email-recipients")).toHaveTextContent(
    "To: Employee, Employer HR, NCM · Marcus Webb",
  );
  const snippet = within(tagged).getByTestId("email-snippet");
  expect(snippet).toHaveTextContent("Dear Marcus Webb");
  // A hundred characters and a trailing ellipsis — the prototype's cut.
  expect(snippet.textContent!.endsWith("…")).toBe(true);
  expect([...snippet.textContent!]).toHaveLength(101);
});

test("the newest send is first, and the order is the server's", async () => {
  renderSubTab();
  await screen.findAllByTestId("email-card");

  expect(
    screen.getAllByTestId("email-card").map((node) => Number(node.dataset.emailId)),
  ).toEqual([801, 800]);
});

test("a free composition renders no claim, no worker and no snippet", async () => {
  // `claimId`/`workerName` are nullable together (the ERD's optional edge) and
  // `body` is nullable on its own — the branches a card is most likely to render
  // as `· null` or as a lone ellipsis.
  renderSubTab();
  await screen.findAllByTestId("email-card");

  const free = card(800);
  expect(free).toHaveAttribute("data-claim-id", "");
  expect(within(free).getByTestId("email-recipients")).toHaveTextContent("To: Supervisor");
  expect(within(free).getByTestId("email-recipients")).not.toHaveTextContent("·");
  expect(within(free).queryByTestId("email-snippet")).not.toBeInTheDocument();
});

test("priority is a chip only when it is not normal", async () => {
  renderSubTab();
  await screen.findAllByTestId("email-card");

  // Most email is normal, and a chip on every card would say nothing.
  expect(within(card(801)).queryByTestId("email-priority")).not.toBeInTheDocument();
  expect(within(card(800)).getByTestId("email-priority")).toHaveTextContent("Urgent");
  expect(card(800)).toHaveAttribute("data-priority", EMAIL_LOG_FREE.priority);
});

test("the count beside the list is the server's total, not the page's length", async () => {
  renderSubTab({
    emails: {
      status: 200,
      body: { items: [EMAIL_LOG_TAGGED], nextCursor: "cur", total: 60 },
    },
  });

  expect(await screen.findByTestId("emails-count")).toHaveTextContent("60 emails");
  expect(screen.getAllByTestId("email-card")).toHaveLength(1);
});

test("Show more appends the next page and then takes itself away", async () => {
  // The sort is descending, so the rows past the first page are the *oldest* —
  // the kinder direction, and exactly why the control has to exist at all: a
  // handler looking for what they sent last month must be able to reach it.
  renderSubTab({
    emails: (url) =>
      url.includes("cursor=")
        ? {
            status: 200,
            // `total` is null on a cursor page, which is the server's own
            // contract: the count is issued only when no cursor was supplied.
            body: { items: [EMAIL_LOG_FREE], nextCursor: null, total: null },
          }
        : {
            status: 200,
            body: { items: [EMAIL_LOG_TAGGED], nextCursor: "cur", total: 2 },
          },
  });

  await screen.findAllByTestId("email-card");
  expect(screen.getAllByTestId("email-card")).toHaveLength(1);

  await userEvent.click(screen.getByTestId("emails-more"));

  await waitFor(() => expect(screen.getAllByTestId("email-card")).toHaveLength(2));
  // The count still reads the first page's number rather than going blank when
  // the second page answered `null`.
  expect(screen.getByTestId("emails-count")).toHaveTextContent("2 emails");
  expect(screen.queryByTestId("emails-more")).not.toBeInTheDocument();
});

test("a complete log offers no Show more", async () => {
  renderSubTab();
  await screen.findAllByTestId("email-card");

  expect(screen.queryByTestId("emails-more")).not.toBeInTheDocument();
});

test("an empty log says so in the prototype's own words, with the way to fix it", async () => {
  renderSubTab({ emails: EMAIL_LOGS_EMPTY });

  expect(await screen.findByTestId("emails-empty")).toHaveTextContent(
    "No emails sent yet. Use the button below to compose.",
  );
  expect(screen.getByTestId("email-compose-open")).toHaveTextContent(
    "＋ Compose Email to Stakeholders",
  );
});

test("loading and failure are two different pictures, and neither is a blank list", async () => {
  const { unmount } = renderSubTab({ emails: "pending" });
  expect(await screen.findByTestId("emails-loading")).toBeInTheDocument();
  unmount();
  vi.unstubAllGlobals();

  renderSubTab({ emails: BAD_REQUEST });
  expect(await screen.findByTestId("emails-error")).toHaveTextContent("could not be loaded");
});

test("a failed refresh keeps the emails already on screen", async () => {
  // TanStack keeps `data` when a refetch or a later page fails, so testing
  // `isError` before the cache would blank a populated log on a transient
  // failure — including in the second after a send, which invalidates the list.
  renderSubTab({
    emails: (url) => (url.includes("cursor=") ? BAD_REQUEST : { status: 200, body: { items: [EMAIL_LOG_TAGGED], nextCursor: "cur", total: 2 } }),
  });
  await screen.findAllByTestId("email-card");

  await userEvent.click(screen.getByTestId("emails-more"));

  expect(await screen.findByTestId("emails-stale")).toBeInTheDocument();
  expect(screen.getAllByTestId("email-card")).not.toHaveLength(0);
  expect(screen.queryByTestId("emails-error")).not.toBeInTheDocument();
});

test("the ＋ button opens the composer", async () => {
  // The dialog itself is mounted by `DiaryTab` — see its docstring on why —
  // so what belongs to this click is the shared state it flips.
  renderSubTab();
  await screen.findAllByTestId("email-card");
  expect(screen.getByTestId("composer-state")).toHaveTextContent("closed");

  await userEvent.click(screen.getByTestId("email-compose-open"));

  expect(screen.getByTestId("composer-state")).toHaveTextContent("open");
});

test("a card opens nothing — there is no thread and no reply", async () => {
  // The prototype's `.email-card` carries `cursor: pointer` and no handler.
  // There is no `GET /emails/{id}`, so a clickable card would be the dead click
  // NFR-3 forbids.
  renderSubTab();
  await screen.findAllByTestId("email-card");

  expect(within(card(801)).queryByRole("button")).not.toBeInTheDocument();
  expect(within(card(801)).queryByRole("link")).not.toBeInTheDocument();
});
