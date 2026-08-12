/**
 * The one API client (AD-1, Lists convention).
 *
 * Types come from `schema.d.ts`, generated from the server's OpenAPI
 * document by `npm run generate:api` — so a field the backend renamed is a
 * TypeScript error here, not a runtime `undefined` in front of a user.
 * Nothing in `web/` may hand-roll a fetch to `/api`.
 *
 * Session cookies ride along automatically: the SPA and the API are the
 * same origin (nginx proxies `/api`), so fetch's default credentials mode
 * already sends them, and the cookie is HttpOnly — no code here can read it.
 */
import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./schema";
import { ApiError, type Problem } from "./errors";

/**
 * openapi-fetch returns errors as values. Every caller in the app is a
 * TanStack Query function, and Query decides success/failure by whether the
 * function threw — so translate once, here, rather than in each hook.
 */
const throwProblems: Middleware = {
  async onResponse({ response }) {
    if (response.ok) return response;

    // A problem+json body is the contract (RFC 9457) — but not every error
    // reaching the SPA comes from FastAPI. A proxy 404, a gateway 502 or an
    // offline fetch carries no envelope, and one that answers
    // `{"detail": "..."}` parses fine while carrying none of the fields we
    // rely on. So parse what is there, then fill in every field: `status`
    // drives retry and redirect decisions, and `title`/`detail` are what
    // the user actually reads — an undefined one renders as "Could not
    // enter the console." with no reason attached.
    let body: Partial<Problem>;
    try {
      body = (await response.clone().json()) as Partial<Problem>;
    } catch {
      body = {};
    }

    const problem: Problem = {
      type: body.type ?? "about:blank",
      title: body.title ?? response.statusText ?? "Request failed",
      status: response.status,
      detail: body.detail ?? `The server answered ${response.status}.`,
    };
    // The raw body rides along beside the narrowed four fields: RFC 9457
    // extension members are how a 409 carries the fresh entity (Story 2.3),
    // and filling in defaults above would otherwise have discarded it.
    throw new ApiError(problem, body);
  },
};

export const api = createClient<paths>({
  baseUrl: "/api",
  // Late-bound on purpose. openapi-fetch otherwise captures `globalThis.fetch`
  // at module-evaluation time, which silently outlives any later replacement
  // of it — component tests stub fetch after imports are evaluated, and would
  // hit the network instead.
  fetch: (request) => globalThis.fetch(request),
});
api.use(throwProblems);
