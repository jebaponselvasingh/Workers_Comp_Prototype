import { readFileSync } from "node:fs";
import path from "node:path";

import userEvent from "@testing-library/user-event";

import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { ToastHost, ToastProvider } from "@/components/ui/toast";
import { EXPORT_FILE, EXPORT_TOO_LARGE, stubApi, type StubRoute } from "@/test/api-mock";

import { ExportControl } from "./ExportControl";

/**
 * Story 7.5 AC 1/4/6 — the control asks the server and reports on itself.
 *
 * Everything this component can get wrong is observable from the outside, so
 * nothing here reads internals:
 *
 * - **What was asked** is the stubbed request's URL. That is the assertion the
 *   whole story rests on — the browser sends a filter spec and no rows (AD-1,
 *   AD-7) — and a query string is the only place the difference between "the
 *   server produced this" and "the cache did" is visible.
 * - **Which control is busy** is `disabled` and the label, per button. AC 6 says
 *   only the clicked control reports busy, which is a claim about the *other*
 *   button as much as about the clicked one, so both are asserted every time.
 * - **What a reader is told** is a toast for a success and a `role="alert"` for
 *   a refusal — `toast.tsx:28-34`'s split, and the two are asserted to be the
 *   right way round rather than merely present.
 *
 * The requests are recorded by reading `fetch.mock.calls` rather than by a
 * bespoke spy, because `stubApi` already replaces the global and a second layer
 * would be a second thing to keep true.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

/** One never-settling route, so the in-flight state is observable at all. */
const PENDING = "pending" as const;

function renderControl(params: Record<string, string> = {}) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ToastProvider>
        <ExportControl
          testId="fraud-band-distribution"
          label="the fraud score distribution"
          path="/dashboard/fraud/export"
          params={{ ...params, table: "bands" }}
        />
        <ToastHost />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

/** Every URL the stubbed `fetch` was called with, in order. */
function requestedUrls(): string[] {
  const fetchMock = globalThis.fetch as unknown as { mock: { calls: unknown[][] } };
  return fetchMock.mock.calls.map((call) => {
    const input = call[0];
    if (typeof input === "string") return input;
    if (input instanceof URL) return input.href;
    return (input as Request).url;
  });
}

test("idle renders two formats and asks for nothing", () => {
  stubApi({});
  renderControl();

  const csv = screen.getByTestId("fraud-band-distribution-export-csv");
  const xlsx = screen.getByTestId("fraud-band-distribution-export-xlsx");

  expect(csv).toHaveTextContent("CSV");
  expect(xlsx).toHaveTextContent("XLSX");
  expect(csv).toBeEnabled();
  expect(xlsx).toBeEnabled();
  // Named as a group, so a screen reader says which surface these two belong to
  // — several of these are on screen at once and "CSV" alone says nothing.
  expect(
    screen.getByRole("group", { name: "Export the fraud score distribution" }),
  ).toBeInTheDocument();
  expect(requestedUrls()).toHaveLength(0);
});

test("the request carries the surface's filter and the chosen format", async () => {
  stubApi({ exports: EXPORT_FILE });
  renderControl({ "filter[sector]": "Aerospace", "filter[severityBand]": "high" });

  await userEvent.click(screen.getByTestId("fraud-band-distribution-export-csv"));

  await waitFor(() => {
    expect(requestedUrls()).toHaveLength(1);
  });
  const asked = requestedUrls()[0];
  // The **whole** query string, because what makes this a server-side export is
  // that every narrowing on screen reached the request: a control that sent the
  // route and the format alone would download the unfiltered book under a card
  // reading "Aerospace".
  expect(asked).toContain("/api/dashboard/fraud/export");
  expect(asked).toContain("filter[sector]=Aerospace");
  expect(asked).toContain("filter[severityBand]=high");
  expect(asked).toContain("table=bands");
  expect(asked).toContain("format=csv");
});

test("XLSX asks for XLSX", async () => {
  stubApi({ exports: EXPORT_FILE });
  renderControl();

  await userEvent.click(screen.getByTestId("fraud-band-distribution-export-xlsx"));

  await waitFor(() => {
    expect(requestedUrls()[0]).toContain("format=xlsx");
  });
});

