import { test as setup } from "@playwright/test";

import { resetDb } from "./reset";

/**
 * Project-dependency setup (not globalSetup) per AD-15: the `stories`
 * project depends on this, proving the reset mechanism before any spec
 * runs. The per-spec-file reset itself is enforced by the auto fixture in
 * fixtures/test.ts — specs import { test } from there, never from
 * @playwright/test directly.
 */
setup("reset database", () => {
  resetDb();
});
