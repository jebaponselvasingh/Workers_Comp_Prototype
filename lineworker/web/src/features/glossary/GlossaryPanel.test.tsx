import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { GLOSSARY_EMPTY, GLOSSARY_TERMS, stubApi } from "@/test/api-mock";

import { GlossaryPanel } from "./GlossaryPanel";

/**
 * The panel is a controlled component (the top bar owns `open`), so the
 * harness supplies the state the bar would — nothing here reaches into the
 * component, and every test drives it the way a user does: through the
 * trigger button, the search box, ✕ and the backdrop.
 */
function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <GlossaryPanel open={open} onOpenChange={setOpen}>
      <button type="button">📖 Glossary</button>
    </GlossaryPanel>
  );
}

function renderPanel(routes: Parameters<typeof stubApi>[0]) {
  stubApi(routes);
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <Harness />
    </QueryClientProvider>,
  );
}

const TERMS = GLOSSARY_TERMS.body.items;

/** Click the trigger and wait for the seeded rows to arrive. */
async function openPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /Glossary/ }));
  await waitFor(() => expect(screen.getAllByTestId("glossary-term")).toHaveLength(TERMS.length));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the button opens a panel listing every term the server sent (AC 1)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });

  expect(screen.queryByTestId("glossary-panel")).not.toBeInTheDocument();
  await openPanel(user);

  expect(screen.getByTestId("glossary-panel")).toBeVisible();
  expect(screen.getByText("WC Glossary — Manufacturing")).toBeVisible();
  // Term, abbreviation chip and definition — the prototype's row anatomy.
  expect(screen.getByText("Maximum Medical Improvement")).toBeVisible();
  expect(screen.getByText("MMI")).toBeVisible();
  expect(screen.getByText(TERMS[1].definition)).toBeVisible();
});

test("the panel is not fetched until it is first opened", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });

  const fetchMock = vi.mocked(globalThis.fetch);
  const glossaryCalls = () =>
    fetchMock.mock.calls.filter(([input]) => String((input as Request).url).includes("/glossary"));

  expect(glossaryCalls()).toHaveLength(0);
  await openPanel(user);
  expect(glossaryCalls().length).toBeGreaterThan(0);
});

test("the search box has focus as soon as the panel opens (UX-DR10)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  await waitFor(() => expect(screen.getByTestId("glossary-search")).toHaveFocus());
});

test("typing an abbreviation filters to that term (AC 2)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  // Lower case against an upper-case abbreviation: the predicate is
  // case-insensitive, which is the only way "havs" is a usable search.
  await user.type(screen.getByTestId("glossary-search"), "havs");

  await waitFor(() => expect(screen.getAllByTestId("glossary-term")).toHaveLength(1));
  expect(screen.getByText("Hand-Arm Vibration Syndrome")).toBeVisible();
});

test("typing part of a term name filters to that term (AC 2)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  await user.type(screen.getByTestId("glossary-search"), "maximum medical");

  await waitFor(() => expect(screen.getAllByTestId("glossary-term")).toHaveLength(1));
  expect(screen.getByTestId("glossary-term-abbr")).toHaveTextContent("MMI");
});

test("a word that appears only in a definition still finds its term (FR-GLOS-1)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  // "audiogram" is in no abbreviation and no term name — it is the case
  // that proves definition text is searched rather than merely displayed.
  await user.type(screen.getByTestId("glossary-search"), "audiogram");

  await waitFor(() => expect(screen.getAllByTestId("glossary-term")).toHaveLength(1));
  expect(screen.getByTestId("glossary-term-name")).toHaveTextContent("Noise-Induced Hearing Loss");
});

test("no matches shows the query back, not an empty void (AC 2, NFR-3)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  await user.type(screen.getByTestId("glossary-search"), "zzzz");

  await waitFor(() => expect(screen.getByTestId("glossary-empty")).toBeVisible());
  // The echo is the point: it confirms what was actually searched for.
  expect(screen.getByTestId("glossary-empty")).toHaveTextContent('No matches for "zzzz".');
  expect(screen.queryAllByTestId("glossary-term")).toHaveLength(0);
});

test("an empty glossary says so rather than blaming the query (NFR-3)", async () => {
  const user = userEvent.setup();
  // A 200 with no rows: 0006 applied without 0007, or a wiped table. The
  // request succeeded, so there is no error to show — and the branch order
  // decides what the user is told.
  renderPanel({ glossary: GLOSSARY_EMPTY });

  await user.click(screen.getByRole("button", { name: /Glossary/ }));
  await waitFor(() => expect(screen.getByTestId("glossary-unavailable")).toBeVisible());

  await user.type(screen.getByTestId("glossary-search"), "mmi");

  // Still "unavailable", never `No matches for "mmi".` — the two states
  // carry different test ids precisely so this assertion can exist. Telling
  // a handler their term does not exist, when in fact the table is empty,
  // is the same lie the error branch is written to avoid, arriving via 200.
  expect(screen.getByTestId("glossary-unavailable")).toBeVisible();
  expect(screen.queryByTestId("glossary-empty")).not.toBeInTheDocument();
});

test("a query that matches nothing is a different state from an empty glossary (AC 2)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  await user.type(screen.getByTestId("glossary-search"), "zzzz");

  // The other half of the pair above: terms exist, this query found none.
  await waitFor(() => expect(screen.getByTestId("glossary-empty")).toBeVisible());
  expect(screen.queryByTestId("glossary-unavailable")).not.toBeInTheDocument();
});

