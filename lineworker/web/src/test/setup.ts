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
