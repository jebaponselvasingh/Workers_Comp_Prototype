import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  PRIORITY_CLAIMS,
  PRIORITY_CLAIMS_EMPTY,
  PRIORITY_CLAIMS_PAGE_TWO,
  PRIORITY_CLAIMS_REORDERED,
  PRIORITY_CLAIMS_UNDER_CAP,
  stubApi,
} from "@/test/api-mock";

import { DashboardPage } from "./DashboardPage";

/**
 * Story 5.4 AC 1/2/3 — the worklist renders the server's rows and nothing of
 * its own.
 *
 * The line these tests police is the one `noDerivation.test.ts` polices
 * structurally: the table receives a page of an already-ranked list and may
 * format it, colour it and truncate it visually; it may not produce, re-order,
 * re-cut or re-band one. So the order assertion reads the rendered claim ids as
 * **one list** rather than as ten independent lookups — a table that rendered
 * every row in the wrong position would satisfy a per-row loop — and a
 * re-ordered contrast fixture renders through the same component. A table that
 * sorted would read identically under both; one that renders what it was sent
 * cannot.
 *
 * Rendered through `DashboardPage` with a fresh `createQueryClient()` per house
 * convention: the section takes its query state as props, so mounting it
 * directly would test a wiring nobody ships — and "a worklist failure leaves
 * the KPI cards, the handler table and the charts standing" would not be
 * assertable at all.
 */

function renderPage(routes: Parameters<typeof stubApi>[0]) {
  stubApi(routes);
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <DashboardPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/**
 * The ten headers, in the prototype's order (`renderSV`, line 1114).
 *
 * Asserted as one ordered constant rather than ten `toBeInTheDocument` calls,
 * because the order *is* the design contract: a table with the right ten
 * headers in the wrong order satisfies ten independent lookups and satisfies
 * nobody reading it.
 */
const COLUMN_HEADERS = [
  "Claim ID",
  "Worker",
  "Employer",
  "Injury Type",
  "Severity",
  "Fraud Score",
  "Handler",
  "Days Open",
  "Priority Next Best Action",
  "Status",
];

function renderedClaimIds(): string[] {
  return screen
    .getAllByTestId("priority-row")
    .map((row) => row.getAttribute("data-claim-id") ?? "");
}

test("the ten columns render in the prototype's order", async () => {
  renderPage({});

  const table = within(await screen.findByTestId("priority-claims")).getByRole("table");
  expect(
    within(table)
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent),
  ).toEqual(COLUMN_HEADERS);
});

test("every cell of every row is the value the server sent", async () => {
  renderPage({});
  await screen.findAllByTestId("priority-row");

  const rows = screen.getAllByTestId("priority-row");
  expect(rows).toHaveLength(PRIORITY_CLAIMS.body.items.length);

  for (const [index, expected] of PRIORITY_CLAIMS.body.items.entries()) {
    const cells = within(rows[index]).getAllByRole("cell").map((cell) => cell.textContent);
    // Read as one ordered list per row, keyed by position, so a component that
    // put the handler's name in the Worker column fails here rather than in a
    // lookup that would have found the string somewhere on the page.
    expect(cells, expected.claimId).toEqual([
      expected.claimId,
      expected.worker,
      expected.employerShortName,
      expected.injuryType,
      // The band's label, not its token — the UI owns display labels and the
      // wire carries snake_case.
      { high: "High", med: "Med", low: "Low" }[expected.severityBand],
      String(expected.fraudScore),
      expected.handlerName,
      String(expected.daysOpen),
      expected.nextBestAction,
      expected.litigationFlag
        ? "LITIG"
        : { intake: "Intake", investigation: "Investigation", treatment: "Treatment", settled: "Settled" }[
            expected.stage
          ],
    ]);
  }
});

test("the caption quotes the cap and the population the server published", async () => {
  renderPage({});

  const caption = await screen.findByTestId("priority-caption");
  // Both numbers off the payload: `cap` is the rule document's answer and
  // `total` is the population *before* it, so the sentence reads "top 30 of 38"
  // rather than "top 30 of 30" — which would be true, circular, and would tell
  // a supervisor nothing about how much of her book qualified.
  expect(caption).toHaveTextContent(
    `(showing top ${String(PRIORITY_CLAIMS.body.cap)} of ${String(PRIORITY_CLAIMS.body.total)})`,
  );
});

