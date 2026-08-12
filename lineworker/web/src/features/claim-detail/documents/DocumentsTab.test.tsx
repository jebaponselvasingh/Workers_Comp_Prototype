import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  CLAIM_DETAIL_TREATMENT,
  DOCUMENTS_BLOCK,
  DOCUMENTS_BLOCK_EMPTY,
  DOCUMENTS_BLOCK_PATH_A,
  DOCUMENT_SHEET_FROI,
  DOCUMENT_SHEET_SUMMARY,
  ME_HANDLER,
  type StubRoute,
  type StubRouteFor,
  stubApi,
} from "@/test/api-mock";

import { ClaimDetailPane } from "../ClaimDetailPane";

/**
 * Story 2.5 — the Documents & ID tab, rendered from a payload.
 *
 * Every assertion is "the tab shows what the server sent". Nothing here is
 * computed from another field: the path is *not* derived from the severity
 * beside it, the form set is *not* looked up from a table in the browser, and
 * the sheet's rows are *not* composed from the claim the dialog is rendered
 * over. A component that did any of those would pass no test in this file —
 * which is the behavioural half of the criterion `noDerivation.test.ts`
 * enforces structurally.
 *
 * Rendered through `ClaimDetailPane` rather than by mounting `DocumentsTab`
 * directly, because the tab is reached by clicking a tab, and `DetailTabs`
 * mounting it only while selected is part of what this story changed.
 */

