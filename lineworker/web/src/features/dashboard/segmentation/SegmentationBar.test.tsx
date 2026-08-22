import { MemoryRouter, useLocation } from "react-router";
import userEvent from "@testing-library/user-event";

import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { FILTER_KEYS, SEGMENTATION_KEYS } from "@/features/dashboard/drill/filters";
import {
  SEGMENTATION_VALUES,
  SEGMENTATION_VALUES_EMPTY,
  SEGMENTATION_VALUES_FILTERED,
  SEGMENTATION_VALUES_OUT_OF_BOOK,
  SEGMENTATION_VALUES_REFUSED,
  stubApi,
} from "@/test/api-mock";

import { SegmentationBar } from "./SegmentationBar";

/**
 * Story 7.3 AC 1/3/4/5 — the segmentation control writes a URL and renders the
 * server's reading of it.
 *
 * The line these tests police is the one every Epic 7 suite polices, and this
 * surface strains it in a direction none of the others did: the browser is
 * handed **the vocabulary of the caller's whole book** beside the filter that is
 * currently applied and the count of what survives it. Narrowing the options to
 * what is still reachable, turning the two counts into a percentage, and
 * deciding emptiness from the number of options are each one line — and the
 * first of them is a behaviour the *server* explicitly refuses, because a picker
 * cut by its own filter cannot be used to widen one.
 *
 * So `SEGMENTATION_VALUES_FILTERED` is the substance rather than a nicety: it
 * carries the *same* options as the unfiltered fixture and differs only in its
 * chips and its count, so a bar that filtered its own options renders three
 * sectors where three belong on one fixture and one where three belong on the
 * other.
 *
 * **The URL is the assertion, not component state.** Every interaction below is
 * checked by reading `location.search` through a probe, because that is what a
 * shared link carries and what the sections beside this bar re-key on: a control
 * that set local state and refetched would satisfy every rendering assertion and
 * fail AC 3.
 *
 * **Rendered through `SegmentationBar`, inside a `MemoryRouter`.** The bar is
 * mounted by `DashboardShell` on the analyst's section routes; what it needs
 * from that shell is a router and a query client, and nothing else — which is
 * itself the reason it is a component rather than a branch in the shell's JSX.
 */

/** The address bar, as a test reads it. */
function SearchProbe() {
  const { search } = useLocation();
  return <span data-testid="search">{search}</span>;
}