test("the result count is announced to assistive technology as the filter runs (NFR-3)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  // Filtering is the one interaction here with no focus change and no
  // visible-to-a-screen-reader consequence: without a live region the list
  // silently becomes one row and nothing is said.
  const announcement = screen.getByTestId("glossary-announcement");
  expect(announcement).toHaveAttribute("aria-live", "polite");
  await waitFor(() => expect(announcement).toHaveTextContent(`${TERMS.length} terms.`));

  await user.type(screen.getByTestId("glossary-search"), "havs");

  await waitFor(() =>
    expect(announcement).toHaveTextContent(`1 of ${TERMS.length} terms match.`),
  );
});

test("the in-flight list is marked busy, not merely empty (NFR-3)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: "pending" });

  await user.click(screen.getByRole("button", { name: /Glossary/ }));

  // The skeletons are aria-hidden, so without `aria-busy` the region is
  // indistinguishable from a glossary with nothing in it.
  await waitFor(() =>
    expect(screen.getByTestId("glossary-announcement")).toHaveTextContent("Loading"),
  );
  expect(screen.getAllByTestId("glossary-skeleton")[0].closest("[aria-busy]")).toHaveAttribute(
    "aria-busy",
    "true",
  );
});

test("a whitespace-only query shows the whole list, and a trailing space still matches", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  // The trim is a *deliberate* deviation from the prototype's `renderGloss`
  // (see `matches()`): terms get pasted out of claim notes and adjuster
  // email with a space attached, and answering "no matches" to `"havs "` is
  // a lie about the data. Pinned here so it stays a decision rather than
  // becoming an accident somebody "fixes".
  const search = screen.getByTestId("glossary-search");
  await user.type(search, "   ");
  await waitFor(() => expect(screen.getAllByTestId("glossary-term")).toHaveLength(TERMS.length));

  await user.clear(search);
  await user.type(search, "havs ");
  await waitFor(() => expect(screen.getAllByTestId("glossary-term")).toHaveLength(1));
  expect(screen.getByText("Hand-Arm Vibration Syndrome")).toBeVisible();
});

test("clearing the query restores the full list (AC 2)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  const search = screen.getByTestId("glossary-search");
  await user.type(search, "havs");
  await waitFor(() => expect(screen.getAllByTestId("glossary-term")).toHaveLength(1));

  await user.clear(search);
  await waitFor(() => expect(screen.getAllByTestId("glossary-term")).toHaveLength(TERMS.length));
});

test("reopening resets the search, the way the prototype's renderGloss(\"\") does", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  await user.type(screen.getByTestId("glossary-search"), "havs");
  await waitFor(() => expect(screen.getAllByTestId("glossary-term")).toHaveLength(1));

  await user.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByTestId("glossary-panel")).not.toBeInTheDocument());

  await openPanel(user);
  expect(screen.getByTestId("glossary-search")).toHaveValue("");
});

test("✕ closes the panel and hands focus back to the button (AC 1, Task 3)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  await user.click(screen.getByRole("button", { name: "Close" }));

  await waitFor(() => expect(screen.queryByTestId("glossary-panel")).not.toBeInTheDocument());
  // Focus on the trigger, not lost to <body>: a keyboard user must be able
  // to carry on from where they were.
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /Glossary/ })).toHaveFocus(),
  );
});

test("clicking the backdrop closes the panel (AC 1)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  await user.click(screen.getByTestId("glossary-backdrop"));

  await waitFor(() => expect(screen.queryByTestId("glossary-panel")).not.toBeInTheDocument());
});

test("rows are replaced by skeletons while the terms are in flight (NFR-3)", async () => {
  const user = userEvent.setup();
  // A route that never settles: a loading state raced against a fast stub
  // is not observable, and "loading" and "empty glossary" must not look
  // alike.
  renderPanel({ glossary: "pending" });

  await user.click(screen.getByRole("button", { name: /Glossary/ }));

  await waitFor(() => expect(screen.getAllByTestId("glossary-skeleton").length).toBeGreaterThan(0));
  expect(screen.queryAllByTestId("glossary-term")).toHaveLength(0);
  expect(screen.queryByTestId("glossary-empty")).not.toBeInTheDocument();
});

test("a failed request says so instead of rendering an empty glossary (NFR-3)", async () => {
  const user = userEvent.setup();
  renderPanel({
    // A non-retryable 404 (the proxy case `client.ts` documents), not a
    // 500: the shared QueryClient retries 5xx twice, so a 500 would make
    // this assertion race the backoff instead of testing the branch.
    glossary: { status: 404, body: { title: "Not Found", status: 404, detail: "no route" } },
  });

  await user.click(screen.getByRole("button", { name: /Glossary/ }));

  await waitFor(() => expect(screen.getByTestId("glossary-error")).toBeVisible());
  // `alert`, not `status`: this message replaces everything the panel was
  // opened for, and a polite region waits for a lull that a user typing
  // into the search box never gives it.
  expect(screen.getByTestId("glossary-error")).toHaveAttribute("role", "alert");
  // Not "no matches" — the difference between "this term does not exist"
  // and "we could not reach the glossary" matters to a new handler.
  expect(screen.queryByTestId("glossary-empty")).not.toBeInTheDocument();
  expect(screen.queryByTestId("glossary-unavailable")).not.toBeInTheDocument();
  expect(screen.queryAllByTestId("glossary-term")).toHaveLength(0);
});

test("the prototype's two data quirks render as they are (Design Notes)", async () => {
  const user = userEvent.setup();
  renderPanel({ glossary: GLOSSARY_TERMS });
  await openPanel(user);

  // A multi-word abbreviation in the chip...
  expect(screen.getByText("OSHA 300")).toBeVisible();
  // ...and a term whose abbreviation repeats it, which renders as two
  // elements with the same text rather than being deduplicated away.
  expect(screen.getAllByText("Apportionment")).toHaveLength(2);
});