function renderTab(
  documents: unknown = DOCUMENTS_BLOCK,
  documentSheet: StubRouteFor = DOCUMENT_SHEET_FROI,
) {
  stubApi({
    me: ME_HANDLER,
    claimDetail: {
      status: 200,
      body: { ...CLAIM_DETAIL_TREATMENT.body, documents },
    },
    documentSheet,
  });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <ClaimDetailPane />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openTab(documents: unknown = DOCUMENTS_BLOCK, sheet?: StubRouteFor) {
  renderTab(documents, sheet);
  await userEvent.click(await screen.findByTestId("tab-documents"));
  return screen.getByTestId("documents-tab");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- AC 1: the required-forms card --------------------------------------

test("the forms card renders the path's banner and one row per form", async () => {
  await openTab();

  const card = screen.getByTestId("required-forms");
  expect(card).toHaveAttribute("data-path", "b");
  expect(screen.getByTestId("required-forms-banner")).toHaveTextContent(
    "Path B — Follow-Up Treatment",
  );

  const rows = within(card).getAllByTestId("required-form");
  expect(rows.map((row) => row.dataset.formCode)).toEqual([
    "C-3",
    "RFA-1W",
    "C-4.3",
    "C-11",
  ]);
  expect(rows[0]).toHaveTextContent("FROI — Employee Claim for Compensation (C-3)");
  expect(rows[0]).toHaveTextContent("carrier within 30 days");
});

test("a different path renders a different banner and a different form set", async () => {
  // The prototype's bug, as the assertion that would have caught it: its
  // `c.path || "B"` shows all 100 claims the same four filings, and a
  // single-payload test passes against exactly that.
  await openTab(DOCUMENTS_BLOCK_PATH_A);

  expect(screen.getByTestId("required-forms")).toHaveAttribute("data-path", "a");
  expect(screen.getByTestId("required-forms-banner")).toHaveTextContent(
    "Path A — Minor Injury",
  );
  expect(
    screen.getAllByTestId("required-form").map((row) => row.dataset.formCode),
  ).toEqual(["C-2F", "FAR-1"]);
});

test("the banner follows the payload's path, not the claim's severity", async () => {
  // The claim in this fixture is severity 78 / high risk. A component that
  // banded the path itself — from the score, from the risk, from anything —
  // would not answer "a" here.
  await openTab(DOCUMENTS_BLOCK_PATH_A);

  expect(screen.getByTestId("case-header-injury")).toHaveTextContent("High severity");
  expect(screen.getByTestId("required-forms")).toHaveAttribute("data-path", "a");
});

test("each form's download link opens the regulator's blank in a new tab", async () => {
  await openTab();

  const links = screen.getAllByTestId("required-form-download");
  expect(links).toHaveLength(4);
  expect(links[0]).toHaveAttribute("href", DOCUMENTS_BLOCK.requiredForms[0].downloadUrl);
  expect(links[0]).toHaveAttribute("target", "_blank");
  // Without `noopener` the opened page gets a handle on this one.
  expect(links[0].getAttribute("rel")).toContain("noopener");
});

// --- AC 3: the ID card and the list -------------------------------------

test("the ID card shows the worker, their identifiers and the plant footer", async () => {
  await openTab();

  const card = screen.getByTestId("employee-id-card");
  expect(screen.getByTestId("employee-id-name")).toHaveTextContent("Marcus Delgado");
  expect(card).toHaveTextContent("Assembly Technician");
  expect(card).toHaveTextContent(DOCUMENTS_BLOCK.idCard.employeeBusinessId);
  expect(card).toHaveTextContent(DOCUMENTS_BLOCK.idCard.policyNum);
  expect(card).toHaveTextContent("Kaya Johnson");
  expect(screen.getByTestId("employee-id-plant")).toHaveTextContent("Peoria, IL");
  expect(card).toHaveTextContent("IL · Midwest");
  expect(screen.getByTestId("employee-id-initials")).toHaveTextContent("MD");
});

test("the documents list shows a chip, a name and a filing date per row", async () => {
  await openTab();

  const rows = screen.getAllByTestId("document-row");
  expect(rows).toHaveLength(3);
  expect(screen.getByTestId("document-count")).toHaveTextContent("(3 files)");

  expect(rows[0]).toHaveTextContent("C-1");
  expect(rows[0]).toHaveTextContent("C-1 First Report of Injury");
  expect(rows[0]).toHaveTextContent("Filed 2026-03-24");
  expect(rows[1]).toHaveTextContent("MED");
  // The prototype writes a timing note where a date belongs on 101 documents;
  // the column is null and the em dash is the honest rendering.
  expect(rows[2]).toHaveTextContent("Filed —");
});

test("every document row is reachable by keyboard", async () => {
  // The prototype delegates clicks from a `div`, which makes its viewer
  // unopenable without a mouse and invisible to a screen reader.
  await openTab();

  const rows = screen.getAllByTestId("document-row");
  rows[0].focus();
  await userEvent.keyboard("{Enter}");

  expect(await screen.findByTestId("document-viewer")).toBeInTheDocument();
});

// --- AC 5: the empty state ----------------------------------------------

test("a claim with no documents says so instead of rendering an empty list", async () => {
  await openTab(DOCUMENTS_BLOCK_EMPTY);

  expect(screen.getByTestId("document-list-empty")).toHaveTextContent("No documents on file");
  expect(screen.queryAllByTestId("document-row")).toHaveLength(0);
  expect(screen.getByTestId("document-count")).toHaveTextContent("(0 files)");
});

test("an empty file does not empty the required-forms card", async () => {
  // Which filings a path requires does not depend on what has been filed. A
  // state that blanked both would tell a handler their statutory obligations
  // had gone away because nobody had met them yet.
  await openTab(DOCUMENTS_BLOCK_EMPTY);

  expect(screen.getAllByTestId("required-form")).toHaveLength(4);
});

// --- AC 4: the read-only viewer -----------------------------------------

test("a FROI row opens the full injury sheet the server assembled", async () => {
  await openTab();

  await userEvent.click(screen.getAllByTestId("document-row")[0]);

  const dialog = await screen.findByTestId("document-viewer");
  expect(within(dialog).getByTestId("document-sheet")).toHaveAttribute("data-variant", "froi");
  expect(screen.getByTestId("document-viewer-title")).toHaveTextContent(
    "C-1 First Report of Injury",
  );

  const labels = within(dialog)
    .getAllByTestId("document-sheet-row")
    .map((row) => row.dataset.label);
  expect(labels).toEqual(DOCUMENT_SHEET_FROI.body.rows.map((row) => row.label));
  expect(within(dialog).getByTestId("document-sheet-signatures")).toHaveTextContent(
    "Adjuster / Date",
  );
});

test("a non-FROI row opens the shorter summary sheet", async () => {
  // The whole point of two variants: a wage statement or a legal filing does
  // not carry the claimant's diagnosis.
  await openTab(DOCUMENTS_BLOCK, DOCUMENT_SHEET_SUMMARY);

  await userEvent.click(screen.getAllByTestId("document-row")[2]);

  const dialog = await screen.findByTestId("document-viewer");
  expect(within(dialog).getByTestId("document-sheet")).toHaveAttribute(
    "data-variant",
    "summary",
  );
  expect(dialog).not.toHaveTextContent("ICD-10");
  expect(dialog).toHaveTextContent("On file");
});

test("the sheet renders exactly the rows it was sent, in order", async () => {
  // AC 4's "server-assembled": the SPA does not compose FROI fields from the
  // claim object. The stub answers the same sheet for every id, so a viewer
  // that built its own rows from the row it was opened by would differ here.
  await openTab(DOCUMENTS_BLOCK, DOCUMENT_SHEET_FROI);

  await userEvent.click(screen.getAllByTestId("document-row")[1]);

  const rows = within(await screen.findByTestId("document-viewer")).getAllByTestId(
    "document-sheet-row",
  );
  expect(rows).toHaveLength(DOCUMENT_SHEET_FROI.body.rows.length);
  expect(rows[8]).toHaveTextContent("ICD-10");
  expect(rows[8]).toHaveTextContent("S39.012A");
});

test("the money row is formatted from cents by the browser", async () => {
  // The convention held end to end: the wire carries 143_200 and the one
  // formatter in the app turns it into dollars.
  await openTab();

  await userEvent.click(screen.getAllByTestId("document-row")[0]);

  const dialog = await screen.findByTestId("document-viewer");
  const aww = within(dialog)
    .getAllByTestId("document-sheet-row")
    .find((row) => row.dataset.label === "AWW");
  expect(aww).toHaveTextContent("$1,432");
  expect(dialog).not.toHaveTextContent("143200");
});

test("non-date rows are rendered verbatim, not through a date formatter", async () => {
  // Regression (code review, 2026-08-12): every non-money row used to be
  // rendered as `formatDate(row.text)`, which is currently `iso ?? "—"` — the
  // identity function. The day that helper does what its name promises, a
  // FROI sheet would print `Invalid Date` for its claim id, its employee, its
  // injury type and its severity. These four are asserted by *value* so the
  // coupling cannot come back unnoticed.
  await openTab();

  await userEvent.click(screen.getAllByTestId("document-row")[0]);
  const dialog = await screen.findByTestId("document-viewer");
  const valueOf = (label: string) =>
    within(dialog)
      .getAllByTestId("document-sheet-row")
      .find((row) => row.dataset.label === label)?.textContent;

  expect(valueOf("Claim ID")).toContain("WC-20017");
  expect(valueOf("Employee")).toContain("Marcus Delgado (EMP-CAT-2043)");
  expect(valueOf("Injury Type")).toContain("Fall from Height");
  expect(valueOf("Severity")).toContain("78");
  expect(dialog).not.toHaveTextContent("Invalid Date");
});

test("an undated row renders the em dash rather than the word null", async () => {
  await openTab(DOCUMENTS_BLOCK, DOCUMENT_SHEET_SUMMARY);

  await userEvent.click(screen.getAllByTestId("document-row")[2]);

  const filed = within(await screen.findByTestId("document-viewer"))
    .getAllByTestId("document-sheet-row")
    .find((row) => row.dataset.label === "Filed");
  expect(filed).toHaveTextContent("—");
});

test("the viewer offers nothing to edit", async () => {
  // AC 4 says read-only. Asserted as the absence of every editing affordance
  // rather than trusted to the component's shape: a viewer that grew an input
  // would be a second write path for columns the four commands own (AD-12).
  await openTab();

  await userEvent.click(screen.getAllByTestId("document-row")[0]);

  const dialog = await screen.findByTestId("document-viewer");
  expect(within(dialog).queryAllByRole("textbox")).toHaveLength(0);
  expect(within(dialog).queryAllByRole("combobox")).toHaveLength(0);
  // One button, and it is the close ✕.
  const buttons = within(dialog).getAllByRole("button");
  expect(buttons).toHaveLength(1);
  expect(buttons[0]).toHaveAccessibleName("Close");
});

test("the viewer closes on the ✕ and blocks nothing native", async () => {
  await openTab();
  await userEvent.click(screen.getAllByTestId("document-row")[0]);
  const dialog = await screen.findByTestId("document-viewer");

  // NFR-3 / UX-DR11: a modal, never a native dialog.
  expect(screen.queryAllByRole("alertdialog")).toHaveLength(0);

  await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));

  expect(screen.queryByTestId("document-viewer")).not.toBeInTheDocument();
});