test("only the clicked format reports busy; the other stays usable", async () => {
  stubApi({ exports: PENDING });
  renderControl();

  await userEvent.click(screen.getByTestId("fraud-band-distribution-export-csv"));

  const csv = screen.getByTestId("fraud-band-distribution-export-csv");
  const xlsx = screen.getByTestId("fraud-band-distribution-export-xlsx");
  await waitFor(() => {
    expect(csv).toBeDisabled();
  });
  // The label swaps as well as the disabled flag: a disabled button with
  // unchanged text reads as broken rather than as busy.
  expect(csv).toHaveTextContent("CSV…");
  expect(csv).toHaveAttribute("aria-busy", "true");
  // **The assertion AC 6 is actually about.** One mutation drives both buttons,
  // so a control keyed on `isPending` would disable this one too — and the
  // workspace would report busy for a request about a different format.
  expect(xlsx).toBeEnabled();
  expect(xlsx).toHaveTextContent("XLSX");
  expect(xlsx).toHaveAttribute("aria-busy", "false");
});

test("a completed export toasts and leaves both controls usable", async () => {
  stubApi({ exports: EXPORT_FILE });
  renderControl();

  await userEvent.click(screen.getByTestId("fraud-band-distribution-export-csv"));

  await waitFor(() => {
    expect(screen.getByTestId("toast-message")).toHaveTextContent(
      "Exported the fraud score distribution.",
    );
  });
  // The subject is in the message rather than a bare "Exported", because four of
  // these can complete inside one `TOAST_DURATION_MS` and a stack of identical
  // sentences says nothing about which four.
  expect(screen.getByTestId("toast")).toHaveAttribute("data-tone", "ok");
  expect(screen.getByTestId("fraud-band-distribution-export-csv")).toBeEnabled();
  expect(screen.getByTestId("fraud-band-distribution-export-xlsx")).toBeEnabled();
  // A success has nothing to say at the control — the file is already saved.
  expect(screen.queryByTestId("fraud-band-distribution-export-error")).toBeNull();
});

test("a refusal stays inline at the control and quotes the server", async () => {
  stubApi({ exports: EXPORT_TOO_LARGE });
  renderControl();

  await userEvent.click(screen.getByTestId("fraud-band-distribution-export-csv"));

  const alert = await screen.findByTestId("fraud-band-distribution-export-error");
  // The server's own sentence, cap and count included: those two numbers are the
  // whole of what a reader needs to narrow the filter, and a house message would
  // throw them away.
  expect(alert).toHaveTextContent("caps an export at 50000");
  expect(alert).toHaveTextContent("74210 rows");
  expect(alert).toHaveRole("alert");
  // **Never a toast.** `toast.tsx` says a refusal belongs where the reader can
  // act on it: a message that removes itself after five seconds is a worse
  // answer than one that stays beside the button that produced it.
  expect(screen.queryByTestId("toast")).toBeNull();
  // …and the control is usable again, because the fix is a different request.
  expect(screen.getByTestId("fraud-band-distribution-export-csv")).toBeEnabled();
});

test("a second attempt clears the previous refusal before it is answered", async () => {
  stubApi({
    exports: (url) => (url.includes("format=csv") ? EXPORT_TOO_LARGE : PENDING),
  });
  renderControl();

  await userEvent.click(screen.getByTestId("fraud-band-distribution-export-csv"));
  await screen.findByTestId("fraud-band-distribution-export-error");

  await userEvent.click(screen.getByTestId("fraud-band-distribution-export-xlsx"));

  // The alert goes at the moment of the *attempt*, not at its success: a stale
  // refusal standing under a request that is in flight reads as a failure that
  // has just happened again.
  await waitFor(() => {
    expect(screen.queryByTestId("fraud-band-distribution-export-error")).toBeNull();
  });
});

/**
 * A route the test settles by hand, so two requests can genuinely overlap.
 *
 * `"pending"` never settles and a plain route settles at once; neither can hold
 * the first request open while the second finishes, which is exactly the state
 * the two tests below are about.
 */
