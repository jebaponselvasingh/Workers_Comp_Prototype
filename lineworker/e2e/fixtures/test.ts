import { test as base, expect } from "@playwright/test";

import { resetDb } from "./reset";

let lastSpecFile: string | undefined;

/**
 * Base test for every story spec: import { test, expect } from "../fixtures/test".
 *
 * The auto fixture enforces AD-15's reset-before-every-spec-file invariant
 * by construction — the first test of each spec file triggers
 * drop schema → migrate → seed, deterministic under workers: 1.
 *
 * `lastSpecFile` is per worker process, so the invariant only holds with a
 * single worker: two workers would interleave spec files and one could drop
 * the schema out from under a test running in the other. That is a silent,
 * nondeterministic failure, so the fixture asserts the config rather than
 * trusting it — raising `workers` needs a real cross-process lock first.
 */
export const test = base.extend<{ _freshDbPerFile: void }>({
  _freshDbPerFile: [
    async ({}, use, testInfo) => {
      if (testInfo.config.workers !== 1) {
        throw new Error(
          `AD-15 reset fixture requires workers: 1 (got ${testInfo.config.workers}). ` +
            "Parallel workers each keep their own reset bookkeeping and would " +
            "reset the shared database mid-test.",
        );
      }
      if (testInfo.file !== lastSpecFile) {
        resetDb();
        lastSpecFile = testInfo.file;
      }
      await use();
    },
    { auto: true },
  ],
});

export { expect };
