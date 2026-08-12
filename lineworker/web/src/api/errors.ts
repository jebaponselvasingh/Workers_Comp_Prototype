/**
 * The client half of the RFC 9457 contract.
 *
 * The SPA switches on `status` and, where it needs to distinguish causes
 * within a status, on `type` — never on `detail`, which is prose written
 * for humans and free to change.
 */

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
}

export class ApiError extends Error {
  readonly problem: Problem;
  /**
   * The parsed response body, before it was narrowed to the four RFC 9457
   * members (Story 2.3).
   *
   * RFC 9457 §3.2 lets a problem document carry extension members, and this
   * API uses one: a 409 attaches the **fresh entity** so a stale writer can
   * render current state without a second round trip. `problem` stays the
   * four-field shape every caller can rely on; anything beyond it is read
   * through `problemExtension` below, which is where the "is it really
   * there?" check lives.
   */
  readonly body: unknown;

  constructor(problem: Problem, body: unknown = undefined) {
    super(problem.detail || problem.title);
    this.name = "ApiError";
    this.problem = problem;
    this.body = body;
  }

  get status(): number {
    return this.problem.status;
  }
}

/**
 * An RFC 9457 extension member, if this error is one and carries it.
 *
 * Unchecked at the type level and deliberately so: the value crossed the
 * network, and pretending a cast is a guarantee is how a 409 with a
 * truncated body becomes a blank case file. Callers narrow with the
 * generated types and treat `undefined` as "the server did not say".
 */
export function problemExtension<T>(error: unknown, member: string): T | undefined {
  if (!(error instanceof ApiError) || typeof error.body !== "object" || error.body === null) {
    return undefined;
  }
  const value = (error.body as Record<string, unknown>)[member];
  return value === undefined || value === null ? undefined : (value as T);
}

/** "Not signed in" — the one error the whole app handles the same way. */
export function isUnauthenticated(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/**
 * "No such thing, for you" — an *answer*, not a failure (Story 2.2).
 *
 * The case-file pane has to tell "this claim is not in your caseload" from
 * "the request did not work": the first is a fact a stale link deserves to
 * be told plainly, the second is a retry. Named here beside
 * `isUnauthenticated` rather than compared inline in a component, so the
 * status codes stay in the module that owns the error contract.
 *
 * Note what a 404 deliberately does *not* distinguish: a claim that does not
 * exist from one that belongs to another employer. The server answers both
 * the same way so that a caller cannot enumerate a portfolio they cannot
 * read (AD-7), and the SPA must not try to reconstruct the difference.
 */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/**
 * "Somebody else got there first" — a stale write (Story 2.3, AD-9).
 *
 * Its own predicate rather than a `status === 409` at the call site, because
 * the response that follows it is prescribed: roll the optimistic value
 * back, render the fresh entity the body carries, and refresh the tracked
 * version. Never a silent retry — the handler's edit was written against
 * values that have since changed, and re-sending it would overwrite
 * somebody's work without either of them knowing.
 */
export function isConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

/**
 * "That patch cannot be applied" — a validation refusal (Story 2.3).
 *
 * Rendered inline at the field rather than as a dialog or a toast (NFR-3,
 * UX-DR11). The server's `detail` names the field and the rule and never
 * echoes the submitted value (AD-11), so it is safe to show verbatim.
 */
export function isInvalidPatch(error: unknown): boolean {
  return error instanceof ApiError && error.status === 422;
}