test("a sheet that fails to load is reported inline, not as an empty document", async () => {
  await openTab(DOCUMENTS_BLOCK, {
    status: 404,
    body: {
      type: "/problems/claim-not-found",
      title: "Not Found",
      status: 404,
      detail: "no",
    },
  });

  await userEvent.click(screen.getAllByTestId("document-row")[0]);

  const alert = await screen.findByTestId("document-viewer-error");
  expect(alert).toHaveAttribute("role", "alert");
  expect(screen.queryByTestId("document-sheet")).not.toBeInTheDocument();

  // …and the subtitle agrees with it (code review, 2026-08-12). The header
  // renders outside the pending/error branch, so it used to read "Loading…"
  // above the failure message — telling a handler to wait for a request that
  // had already finished failing.
  const subtitle = screen.getByTestId("document-viewer-subtitle");
  expect(subtitle).toHaveTextContent("Could not be loaded");
  expect(subtitle).not.toHaveTextContent("Loading");
});

test("nothing is requested until a row is opened", async () => {
  // `enabled` on the query. A tab that fetched every sheet on mount would
  // issue eight requests to serve a modal most handlers never open.
  await openTab();
  // `openapi-fetch` hands `fetch` a `Request` object as its only argument, so
  // the URL is `input.url` and never the argument stringified — Story 2.4's
  // `InjuryWrites.test.tsx` records the same trap.
  const calls = (globalThis.fetch as unknown as { mock: { calls: [Request][] } }).mock.calls;
  const sheetCalls = () => calls.filter(([input]) => input.url.includes("/documents/")).length;
  expect(sheetCalls()).toBe(0);

  await userEvent.click(screen.getAllByTestId("document-row")[0]);
  await screen.findByTestId("document-sheet");

  expect(sheetCalls()).toBe(1);
});

test("the tab is not mounted behind an unselected tab", async () => {
  // The reason it is a prop on `DetailTabs` rather than a permanently
  // rendered panel: it owns which document the viewer has open.
  renderTab();
  await screen.findByTestId("case-header");

  expect(screen.queryByTestId("documents-tab")).not.toBeInTheDocument();
});

test("a case file still loading draws no tab content", async () => {
  renderTab();
  stubApi({ me: ME_HANDLER, claimDetail: "pending" as StubRoute });

  expect(screen.queryByTestId("documents-tab")).not.toBeInTheDocument();
});
