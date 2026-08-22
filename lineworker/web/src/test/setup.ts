import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeEach } from "vitest";

// vitest runs with globals:false, so testing-library's auto-cleanup
// never registers — do it explicitly.
afterEach(cleanup);

/**
 * jsdom gap, not an app gap: `Request` under vitest is Node's (undici),
 * which has no document to resolve a relative URL against, so
 * `new Request("/api/me")` throws "Failed to parse URL" — something that
 * cannot happen in a browser. The API client uses a same-origin relative
 * base URL by design (that is what makes the session cookie ride along),
 * so the fix belongs here rather than in the client.
 */
/**
 * Second jsdom gap: no `ResizeObserver`. Radix's floating layer measures
 * its trigger and arrow with one, so any tooltip, popover or select throws
 * on mount under vitest while working perfectly in a browser.
 *
 * A no-op observer is the right stub rather than a lie: the tests that
 * need it assert that the tooltip *content* is reachable and says the
 * right thing, never where it was positioned — positioning is the
 * browser's job and the e2e suite's to check.
 */
if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
}

/**
 * Third jsdom gap, same shape as the second: Radix's Select opens on a
 * pointer sequence and scrolls the chosen item into view, and jsdom
 * implements neither the Pointer Capture API nor `scrollIntoView`. Without
 * these the filter dropdown throws the moment a test clicks it, while
 * working in every real browser.
 *
 * No-ops rather than fakes, for the ResizeObserver reason: the tests that
 * open the dropdown assert which option was chosen and what was requested
 * as a result — never where the listbox was drawn or how far it scrolled,
 * which are the browser's business and the e2e suite's to check.
 */
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}

/**
 * Fourth jsdom gap, and the one Story 5.3 opened: **nothing has a size.**
 *
 * jsdom implements no layout, so `getBoundingClientRect()` answers an all-zero
 * box for every element however it is styled. Recharts 3.10's
 * `ResponsiveContainer` calls `getBoundingClientRect()` on mount, refuses to
 * render at a non-positive width or height, and returns `null` — so every chart
 * in every component test would render *nothing*, silently, while working
 * perfectly in a browser. A suite in that state does not fail; it passes over an
 * empty `<div>` and proves nothing about seven surfaces. `PortfolioCharts.test`
 * asserts an `<svg>` exists under each surface so this stub cannot go stale
 * without a test saying so.
 *
 * The box is deliberately crude, for the `ResizeObserver` stub's reason: the
 * tests assert that a chart's *values* reached the DOM — through each surface's
 * visible legend or its `sr-only` list — never where a bar was drawn or how wide
 * it came out. Geometry is the browser's business and the e2e suite's to check.
 *
 * Applied to `Element.prototype` because the measured node is Recharts' own
 * internal `<div>`, which a test has no handle on. **Restored after every test**,
 * for the `Request` patch's reason two blocks down: this is a process-wide
 * global, and the file's own `ResizeObserver` stub exists precisely because
 * Radix's floating layer positions itself from rects — so leaving a 1024x768
 * world behind would quietly move every popper placement and pointer hit-test in
 * the other thirty-three test files. An earlier draft claimed "nothing else in
 * this suite reads a rect", which was never true.
 */
const JSDOM_VIEWPORT = { width: 1024, height: 768 };

const REAL_BOUNDING_RECT = Element.prototype.getBoundingClientRect;

beforeEach(() => {
  Element.prototype.getBoundingClientRect = function boundingBox(): DOMRect {
    return {
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: JSDOM_VIEWPORT.width,
      bottom: JSDOM_VIEWPORT.height,
      width: JSDOM_VIEWPORT.width,
      height: JSDOM_VIEWPORT.height,
      toJSON: () => ({}),
    } as DOMRect;
  };
});

afterEach(() => {
  Element.prototype.getBoundingClientRect = REAL_BOUNDING_RECT;
});
;
}

/**
 * Fifth jsdom gap (Story 7.5): **no object URLs.**
 *
 * jsdom implements neither `URL.createObjectURL` nor `URL.revokeObjectURL`, so
 * the one line that turns a downloaded blob into something an `<a download>` can
 * point at throws — and `useExport` is the only code in the app that needs it.
 * Without these stubs every export test would fail on a browser API rather than
 * on anything about exporting.
 *
 * No-ops returning a fixed string rather than a fake blob registry, for the
 * `ResizeObserver` stub's reason: the tests assert what was *requested* and what
 * the control says afterwards — never that a file reached a filesystem, which is
 * the browser's business and the e2e suite's to check (it downloads a real one
 * and parses it).
 *
 * Assigned unconditionally rather than behind an `in` check, because jsdom does
 * define `URL` — it is the two static methods that are missing, so a guard on
 * the constructor would test the wrong thing.
 */
URL.createObjectURL = () => "blob:lineworker/export";
URL.revokeObjectURL = () => {};

const NodeRequest = globalThis.Request;

function withDocumentBase(input: RequestInfo | URL): RequestInfo | URL {
  if (typeof input !== "string") return input;
  try {
    // Absolute already — leave it alone.
    return new URL(input).href;
  } catch {
    // Relative in any form ("/api/me", "api/me", "../me"): resolve it the
    // way a browser would.
    return new URL(input, globalThis.location.href);
  }
}

class DocumentBaseRequest extends NodeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    super(withDocumentBase(input), init);
  }
}

globalThis.Request = DocumentBaseRequest as unknown as typeof Request;

// Restore afterwards: this is a process-wide global, and leaving a patched
// Request behind would leak into every other suite in the run.
afterAll(() => {
  globalThis.Request = NodeRequest;
});
