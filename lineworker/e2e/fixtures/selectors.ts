import type { Locator, Page } from "@playwright/test";

/**
 * Selector policy (AD-15): accessible role first, data-testid second,
 * never CSS classes. Route all spec lookups through these helpers so the
 * policy is enforced by construction. The login-as-persona fixture arrives
 * with Story 1.3.
 */
export function byRole(
  page: Page,
  role: Parameters<Page["getByRole"]>[0],
  name?: string | RegExp,
): Locator {
  return page.getByRole(role, name === undefined ? undefined : { name });
}

export function byTestId(page: Page, testId: string): Locator {
  return page.getByTestId(testId);
}
