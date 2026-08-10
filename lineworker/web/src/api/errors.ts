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

  constructor(problem: Problem) {
    super(problem.detail || problem.title);
    this.name = "ApiError";
    this.problem = problem;
  }

  get status(): number {
    return this.problem.status;
  }
}

/** "Not signed in" — the one error the whole app handles the same way. */
export function isUnauthenticated(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}