function renderBar(routes: Parameters<typeof stubApi>[0], initial = "/dashboard/fraud") {
  stubApi(routes);
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <QueryClientProvider client={createQueryClient()}>
        <SegmentationBar />
        <SearchProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** What the bar has drawn, as text — the chips in the server's order. */
function chipTexts(): string[] {
  return screen.getAllByTestId("drill-chip").map((chip) => chip.textContent ?? "");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("the ten dimensions are a subsequence of the drill list's twenty-four", () => {
  // The property `SEGMENTATION_KEYS` is written out for rather than filtered off
  // `FILTER_KEYS`: a `.filter()` would return `FilterKey[]` and erase the literal
  // union, so `SegmentationKey` would stop being narrower than `FilterKey` and a
  // picker could be built for a facet this control does not own. The cost of
  // writing it out is that the order can drift, and this is what stops it —
  // which matters because it is the order both chip rows are drawn in, so a
  // divergence would put the workspace's chips and the drill list's in different
  // sequences for one filter.
  const owned = new Set<string>(SEGMENTATION_KEYS);
  expect([...SEGMENTATION_KEYS]).toEqual(FILTER_KEYS.filter((key) => owned.has(key)));
});

test("a picker offers exactly the values the server published, in its order", async () => {
  renderBar({ segmentationValues: SEGMENTATION_VALUES });

  const sector = await screen.findByTestId("segmentation-picker-sector");
  expect(
    within(sector)
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["Any", "Aerospace", "Automotive", "Heavy Equipment"]);

  // The two *derived* dimensions offer only the bands some claim is in — which
  // is the endpoint's rule and the one a hardcoded vocabulary gets wrong: a bar
  // that listed `RiskBand`'s three members would offer "Low" over a book with
  // no low-severity claim in it.
  const severity = await screen.findByTestId("segmentation-picker-severityBand");
  expect(
    within(severity)
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["Any", "High", "Medium"]);
});

test("an age band reads as a range composed from the published edges", async () => {
  renderBar({ segmentationValues: SEGMENTATION_VALUES });

  const ages = await screen.findByTestId("segmentation-picker-ageGroup");

  // The wire values carry no numbers at all — deliberately, so that moving an
  // edge in `derivation_thresholds` cannot leave a member name asserting the old
  // one — so the range a reader sees is built here from the three edges the
  // payload published. The *values* stay the ordinal words, which is what the
  // URL carries.
  expect(
    within(ages)
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["Any", "35–44", "45–54"]);
  expect(
    within(ages)
      .getAllByRole("option")
      .map((option) => option.getAttribute("value")),
  ).toEqual(["", "younger", "older"]);
});

test("the employer picker shows the server's names and never the ids", async () => {
  renderBar({ segmentationValues: SEGMENTATION_VALUES });

  const employers = await screen.findByTestId("segmentation-picker-employerId");

  // The one dimension whose options carry a label, because an id is not a name
  // and nothing here can turn `4` into "Boeing" — and in the server's order,
  // which is by the label a reader sees rather than by the id.
  expect(
    within(employers)
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["Any", "Boeing", "Caterpillar"]);
});

test("choosing a value writes the API's own filter parameter into the URL", async () => {
  renderBar({ segmentationValues: SEGMENTATION_VALUES });

  await userEvent.selectOptions(
    await screen.findByTestId("segmentation-picker-sector"),
    "Aerospace",
  );

  // The drill list's own spelling, character for character: that is what makes
  // an active segmentation survive a click into it as a *merge* rather than as a
  // translation.
  await waitFor(() =>
    expect(screen.getByTestId("search")).toHaveTextContent(
      "filter%5Bsector%5D=Aerospace",
    ),
  );
});

test("two dimensions compose, and the second does not clear the first", async () => {
  renderBar({ segmentationValues: SEGMENTATION_VALUES });

  await userEvent.selectOptions(
    await screen.findByTestId("segmentation-picker-sector"),
    "Aerospace",
  );
  await userEvent.selectOptions(
    await screen.findByTestId("segmentation-picker-severityBand"),
    "high",
  );

  await waitFor(() => {
    const search = screen.getByTestId("search").textContent ?? "";
    expect(search).toContain("filter%5Bsector%5D=Aerospace");
    expect(search).toContain("filter%5BseverityBand%5D=high");
  });
});

test("a section control in the URL survives a filter change", async () => {
  renderBar({ segmentationValues: SEGMENTATION_VALUES }, "/dashboard/trends?grain=quarter");

  await userEvent.selectOptions(
    await screen.findByTestId("segmentation-picker-sector"),
    "Aerospace",
  );

  // One query string, two schemes, and neither may clear the other: a grain is a
  // section control under the route's own bare name and a filter is
  // `filter[…]`. A hook that rebuilt the URL from its own state rather than
  // editing the current one would drop whichever half it did not own.
  await waitFor(() => {
    const search = screen.getByTestId("search").textContent ?? "";
    expect(search).toContain("grain=quarter");
    expect(search).toContain("filter%5Bsector%5D=Aerospace");
  });
});

test("choosing Any clears the dimension rather than sending a blank", async () => {
  renderBar(
    { segmentationValues: SEGMENTATION_VALUES },
    "/dashboard/fraud?filter%5Bsector%5D=Aerospace",
  );

  await userEvent.selectOptions(await screen.findByTestId("segmentation-picker-sector"), "");

  // `filter[sector]=` is a dimension nobody chose: it would draw a chip with no
  // label and send a parameter the server would refuse, so the absence of a
  // narrowing is the absence of a parameter.
  await waitFor(() =>
    expect(screen.getByTestId("search")).not.toHaveTextContent("filter%5Bsector%5D"),
  );
});

test("the chips are the server's reading of the URL, in its order", async () => {
  renderBar(
    { segmentationValues: SEGMENTATION_VALUES_FILTERED },
    "/dashboard/fraud?filter%5Bsector%5D=Aerospace&filter%5BseverityBand%5D=high",
  );

  // Severity before sector although the URL names sector first: the chip row is
  // the vocabulary's order, which is `DrillFilters`' field order, which is the
  // order the drill list draws the same two chips in.
  await waitFor(() => expect(chipTexts()).toEqual(["Severity: High✕", "Sector: Aerospace✕"]));
});

test("one chip's ✕ clears one dimension and leaves the other applied", async () => {
  renderBar(
    { segmentationValues: SEGMENTATION_VALUES_FILTERED },
    "/dashboard/fraud?filter%5Bsector%5D=Aerospace&filter%5BseverityBand%5D=high",
  );

  await screen.findByTestId("drill-chips");
  await userEvent.click(screen.getAllByTestId("drill-chip-remove")[0]);

  await waitFor(() => {
    const search = screen.getByTestId("search").textContent ?? "";
    expect(search).not.toContain("filter%5BseverityBand%5D");
    expect(search).toContain("filter%5Bsector%5D=Aerospace");
  });
});

test("clear all drops every dimension and keeps the section's control", async () => {
  renderBar(
    { segmentationValues: SEGMENTATION_VALUES_FILTERED },
    "/dashboard/trends?cohort=sector&filter%5Bsector%5D=Aerospace&filter%5BseverityBand%5D=high",
  );

  await screen.findByTestId("drill-chips");
  await userEvent.click(screen.getByTestId("drill-clear-all"));

  // "Clear all" is about the filters. Clearing a cohort selection with them
  // would be a second thing happening on one click, and the analyst would have
  // to notice it to undo it.
  await waitFor(() => {
    const search = screen.getByTestId("search").textContent ?? "";
    expect(search).not.toContain("filter%5B");
    expect(search).toContain("cohort=sector");
  });
});

test("a filter change re-keys the request rather than re-filtering a cached answer", async () => {
  const seen: string[] = [];
  renderBar({
    segmentationValues: (url) => {
      seen.push(url);
      return SEGMENTATION_VALUES;
    },
  });

  await screen.findByTestId("segmentation-picker-sector");
  await userEvent.selectOptions(screen.getByTestId("segmentation-picker-sector"), "Aerospace");

  // The whole of AD-1 on this surface, as an observable property: a picker
  // change is a *request*, because the filter is in the query key. A bar that
  // narrowed its own cached options would render new pickers and never touch the
  // network.
  // The *fetch* URL, where the brackets are raw — the router percent-encodes
  // them in `location.search` and `openapi-fetch` does not, and asserting the
  // wrong one of the two would pass on a bar that never sent the parameter at
  // all.
  await waitFor(() =>
    expect(seen.some((url) => url.includes("filter[sector]=Aerospace"))).toBe(true),
  );
  expect(seen[0]).not.toContain("filter[sector]");
});

test("an impossible combination says so and leaves the chips clearable", async () => {
  renderBar(
    { segmentationValues: SEGMENTATION_VALUES_EMPTY },
    "/dashboard/fraud?filter%5Bstate%5D=MI&filter%5Bsector%5D=Aerospace",
  );

  // Decided on the **total**, never on the number of options or chips: a
  // zero-filled vocabulary produces rows for nothing, so counting what is on
  // screen would call a full page empty and an empty one full.
  expect(await screen.findByTestId("segmentation-empty")).toHaveTextContent(
    "No claims match these filters",
  );
  expect(screen.getByTestId("segmentation-count")).toHaveTextContent("0 of 100 claims match");
  // …and the way out is still on screen. An analyst stranded on an empty
  // workspace with no clearable chip has only the address bar.
  expect(chipTexts()).toEqual(["State: MI✕", "Sector: Aerospace✕"]);
});

test("a refused value clears its own dimension, names it, and keeps the rest", async () => {
  renderBar(
    {
      segmentationValues: (url) =>
        // The fetch URL's own spelling: `openapi-fetch` leaves the brackets raw
        // where the router percent-encodes them.
        url.includes("filter[gender]")
          ? SEGMENTATION_VALUES_REFUSED
          : SEGMENTATION_VALUES_FILTERED,
    },
    "/dashboard/fraud?filter%5Bgender%5D=nonsense&filter%5Bsector%5D=Aerospace",
  );

  // AC 5, in three parts. The refused dimension goes…
  await waitFor(() =>
    expect(screen.getByTestId("search")).not.toHaveTextContent("filter%5Bgender%5D"),
  );
  // …the rest of the filter stays applied…
  expect(screen.getByTestId("search")).toHaveTextContent("filter%5Bsector%5D=Aerospace");
  // …and the page renders, with an inline message naming the dimension by the
  // word the analyst chose it under rather than by the parameter it was sent as.
  expect(await screen.findByTestId("segmentation-refused")).toHaveTextContent("Gender");
});

test("the options stay the whole book's while a filter is applied", async () => {
  renderBar(
    { segmentationValues: SEGMENTATION_VALUES_FILTERED },
    "/dashboard/fraud?filter%5Bsector%5D=Aerospace&filter%5BseverityBand%5D=high",
  );

  const sector = await screen.findByTestId("segmentation-picker-sector");

  // The asymmetry the endpoint exists for: a picker whose options had been cut
  // by the active filter could not be used to *widen* one, and every narrowing
  // would be a one-way door. Six claims match and three sectors are still
  // offered.
  expect(
    within(sector)
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["Any", "Aerospace", "Automotive", "Heavy Equipment"]);
  expect(screen.getByTestId("segmentation-count")).toHaveTextContent("6 of 100 claims match");
});

test("a failure with no filter applied says so, and says the figures are the book", async () => {
  // A **404**, not a 500, for the reason `api-mock.ts` records: the query client
  // retries a 5xx twice with backoff, so a 500 fixture leaves the test waiting
  // on timers rather than asserting an error state.
  renderBar({ segmentationValues: { status: 404, body: { detail: "boom" } } });

  // Inline, never a dialog (NFR-3), and it says what the reader can conclude
  // from the sections underneath rather than only that something broke.
  const alert = await screen.findByTestId("segmentation-error");
  expect(alert).toHaveAttribute("role", "alert");
  expect(alert).toHaveTextContent("The segmentation options could not be loaded");
  expect(alert).toHaveTextContent("No filter is applied");
  // Nothing to clear, so nothing to draw: an empty chip row would be a control
  // with no purpose above four unfiltered sections.
  expect(screen.queryByTestId("drill-chips")).toBeNull();
});

test("a failure with a filter applied keeps the chips and never claims to be unfiltered", async () => {
  renderBar(
    { segmentationValues: { status: 404, body: { detail: "boom" } } },
    "/dashboard/fraud?filter%5Bsector%5D=Aerospace&filter%5BseverityBand%5D=high",
  );

  // The configuration the first version of this branch could not survive. It
  // returned the alert *instead of* the fragment holding the pickers, the chips,
  // the counts and the zero-result line — and `FraudPage`/`TrendsPage` read the
  // filter straight from the URL through their own `useSegmentation()`, so all
  // four aggregates stayed folded over the intersection while the only control
  // that could widen them was gone. The way out was the address bar.
  const alert = await screen.findByTestId("segmentation-error");
  expect(alert).toHaveTextContent("The filter below is still applied");
  // …and it must not say the opposite. The old copy asserted "The figures below
  // are unfiltered", which is false exactly here — the one case where it matters
  // — and would have an analyst read a narrowed subset as the whole portfolio.
  expect(alert).not.toHaveTextContent("unfiltered");

  // The chips are drawn from the **URL**, because there is no echo to draw them
  // from, and they are still clearable — chip order is the vocabulary's, not the
  // URL's.
  expect(chipTexts()).toEqual(["Severity: High✕", "Sector: Aerospace✕"]);
  await userEvent.click(screen.getByTestId("drill-clear-all"));
  await waitFor(() =>
    expect(screen.getByTestId("search")).not.toHaveTextContent("filter%5B"),
  );
});

test("a picker never reads Any for a dimension the chips say is applied", async () => {
  renderBar(
    { segmentationValues: SEGMENTATION_VALUES_OUT_OF_BOOK },
    "/dashboard/fraud?filter%5Bsector%5D=Nonexistent",
  );

  // `useSegmentation`'s ruling — a `<select>` must never carry a value none of
  // its options has — applied to the ten dimensions, where it was missing.
  // React renders such a select as *no selection at all*, so the chip row said
  // the dimension was applied, every figure read zero, and the picker said no
  // filter was set: three controls on one screen and the false one is the one an
  // analyst would act on.
  const sector = await screen.findByTestId("segmentation-picker-sector");
  expect(sector).toHaveValue("Nonexistent");
  // Surfaced as a transient option and marked, so it does not read as a value
  // the book contains — which is the one thing it is known not to be.
  expect(
    within(sector)
      .getAllByRole("option")
      .map((option) => option.textContent),
  ).toEqual(["Any", "Nonexistent (no claims)", "Aerospace", "Automotive", "Heavy Equipment"]);

  // …and the picker is still the way out: choosing Any drops the parameter, and
  // the transient option goes with it.
  await userEvent.selectOptions(sector, "");
  await waitFor(() =>
    expect(screen.getByTestId("search")).not.toHaveTextContent("filter%5Bsector%5D"),
  );
});

test("an empty book is not blamed on a filter nobody set", async () => {
  renderBar({
    segmentationValues: {
      status: 200,
      body: { ...SEGMENTATION_VALUES_EMPTY.body, appliedFilters: [], claimsInScope: 0 },
    },
  });

  // A scoped analyst assigned nothing reads the same `0` as an analyst who
  // narrowed to nothing, and only the chip row can tell them apart. "Clear one
  // to widen the view" sends the first of them looking for a filter she never
  // set, on the emptiest screen in the console.
  expect(await screen.findByTestId("segmentation-empty")).toHaveTextContent(
    "No claims in this portfolio.",
  );
  expect(screen.getByTestId("segmentation-empty")).not.toHaveTextContent("Clear one");
});
