import { expect, type Page } from "@playwright/test";

import { byRole } from "./selectors";

/**
 * Shared login-as-persona plumbing (AD-15).
 *
 * Every later story's spec logs in through **this** helper, and this helper
 * drives the real login screen — no cookie injection, no API shortcut. Two
 * reasons: the login path stays covered by every spec that runs, and when
 * the Deferred IdP decision lands, one file changes instead of forty.
 *
 * Personas are identified by name + role rather than by id (ids are
 * identity-column surrogates that shift if the seed changes) or by full
 * label (labels are derived from employer assignments and would churn).
 */

export type Role = "supervisor" | "handler" | "analyst";

export const ROLE_CARD_NAME: Record<Role, RegExp> = {
  supervisor: /^Supervisor/,
  handler: /Claims Handler/,
  analyst: /Data Analyst/,
};

/** Personas the Story 1.2 seed guarantees, with the shell each lands on. */
export const PERSONAS = {
  fullPortfolioSupervisor: { name: "David Bline", role: "supervisor" as Role, home: "/dashboard" },
  scopedSupervisor: { name: "Jennifer Park", role: "supervisor" as Role, home: "/dashboard" },
  analyst: { name: "David Bline", role: "analyst" as Role, home: "/dashboard" },
  handler: { name: "Kaya Johnson", role: "handler" as Role, home: "/workspace" },
};

export async function selectRole(page: Page, role: Role): Promise<void> {
  await byRole(page, "radio", ROLE_CARD_NAME[role]).check();
}

/** Pick the role card, choose the persona, enter the console. */
export async function loginAs(
  page: Page,
  persona: { name: string; role: Role; home: string },
): Promise<void> {
  await page.goto("/");
  await selectRole(page, persona.role);

  const picker = byRole(page, "combobox", "Log in as");
  await expect(picker).toBeEnabled();

  // Select by the option's value (the app_user id the API returned) rather
  // than by label: the labels are derived from employer assignments, so
  // matching on them would make every spec brittle to a seed change.
  const option = picker.locator("option").filter({ hasText: persona.name }).first();
  const personaId = await option.getAttribute("value");
  expect(personaId, `no ${persona.role} persona named ${persona.name}`).not.toBeNull();
  await picker.selectOption(personaId!);

  await byRole(page, "button", /Enter Console/).click();
  await page.waitForURL(`**${persona.home}`);
}

export async function switchPersona(page: Page): Promise<void> {
  await byRole(page, "button", "↩ Switch").click();
  await page.waitForURL((url) => url.pathname === "/");
}