test("a book smaller than the cap is not told the cap cut it", async () => {
  // The regression this test exists for: a caption assembled from `cap` and
  // `total` alone tells Jennifer Park "showing top 30 of 8" — a cap that
  // removed nothing, quoted as though it had, promising thirty rows above a
  // table holding eight. Which sentence is true is a comparison of two
  // rule-decided numbers, so the server decides it and sends `truncated`;
  // `noDerivation.test.ts` is what stops this component deciding it instead.
  renderPage({ priorityClaims: PRIORITY_CLAIMS_UNDER_CAP });

  const caption = await screen.findByTestId("priority-caption");
  expect(caption).toHaveTextContent(`(${String(PRIORITY_CLAIMS_UNDER_CAP.body.total)} claims)`);
  expect(caption).not.toHaveTextContent("showing top");
  expect(caption).not.toHaveTextContent(String(PRIORITY_CLAIMS_UNDER_CAP.body.cap));
});

test("the LITIG chip appears on litigated rows and the stage pill on the rest", async () => {
  renderPage({});
  await screen.findAllByTestId("priority-row");

  const litigated = PRIORITY_CLAIMS.body.items.filter((row) => row.litigationFlag);
  // The fixture has to contain both kinds, or this test asserts nothing in one
  // direction and would pass over a component that always drew one chip.
  expect(litigated.length).toBeGreaterThan(0);
  expect(litigated.length).toBeLessThan(PRIORITY_CLAIMS.body.items.length);

  const chips = screen.getAllByTestId("priority-litig");
  expect(chips).toHaveLength(litigated.length);
  // The queue card's hover text, verbatim — one badge vocabulary across the two
  // surfaces that draw it.
  expect(chips[0]).toHaveAttribute("title", "In litigation");

  const rows = screen.getAllByTestId("priority-row");
  const STAGE_TEXT: Record<string, string> = {
    intake: "Intake",
    investigation: "Investigation",
    treatment: "Treatment",
    settled: "Settled",
  };
  for (const [index, row] of PRIORITY_CLAIMS.body.items.entries()) {
    const status = within(rows[index]).getByTestId("priority-status");
    // A litigated row draws the LITIG chip **instead of** its stage pill — the
    // prototype's own rule, and the reason both facts are on the wire rather
    // than one pre-resolved chip. Asserted as the cell's whole text, so a
    // component that drew both would fail rather than pass a substring check.
    expect(status.textContent, row.claimId).toBe(
      row.litigationFlag ? "LITIG" : STAGE_TEXT[row.stage],
    );
  }
});

test("the fraud score is tinted by the server's flag, not by its own value", async () => {
  renderPage({});
  await screen.findAllByTestId("priority-row");

  const rows = screen.getAllByTestId("priority-row");
  for (const [index, row] of PRIORITY_CLAIMS.body.items.entries()) {
    const cell = within(rows[index]).getByTestId("priority-fraud-score");
    const tint = cell.querySelector("span")?.className ?? "";
    // Two tones and two only. The prototype bands this cell into three from two
    // cut-offs written into its render function — one of which is a real JDM
    // parameter published on this very payload — and reproducing that would be
    // the browser re-deciding a band the server already decided.
    expect(tint.includes("text-warn"), row.claimId).toBe(row.fraudFlagged);
  }
});

test("the injury type and the action carry their full text in a title", async () => {
  renderPage({});
  await screen.findAllByTestId("priority-row");

  const rows = screen.getAllByTestId("priority-row");
  for (const [index, row] of PRIORITY_CLAIMS.body.items.entries()) {
    // The prototype cuts these at 22 and 48 characters *in JavaScript*.
    // Truncation is a property of the column, not of the claim, so the whole
    // string is in the DOM and in `title`, and CSS does the clamping.
    expect(
      within(rows[index]).getByTestId("priority-injury").querySelector("span"),
    ).toHaveAttribute("title", row.injuryType);
    expect(
      within(rows[index]).getByTestId("priority-next-action").querySelector("span"),
    ).toHaveAttribute("title", row.nextBestAction);
  }
});

test("a re-ordered payload renders re-ordered — the table never sorts", async () => {
  renderPage({ priorityClaims: PRIORITY_CLAIMS_REORDERED });
  await screen.findAllByTestId("priority-row");

  // The contrast fixture is `PRIORITY_CLAIMS` reversed, which is the one
  // permutation every plausible client-side sort would *undo*: a table that
  // re-imposed the priority ranking — by severity, by fraud score, by days
  // open, by claim id — would produce the original order from this payload.
  expect(renderedClaimIds()).toEqual(
    PRIORITY_CLAIMS_REORDERED.body.items.map((row) => row.claimId),
  );
  expect(renderedClaimIds()).not.toEqual(
    PRIORITY_CLAIMS.body.items.map((row) => row.claimId),
  );
});

test("a pending request draws skeleton rows and says the section is busy", async () => {
  renderPage({ priorityClaims: "pending" });

  const section = await screen.findByTestId("priority-claims");
  await waitFor(() => {
    expect(screen.getAllByTestId("priority-row-skeleton")).toHaveLength(10);
  });
  expect(section).toHaveAttribute("aria-busy", "true");
  // No figures on screen while none are known — the heading and the columns
  // stand, and the caption does not, because there is no honest number for it.
  expect(screen.queryAllByTestId("priority-row")).toHaveLength(0);
  expect(screen.queryByTestId("priority-caption")).toBeNull();
});

