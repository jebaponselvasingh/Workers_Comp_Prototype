import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterAll, afterEach } from "vitest";

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
  Element.prototype.scrollIntoView = () => {};
}

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
