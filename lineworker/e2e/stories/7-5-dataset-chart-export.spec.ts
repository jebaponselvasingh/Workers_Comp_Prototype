import { readFileSync } from "node:fs";

import type { Download, Page } from "@playwright/test";

import { PERSONAS, loginAs } from "../fixtures/login";
import { psqlQuery } from "../fixtures/reset";
import {
  EXPECTED_EXPORT_HEADER_PREFIX,
  EXPECTED_FRAUD_BAND_EXPORT_ROWS,
  expectedExportClaimIdsFor,
  expectedExportRowsFor,
  workspaceUrl,
  type Segmentation,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 7.5 — Dataset & Chart Export.
 *
 * Nothing here stubs anything. The browser logs in for real, clicks a real
 * button, receives a real streamed response through nginx, and Playwright saves
 * a real file to disk — which is the only place in this build where the whole
 * path is exercised end to end. The vitest suite proves the control asks the
 * right question; this proves the answer arrives as a file.
 *
 * **Three things can only be checked here.**
 *
 * 1. **That a download happens at all.** `Content-Disposition: attachment` is
 *    the difference between a file in a folder and a screenful of commas in a
 *    tab, and no unit test in either language can see it: jsdom has no
 *    downloads and the server tests read a response body rather than a browser's
 *    behaviour.
 * 2. **That the bytes parse.** A CSV is asserted by reading the saved file and
 *    counting its data rows against an oracle that folds the seed
 *    independently; an XLSX is asserted by its suggested filename and its ZIP
 *    magic bytes, because `e2e/package.json` has **no spreadsheet parser and is
 *    not getting one** — a dependency added to prove that a file a library wrote
 *    can be read by the same library's reader proves nothing, and the cell-level
 *    comparison already happens in `test_dataset_export.py` against `zipfile`
 *    and `xml.etree`.
 * 3. **That the audit row is real and durable.** Read with `psqlQuery`, which is
 *    the only way a spec can see `audit_event` — nothing in the product reads it
 *    (Story 2.3's ruling, and inventing an endpoint so a spec could would be
 *    building a product surface for a test).
 *
 * **One `@smoke`**, and it is the whole of AC 1, AC 2 and AC 3 in one gesture:
 * log in as the analyst, apply a segmentation filter, export the filtered claim
 * list as CSV, parse the download, check its data-row count against the total
 * the page is showing *and* against the seed folded independently, then check
 * that exactly one `export.csv` audit row exists naming that count.
 */

const ANALYST = PERSONAS.analyst;
const SUPERVISOR = PERSONAS.fullPortfolioSupervisor;

const FRAUD = "/dashboard/fraud";
const DRILL = "/dashboard/claims";

/**
 * One dimension the seeded book genuinely narrows on, **chosen for its size**.
 *
 * A single region rather than the two-dimension pair the other Epic 7 specs use,
 * and the difference is about what this spec is for: those narrow to prove a
 * derived band and a stored column intersect, and this one needs a filter whose
 * surviving population is *larger than one page*. The drill list pages at
 * **fifty** — `priority_weights.pageLimit`, which `e2e/fixtures/seed.ts` restates
 * as `DRILL_PAGE_LIMIT` — so a filter leaving fifty or fewer would make the
 * file's row count and the page's row count coincide, and "every page of them,
 * not the pages that happen to be loaded" would be satisfied by an export that
 * returned only what the browser had. `Midwest` leaves fifty-seven of the
 * hundred seeded claims, so the margin is **seven rows across a second page**;
 * `Aerospace`, the pair the other specs use, leaves twelve and would not clear
 * the page at all. Seven is a thin margin and it is the honest one — the seed
 * has no larger single facet — so the assertion below compares the drawn card
 * count against the file's own row count rather than trusting this paragraph,
 * and it is that assertion rather than this comment that fails if a retuned
 * `pageLimit` swallows the margin.
 */
const FILTER: Segmentation = { region: "Midwest" };

/** The drill list has answered — its server-published total is on screen. */
async function drillSettled(page: Page): Promise<void> {
  await expect(byTestId(page, "drill-count")).toBeVisible();
}

/** The fraud panel has answered — the band donut's legend is drawn. */
async function fraudSettled(page: Page): Promise<void> {
  await expect(byTestId(page, "fraud-band-distribution-legend-row").first()).toBeVisible();
}

/** Click one export button and wait for the browser to accept the file. */
async function downloadFrom(page: Page, testId: string): Promise<Download> {
  const waiting = page.waitForEvent("download");
  await byTestId(page, testId).click();
  return waiting;
}

/** A saved download's bytes. */
async function bytesOf(download: Download): Promise<Buffer> {
  const saved = await download.path();
  return readFileSync(saved);
}

/**
 * A saved CSV's lines, blank tail dropped.
 *
 * Split on `\r\n` because that is what RFC 4180 prescribes and what the server
 * writes — asserted implicitly here rather than in its own test, since a file
 * split on the wrong terminator produces one enormous line and every count below
 * fails loudly.
 */
async function csvLines(download: Download): Promise<string[]> {
  const text = (await bytesOf(download)).toString("utf8");
  return text.split("\r\n").filter((line) => line !== "");
}

/**
 * Every `export.*` audit row so far, as `action|entity|rows`.
 *
 * The whole table, oldest first, because the fixture resets the database once
 * per **spec file** rather than per test — so each test below takes a count
 * first and asserts the rows it added, which is the only reading that is a
 * statement about one export rather than about whichever tests ran before it.
 */
function exportAuditRows(): string[] {
  return psqlQuery(
    "SELECT action || '|' || entity || '|' || (after->>'rows') " +
      "FROM audit_event WHERE action LIKE 'export.%' ORDER BY id",
  );
}

/** The rows written since a recorded count — see `exportAuditRows`. */
function exportAuditRowsSince(before: number): string[] {
  return exportAuditRows().slice(before);
}

test.describe("@story:7-5 @epic:7 dataset & chart export", () => {
  test("@smoke an analyst exports a filtered claim list and it is audited", async ({
    page,
  }) => {
    const auditedBefore = exportAuditRows().length;
    await loginAs(page, ANALYST);
    await page.goto(workspaceUrl(DRILL, FILTER));
    await drillSettled(page);

    // The page's own figure first, so the file has something on screen to be
    // compared against. It is the **server's** total for the whole filtered
    // ranking rather than the rows drawn, which is precisely the number an
    // export has to match and precisely the number a browser-assembled file
    // would not.
    const expectedRows = expectedExportRowsFor(ANALYST, FILTER);
    await expect(byTestId(page, "drill-count")).toHaveText(`${String(expectedRows)} in view`);
    // …and the list is longer than one page, so "every page of them" is a claim
    // with something behind it rather than a coincidence of a short filter.
    const drawn = await byTestId(page, "queue-card").count();
    expect(drawn).toBeLessThan(expectedRows);

    const download = await downloadFrom(page, "drill-claims-export-csv");

    // AC 1: a file arrives, named by the server, and it is a table.
    expect(download.suggestedFilename()).toMatch(/^lineworker-claims-\d{4}-\d{2}-\d{2}\.csv$/);
    const lines = await csvLines(download);
    expect(lines[0]).toContain(EXPECTED_EXPORT_HEADER_PREFIX);

    // AC 2: **every page of the list, not the page on screen** — checked against
    // the seed folded independently, and as a set rather than only a count,
    // because two populations of the same size are what a count cannot see.
    expect(lines.length - 1).toBe(expectedRows);
    const exportedIds = new Set(lines.slice(1).map((line) => line.split(",")[0]));
    expect(exportedIds).toEqual(expectedExportClaimIdsFor(ANALYST, FILTER));

    // AC 3: exactly one audit row, naming the target and the **real** count.
    // Read straight from the database because nothing in the product exposes
    // `audit_event`, and durable by the time the download completed — which is
    // the ordering the service exists to guarantee (the row commits before the
    // first byte leaves).
    expect(exportAuditRowsSince(auditedBefore)).toEqual([
      `export.csv|claims|${String(expectedRows)}`,
    ]);

    // …and the completion is announced without blocking anything (NFR-3).
    await expect(byTestId(page, "toast-message")).toHaveText("Exported this claim list.");
  });

  test("a spreadsheet arrives as a real workbook (AC 1)", async ({ page }) => {
    const auditedBefore = exportAuditRows().length;
    await loginAs(page, ANALYST);
    await page.goto(workspaceUrl(DRILL, FILTER));
    await drillSettled(page);

    const download = await downloadFrom(page, "drill-claims-export-xlsx");

    expect(download.suggestedFilename()).toMatch(/^lineworker-claims-\d{4}-\d{2}-\d{2}\.xlsx$/);
    // **ZIP magic bytes, and no parser.** An XLSX is a zip container, so `PK` is
    // the cheapest honest evidence that these bytes are a workbook rather than a
    // CSV served under a spreadsheet media type — which is exactly the failure a
    // content-type assertion alone would miss. The cells are compared against the
    // CSV's, one for one, in `test_dataset_export.py`, which reads the container
    // with `zipfile` rather than with the library that wrote it.
    const bytes = await bytesOf(download);
    expect(bytes.subarray(0, 2).toString("latin1")).toBe("PK");
    expect(bytes.byteLength).toBeGreaterThan(1000);

    expect(exportAuditRowsSince(auditedBefore)).toEqual([
      `export.xlsx|claims|${String(expectedExportRowsFor(ANALYST, FILTER))}`,
    ]);
  });

  test("a chart's aggregate exports its own rows, not the claims behind it", async ({
    page,
  }) => {
    const auditedBefore = exportAuditRows().length;
    await loginAs(page, ANALYST);
    await page.goto(workspaceUrl(FRAUD, FILTER));
    await fraudSettled(page);

    const download = await downloadFrom(page, "fraud-band-distribution-export-csv");
    const lines = await csvLines(download);

    expect(download.suggestedFilename()).toMatch(/^lineworker-fraud-bands-/);
    expect(lines[0]).toBe("fraud_band,claims");
    // **Three rows whatever the filter leaves**, because a band is a rule's
    // answer rather than a column's value — the zero-fill, in a file, where a
    // missing row cannot be told from a band nobody scored into.
    expect(lines.length - 1).toBe(EXPECTED_FRAUD_BAND_EXPORT_ROWS);
    // …and the counts sum to the segment, which is what makes this the chart's
    // own aggregate rather than a list of claims under a different heading.
    const counted = lines
      .slice(1)
      .reduce((total, line) => total + Number(line.split(",")[1]), 0);
    expect(counted).toBe(expectedExportRowsFor(ANALYST, FILTER));

    expect(exportAuditRowsSince(auditedBefore)).toEqual([
      `export.csv|fraud_bands|${String(EXPECTED_FRAUD_BAND_EXPORT_ROWS)}`,
    ]);
  });

  test("only the clicked control reports busy (AC 6)", async ({ page }) => {
    await loginAs(page, ANALYST);
    await page.goto(workspaceUrl(DRILL, FILTER));
    await drillSettled(page);

    // The response is held open until the assertions below have run, so the
    // in-flight state is observable at all. Routed rather than throttled because
    // a hundred-claim export against this stack completes in milliseconds, and a
    // race is not a state.
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/dashboard/claims/export**", async (route) => {
      await held;
      await route.continue();
    });

    await byTestId(page, "drill-claims-export-csv").click();

    await expect(byTestId(page, "drill-claims-export-csv")).toBeDisabled();
    // **The assertion this test exists for.** One mutation drives both buttons,
    // so a control keyed on a shared pending flag would disable this one too —
    // and the rest of the workspace has to stay usable besides, which the back
    // link standing here is the cheapest evidence of.
    await expect(byTestId(page, "drill-claims-export-xlsx")).toBeEnabled();
    await expect(byTestId(page, "drill-back")).toBeEnabled();

    release();
    await expect(byTestId(page, "toast-message")).toBeVisible();
  });

  test("a supervisor cannot reach an export route (AC 5)", async ({ page }) => {
    const auditedBefore = exportAuditRows().length;
    await loginAs(page, SUPERVISOR);

    // Requested through the browser's own session rather than through a
    // component, because there is no control on a supervisor's screen to click:
    // the workspace is the analyst's, and what this checks is that the *route*
    // refuses rather than that the UI hides it.
    const refused = await page.request.get(`/api${DRILL}/export?format=csv`);

    expect(refused.status()).toBe(403);
    expect((await refused.json()).type).toBe("/problems/fraud-analytics-not-permitted");
    expect(refused.headers()["cache-control"]).toBe("no-store");
    // …and nothing was recorded, because nothing left. An egress log naming an
    // export that never happened would make the one question it exists to answer
    // unanswerable from itself (AC 4).
    expect(exportAuditRowsSince(auditedBefore)).toEqual([]);
  });
});