test("an empty book renders its own sentence rather than an empty table", async () => {
  renderPage({ priorityClaims: PRIORITY_CLAIMS_EMPTY });

  expect(await screen.findByTestId("priority-claims-empty")).toBeVisible();
  expect(screen.queryAllByTestId("priority-row")).toHaveLength(0);
  // A headless table would read as "nothing needs attention" too, and would be
  // indistinguishable from a failure. The sentence is the point. Scoped to this
  // section rather than to the page, because the handler benchmark table is a
  // sibling and is unaffected by an empty worklist.
  const section = screen.getByTestId("priority-claims");
  expect(within(section).queryByRole("table")).toBeNull();
  expect(screen.queryByTestId("priority-claims-more")).toBeNull();
});

test("a failed request shows an inline alert and leaves the other sections standing", async () => {
  renderPage({ priorityClaims: { status: 404, body: {} } });

  const alert = await screen.findByTestId("priority-claims-error");
  expect(alert).toHaveAttribute("role", "alert");
  expect(screen.queryAllByTestId("priority-row")).toHaveLength(0);
  expect(await screen.findByTestId("priority-claims")).toHaveAttribute("aria-busy", "false");
  // Four server answers, four failure modes (NFR-3). The three sections above
  // are unaffected — which is the whole reason the dashboard is four queries.
  expect(await screen.findByTestId("kpi-total-claims")).toBeVisible();
  expect(await screen.findByTestId("handler-benchmarks")).toBeVisible();
  expect(screen.queryByTestId("portfolio-summary-error")).toBeNull();
});

test("Show more appends the next page and then disappears", async () => {
  const user = userEvent.setup();
  renderPage({
    // The cursor decides the page: the first request carries none and gets page
    // one, the second carries the server's `nextCursor` and gets page two. That
    // is what `StubRouteFor` is for on this route.
    priorityClaims: (url) =>
      url.includes("cursor=") ? PRIORITY_CLAIMS_PAGE_TWO : PRIORITY_CLAIMS,
  });

  await screen.findAllByTestId("priority-row");
  expect(renderedClaimIds()).toEqual(PRIORITY_CLAIMS.body.items.map((row) => row.claimId));

  await user.click(screen.getByTestId("priority-claims-more"));

  await waitFor(() => {
    expect(screen.getAllByTestId("priority-row")).toHaveLength(
      PRIORITY_CLAIMS.body.items.length + PRIORITY_CLAIMS_PAGE_TWO.body.items.length,
    );
  });
  // Appended in the order the server sent them, with no repeats — the two
  // sources overlap whenever the base query refetches, which is why the
  // component dedupes on the business id.
  expect(renderedClaimIds()).toEqual([
    ...PRIORITY_CLAIMS.body.items.map((row) => row.claimId),
    ...PRIORITY_CLAIMS_PAGE_TWO.body.items.map((row) => row.claimId),
  ]);
  expect(new Set(renderedClaimIds()).size).toBe(renderedClaimIds().length);
  // Page two carries no cursor, so the button goes — the server decides when
  // the list is finished, and nothing here counts rows against the cap.
  await waitFor(() => {
    expect(screen.queryByTestId("priority-claims-more")).toBeNull();
  });
  // …and the caption still reads the same population. `total` is counted before
  // the cap and does not move as the table grows.
  expect(screen.getByTestId("priority-caption")).toHaveTextContent(
    `of ${String(PRIORITY_CLAIMS.body.total)})`,
  );
});

test("a rejected second page keeps the loaded rows and offers a section reload", async () => {
  const user = userEvent.setup();
  renderPage({
    priorityClaims: (url) =>
      url.includes("cursor=") ? { status: 400, body: {} } : PRIORITY_CLAIMS,
  });

  await screen.findAllByTestId("priority-row");
  await user.click(screen.getByTestId("priority-claims-more"));

  const alert = await screen.findByTestId("priority-claims-page-error");
  expect(alert).toHaveAttribute("role", "alert");
  // The already-loaded rows stay. A cursor refusal is a statement about the
  // *next* page, not about the ten rows on screen.
  expect(renderedClaimIds()).toEqual(PRIORITY_CLAIMS.body.items.map((row) => row.claimId));
  // And the only recovery offered reloads the section rather than replaying the
  // rejected cursor, which would fail identically for ever.
  expect(screen.getByTestId("priority-claims-reload")).toBeVisible();
  expect(screen.queryByTestId("priority-claims-more")).toBeNull();
});