function deferredRoute() {
  let settle: (route: StubRoute) => void = () => undefined;
  const promise = new Promise<StubRoute>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

test("two exports can be in flight at once and each clears its own button", async () => {
  const csv = deferredRoute();
  const xlsx = deferredRoute();
  stubApi({
    exports: (url) => (url.includes("format=csv") ? csv.promise : xlsx.promise),
  });
  renderControl();

  const csvButton = screen.getByTestId("fraud-band-distribution-export-csv");
  const xlsxButton = screen.getByTestId("fraud-band-distribution-export-xlsx");

  await userEvent.click(csvButton);
  await waitFor(() => {
    expect(csvButton).toBeDisabled();
  });
  // The sibling is deliberately still enabled while CSV runs — which is the
  // documented behaviour and therefore makes the state below reachable by an
  // ordinary reader rather than by a race.
  expect(xlsxButton).toBeEnabled();
  await userEvent.click(xlsxButton);

  // **Both busy at once**, which a single `pending: ExportFormat | null` could
  // not represent: the second click overwrote the first format and CSV stopped
  // saying it was working while its request was still on the wire.
  await waitFor(() => {
    expect(xlsxButton).toBeDisabled();
  });
  expect(csvButton).toBeDisabled();
  expect(csvButton).toHaveTextContent("CSV…");
  expect(xlsxButton).toHaveTextContent("XLSX…");

  // The **second** request settles first, which is the ordering that used to
  // clear the first one's busy state along with its own.
  xlsx.settle(EXPORT_FILE);
  await waitFor(() => {
    expect(xlsxButton).toBeEnabled();
  });
  expect(csvButton).toBeDisabled();

  csv.settle(EXPORT_FILE);
  await waitFor(() => {
    expect(csvButton).toBeEnabled();
  });
});

test("a refusal reaches the control that asked for it, even after a later export succeeded", async () => {
  const csv = deferredRoute();
  const xlsx = deferredRoute();
  stubApi({
    exports: (url) => (url.includes("format=csv") ? csv.promise : xlsx.promise),
  });
  renderControl();

  await userEvent.click(screen.getByTestId("fraud-band-distribution-export-csv"));
  await waitFor(() => {
    expect(screen.getByTestId("fraud-band-distribution-export-csv")).toBeDisabled();
  });
  await userEvent.click(screen.getByTestId("fraud-band-distribution-export-xlsx"));

  // The later click succeeds first. With `mutate`'s per-call `onSuccess`/
  // `onError`, this is the moment the earlier click's callbacks were detached:
  // `MutationObserver.mutate` removes the observer from the previous mutation
  // and overwrites `mutateOptions`, so what happened to CSV afterwards reached
  // nothing at all.
  xlsx.settle(EXPORT_FILE);
  await waitFor(() => {
    expect(screen.getByTestId("toast-message")).toBeInTheDocument();
  });

  csv.settle(EXPORT_TOO_LARGE);

  // **AC 4**: the refusal is rendered at the control that produced it, with the
  // server's own numbers, rather than vanishing because a different button had
  // been clicked in the meantime.
  const alert = await screen.findByTestId("fraud-band-distribution-export-error");
  expect(alert).toHaveTextContent("caps an export at 50000");
  expect(alert).toHaveRole("alert");
  expect(screen.getByTestId("fraud-band-distribution-export-csv")).toBeEnabled();
});

test("the component holds no rows and folds nothing", () => {
  // Read off the working directory rather than `import.meta.url`: this file runs
  // under the jsdom environment, where `import.meta.url` is the dev server's
  // `http://` address and `readFileSync` refuses it. `noDerivation.test.ts` runs
  // in node and can use the module URL; this one cannot.
  const source = readFileSync(
    path.resolve(process.cwd(), "src/features/dashboard/export/ExportControl.tsx"),
    "utf8",
  );

  // The blunt half of `noDerivation.test.ts`, restated at the file it is about.
  // That guard scans this folder and would fail the build on either of these;
  // asserting them here as well is what names the temptation at the place a
  // future reader would act on it — the rows are in the query cache one
  // component away, and a CSV assembled from them is one `.map().join()`.
  expect(source).not.toContain(".sort(");
  expect(source).not.toContain(".reduce(");
  // `.join(` is deliberately **not** asserted here, and the reason is worth a
  // line: this file reads the source raw, and the component's own docstring
  // names `.map().join()` as the temptation it exists in spite of — so the
  // assertion would fail on the sentence explaining why it must not happen.
  // `noDerivation.test.ts` strips comments before scanning and is where the
  // stricter form of this check lives.
});
