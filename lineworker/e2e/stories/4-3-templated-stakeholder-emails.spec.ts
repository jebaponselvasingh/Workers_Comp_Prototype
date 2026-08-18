import { PERSONAS, loginAs, switchPersona } from "../fixtures/login";
import {
  BLANK_COMPOSE_RECIPIENTS,
  EMAIL_TEMPLATE_KEYS,
  EMAIL_TEMPLATE_LABEL,
  claimIdsInStage,
  expectedMeetingsFor,
  expectedTemplateRecipients,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 4.3 — Templated Stakeholder Emails.
 *
 * The last surface in Epic 4, and six things about it are only true in a
 * browser. This spec is organised around them.
 *
 * 1. **A logged email survives a reload.** The prototype's `emailsStore` is a
 *    browser-lifetime array and its send is an `alert()`; the whole of
 *    FR-DIARY-3 is that a row is written instead. Only a real navigation can
 *    show that, and it is the one assertion no unit test can make.
 *
 * 2. **The merge is the server's, end to end.** A vitest can prove the composer
 *    renders whatever its hook returned — it cannot prove that what a real
 *    `GET /email-templates/{key}/merged` returns for a *seeded* claim, resolved
 *    under a *session's* scope, arrives with every `{{…}}` resolved and the
 *    claim under the cursor named in it. AD-1 says the SPA interpolates
 *    nothing; this is where "nothing" is checked against a real database.
 *
 * 3. **Story 4.1's ✉ seam is closed, across two panes.** The meeting card is in
 *    the 📅 Meetings sub-tab and the letter it opens is merged from a meeting
 *    id the browser only has because the diary list gave it one. The send then
 *    moves the pane to ✉ Emails, which is two sub-tabs and one modal in one
 *    click — and is the only place the post-send switch is observable at all.
 *
 * 4. **The refusals are inline and none of them is a native dialog.** The
 *    prototype answers an empty subject with `alert('Please enter a subject.')`.
 *    A `page.on("dialog")` listener that *fails the test* is a stronger
 *    statement than an assertion about a message being visible (NFR-3, UX-DR11).
 *
 * 5. **"No claim selected" is a real state of the running console**, not a prop
 *    a test passed. See `withNoSelection` for what it takes to reach it and why
 *    the seed cannot produce it on its own.
 *
 * 6. **A sent log belongs to its author.** The seed puts two handlers on John
 *    Deere, so employer scope alone would show Sarah what Kaya wrote. The
 *    session cookie is what decides, and this is where a session exists.
 *
 * **What this spec deliberately does not assert.** The audit rows, the cursor,
 * the 404s for a template key or a claim outside scope, and the length caps:
 * `server/tests/test_emails.py` covers every one of them against the same
 * database this stack runs, and nothing in the product reads `audit_event`
 * (Story 2.3's ruling — inventing an endpoint so a spec could use one would be
 * the test dictating the surface).
 *
 * **The viewport is set explicitly.** The copilot aside is `hidden … xl:flex` —
 * below 1280px the right pane is mounted but not painted, so every assertion
 * about the diary would fail for a reason that has nothing to do with the story.
 * Playwright's Desktop Chrome default is exactly 1280 wide, which is the
 * breakpoint itself; stating a width above it makes the dependency visible
 * rather than lucky. Unlike Stories 4.1 and 4.2 there is no under-the-breakpoint
 * test here: the composer is a Radix dialog portalled to `document.body` and
 * would simply open, which is the case 4.1 already pins for the scheduler.
 *
 * **These tests share one database.** The AD-15 fixture resets per spec *file*
 * and `email_log` starts empty (there is no seed, deliberately — the six
 * *templates* are the seed), so each test below reads current state before it
 * writes rather than assuming a count.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

/**
 * The recipient words a *card* and a *toast* use — `PARTICIPANT_TAG_LABEL`,
 * restated.
 *
 * A second copy on purpose, `MEETING_TYPE_LABEL`'s rule: a spec that imported
 * the app's own map would assert that the map equals itself. Note that these are
 * not the composer's labels — the checkbox grid says "👨‍⚕️ Treating Physician"
 * where a card says "Physician" — which is the UI owning its wording per
 * surface, and is why this map is here rather than shared with the fixture.
 */
const RECIPIENT_TAG_LABEL: Record<string, string> = {
  employee: "Employee",
  employer_hr: "Employer HR",
  ncm: "NCM",
  treating_physician: "Physician",
  supervisor: "Supervisor",
  attorney: "Attorney",
};

interface EmailLog {
  id: number;
  claimId: string | null;
  workerName: string | null;
  templateKey: string | null;
  subject: string;
  body: string | null;
  priority: "normal" | "high" | "urgent";
  recipients: string[];
  sentAt: string;
}

interface EmailLogList {
  items: EmailLog[];
  nextCursor: string | null;
  total: number | null;
}

interface MergedEmail {
  claimId: string | null;
  subject: string;
  body: string;
  recipients: string[];
}

interface Meeting {
  id: number;
  claimId: string | null;
  meetingType: string;
  participants: string[];
}

interface MeetingList {
  items: Meeting[];
  nextCursor: string | null;
  total: number;
}

/**
 * The viewer's local calendar day, as `web/src/lib/clock.ts::todayIso` builds it.
 *
 * Restated rather than imported, so the spec is an oracle rather than an echo.
 * Playwright runs on the same host as the browser, so "local" agrees on both
 * sides.
 */
function todayIso(now = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/** The caller's own sent log, newest first — the list the sub-tab renders. */
async function emailsOf(page: Page): Promise<EmailLogList> {
  const response = await page.request.get("/api/claims-diary/emails");
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as EmailLogList;
}

/**
 * The caller's whole meeting book — **asserted to fit one page**.
 *
 * `diaryOf`'s bound from the 4.1 and 4.2 specs, kept: comparing a payload
 * against the cards on screen is only an oracle while the payload is the whole
 * book, and `asOf` is sent because the sub-tab sends it — a fetch that omitted
 * it would come back judged at the server's UTC date while the DOM beside it was
 * judged at the browser's.
 */
async function diaryOf(page: Page): Promise<MeetingList> {
  const response = await page.request.get(
    `/api/claims-diary/meetings?asOf=${todayIso()}&limit=200`,
  );
  expect(response.status(), await response.text()).toBe(200);
  const list = (await response.json()) as MeetingList;
  expect(list.nextCursor, "the diary outgrew one page; walk the cursor").toBeNull();
  return list;
}

/** Open the workspace on a claim with the copilot pane on screen. */
async function openWorkspace(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "copilot")).toBeVisible();
}

/** One logged email's card, addressed by the server's id — never by index. */
function emailCard(page: Page, emailId: number) {
  return page.locator(`[data-testid="email-card"][data-email-id="${emailId}"]`);
}

/** One meeting card, addressed by the server's id — never by index. */
function meetingCard(page: Page, meetingId: number) {
  return page.locator(`[data-testid="meeting-card"][data-meeting-id="${meetingId}"]`);
}

/**
 * Which recipient boxes are ticked, as role tokens, sorted.
 *
 * Read off `data-state`, which is the Radix checkbox's own account of itself,
 * rather than off the label text — the tokens are what travels on the wire and
 * what the seed oracle names, and a set comparison is what "the template's set
 * *replaces* whatever was ticked" actually means. Sorted for the fixture's
 * reason: the seed writes one template's defaults out of vocabulary order and
 * the composer holds them in a `Set`, so order is not part of the contract.
 */
async function checkedRecipients(page: Page): Promise<string[]> {
  const boxes = await page
    .locator('[data-testid="composer-recipient"]')
    .evaluateAll((nodes) =>
      nodes.map((node) => ({
        recipient: node.getAttribute("data-recipient"),
        checked: node.getAttribute("data-state") === "checked",
      })),
    );
  return boxes
    .filter((box) => box.checked)
    .map((box) => box.recipient as string)
    .sort();
}

/** Open the ✉ Emails sub-tab and the composer over it. */
async function openComposer(page: Page): Promise<void> {
  await byTestId(page, "diary-subtab-emails").click();
  await expect(byTestId(page, "emails-subtab")).toBeVisible();
  await byTestId(page, "email-compose-open").click();
  await expect(byTestId(page, "email-composer")).toBeVisible();
}

/**
 * Hold the workspace in the state where **nothing is selected**.
 *
 * This takes explaining, because the obvious route does not work. `?claim=` is
 * the whole of the selection (AD-9), and `useSelectedClaim`'s auto-select fills
 * it with the first card of the first non-empty group the moment the queue
 * answers — so a plain `goto("/workspace")` is a claim-less workspace for a few
 * milliseconds and a selected one thereafter. The seed has no handler with an
 * empty book, so the state cannot be reached by choosing a persona either.
 *
 * What that hook's own docstring says is that auto-select "waits for a real
 * value, so a slow request cannot leave the URL pointing at nothing and an empty
 * caseload never navigates at all". This is that sentence, made deterministic:
 * the queue request is answered with the problem document the API would send if
 * it were unwell, `firstClaimIdOf` stays null, and the workspace holds no
 * selection for as long as the test needs one. Nothing else in the pane reads
 * the queue — the templates, the merge and the sent log are three other
 * endpoints — so what is under test is genuinely the composer with no claim, and
 * not a mock of it.
 */
async function withNoSelection(page: Page): Promise<void> {
  await page.route("**/api/claims/queue**", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/problem+json",
      body: JSON.stringify({
        type: "/problems/unavailable",
        title: "Service Unavailable",
        status: 503,
      }),
    }),
  );
}

test.use({ viewport: { width: 1440, height: 900 } });

test.describe("@story:4-3 @epic:4 templated stakeholder emails", () => {
  test("@smoke a handler merges a template, logs the send, and it survives a reload", async ({
    page,
  }) => {
    // The prototype's two `alert()`s — the empty-subject refusal and the
    // post-send confirmation — must both be gone. A listener that fails is a
    // stronger statement than an assertion about a message being visible.
    page.on("dialog", (dialog) => {
      throw new Error(`a native dialog appeared: ${dialog.message()}`);
    });

    await loginAs(page, PERSONAS.handler);

    // Current state, read rather than assumed: the AD-15 fixture resets per
    // spec *file*, so a `--grep`, a shard or a re-order must not change what
    // this test believes about the log it is about to add to.
    const before = await emailsOf(page);

    const claimId = claimIdsInStage(KAYA.name, KAYA.role, "treatment")[0];
    await openWorkspace(page, claimId);

    // --- AC 4: the sub-tab, and the state it is in ------------------------
    await byTestId(page, "diary-subtab-emails").click();
    await expect(byTestId(page, "diary-subtab-emails")).toHaveAttribute("aria-selected", "true");
    await expect(byTestId(page, "emails-subtab")).toBeVisible();

    // Conditional on the state just read, not on the run order. `email_log`
    // has no seed, so on a whole-file run this branch is the one taken.
    if (before.total === 0) {
      await expect(byTestId(page, "emails-empty")).toHaveText(
        "No emails sent yet. Use the button below to compose.",
      );
    } else {
      await expect(byTestId(page, "email-card")).toHaveCount(before.items.length);
    }

    // --- AC 1: what the composer opens holding ----------------------------
    await byTestId(page, "email-compose-open").click();
    await expect(byTestId(page, "email-composer")).toBeVisible();

    // Six recipient boxes, Employee alone ticked.
    await expect(byTestId(page, "composer-recipient")).toHaveCount(
      Object.keys(RECIPIENT_TAG_LABEL).length,
    );
    expect(await checkedRecipients(page)).toEqual([...BLANK_COMPOSE_RECIPIENTS]);

    // Six template buttons, in the composer's button order, with the seed's
    // own labels — the oracle's list, not the payload's.
    const templateKeys = await page
      .locator('[data-testid="composer-template"]')
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-template-key")));
    expect(templateKeys).toEqual([...EMAIL_TEMPLATE_KEYS]);
    for (const key of EMAIL_TEMPLATE_KEYS) {
      await expect(
        page.locator(`[data-testid="composer-template"][data-template-key="${key}"]`),
      ).toHaveText(EMAIL_TEMPLATE_LABEL[key]);
    }

    // The read-only claim reference, and a priority that opens on Normal.
    await expect(byTestId(page, "composer-claim")).toHaveAttribute("data-claim-id", claimId);
    await expect(byTestId(page, "composer-priority")).toHaveValue("normal");
    // Nothing has been merged yet, so there is nothing to send.
    await expect(byTestId(page, "composer-subject")).toHaveValue("");

    // --- AC 2: the merge is the server's, and it resolves everything ------
    const template = "three_point_contact" as const;
    await page
      .locator(`[data-testid="composer-template"][data-template-key="${template}"]`)
      .click();

    // The claim under the cursor is named in the merged subject, which is what
    // makes this a *merge* rather than the template's raw text. Waiting on the
    // value is also waiting on the round trip.
    await expect(byTestId(page, "composer-subject")).toHaveValue(new RegExp(claimId));
    await expect(
      page.locator(`[data-testid="composer-template"][data-template-key="${template}"]`),
    ).toHaveAttribute("data-selected", "true");

    const mergedSubject = await byTestId(page, "composer-subject").inputValue();
    const mergedBody = await byTestId(page, "composer-body").inputValue();
    // AD-1's observable half: the server resolved every placeholder, and the
    // SPA had nothing left to substitute. `render_template` raises on an
    // unknown token rather than rendering an empty one, which is what makes
    // this assertion a real one instead of a coincidence.
    expect(mergedSubject).not.toContain("{{");
    expect(mergedBody).not.toContain("{{");
    expect(mergedBody).toContain(claimId);
    // …while the *square*-bracket prompts are template text and must survive
    // untouched (AD-2). The 3-Point letter has none, so this is asserted on the
    // one that does, without leaving the modal — a second template press.
    await page
      .locator('[data-testid="composer-template"][data-template-key="settlement_notice"]')
      .click();
    await expect(byTestId(page, "composer-body")).toHaveValue(/\$\[AMOUNT\]/);
    expect(await byTestId(page, "composer-body").inputValue()).toContain("[RATING]%");
    expect(await checkedRecipients(page)).toEqual(
      expectedTemplateRecipients("settlement_notice"),
    );

    // Back to the one this test sends, which also proves a template *replaces*
    // rather than patches: the recipient set is the new template's exactly,
    // never a union with what the last one ticked.
    await page
      .locator(`[data-testid="composer-template"][data-template-key="${template}"]`)
      .click();
    await expect(byTestId(page, "composer-subject")).toHaveValue(mergedSubject);
    expect(await checkedRecipients(page)).toEqual(expectedTemplateRecipients(template));

    // --- AC 4: the send is a row, a toast and a list entry ----------------
    await byTestId(page, "composer-send").click();
    await expect(byTestId(page, "email-composer")).toBeHidden();

    // The prototype's post-send `alert()` in its proper form — non-blocking,
    // polite, and gone on its own (UX-DR11).
    await expect(byTestId(page, "toast")).toBeVisible();
    await expect(byTestId(page, "toast-message")).toContainText("✓ Email logged to:");
    await expect(byTestId(page, "toast")).toHaveAttribute("data-tone", "ok");

    // The pane is on ✉ Emails — trivially so here, because that is where the
    // ＋ button lives. The *interesting* case is the meeting test below, which
    // opens the composer from 📅 Meetings and watches the pane move.
    await expect(byTestId(page, "diary-subtab-emails")).toHaveAttribute("aria-selected", "true");
    await expect(byTestId(page, "email-card")).toHaveCount(before.items.length + 1);

    const written = await emailsOf(page);
    expect(written.total).toBe((before.total ?? 0) + 1);
    // Element 0 is the *server's* newest, which this test then checks is the
    // email it just sent — an identity check on the ordering contract rather
    // than a positional shortcut past it.
    const logged = written.items[0];
    expect(logged.subject).toBe(mergedSubject);
    expect(logged.body).toBe(mergedBody);
    expect(logged.claimId).toBe(claimId);
    expect(logged.templateKey).toBe(template);
    expect(logged.priority).toBe("normal");
    expect([...logged.recipients].sort()).toEqual(expectedTemplateRecipients(template));

    const card = emailCard(page, logged.id);
    await expect(card.getByTestId("email-subject")).toContainText(`✉ ${mergedSubject}`);
    await expect(card.getByTestId("email-sent-badge")).toContainText("Sent ");
    // The `To:` line renders the roles in the **server's** order, mapped through
    // the words this file restates — and gains the worker's name because the
    // email names a claim.
    await expect(card.getByTestId("email-recipients")).toHaveText(
      `To: ${logged.recipients.map((role) => RECIPIENT_TAG_LABEL[role]).join(", ")} · ${logged.workerName}`,
    );
    // Truncated, and it says so. Asserted as "the tail is not here, and the
    // ellipsis is" rather than by recomputing the cut, which would make the
    // spec a second copy of `snippet()`.
    await expect(card.getByTestId("email-snippet")).toContainText("…");
    await expect(card.getByTestId("email-snippet")).not.toContainText("WC Claims Adjuster");
    // Normal carries no chip — a badge on every card would say nothing.
    await expect(card.getByTestId("email-priority")).toHaveCount(0);
    await expect(byTestId(page, "emails-count")).toContainText(String(written.total));

    // --- The claim of the whole story: it survives a reload ---------------
    await page.reload();
    // The pane comes back on 📓 Notes: sub-tab selection is local UI state and
    // deliberately not in the URL (only `?claim=` is).
    await byTestId(page, "diary-subtab-emails").click();
    await expect(byTestId(page, "email-card")).toHaveCount(before.items.length + 1);
    await expect(emailCard(page, logged.id)).toBeVisible();
    await expect(emailCard(page, logged.id).getByTestId("email-subject")).toContainText(
      mergedSubject,
    );
  });

  test("an empty subject is refused at the control, and nothing is written (AC 3)", async ({
    page,
  }) => {
    // `alert('Please enter a subject.')` is the prototype's answer and the one
    // NFR-3 forbids. The listener is the assertion.
    page.on("dialog", (dialog) => {
      throw new Error(`a native dialog appeared: ${dialog.message()}`);
    });

    await loginAs(page, PERSONAS.handler);
    const before = await emailsOf(page);

    await openWorkspace(page, claimIdsInStage(KAYA.name, KAYA.role, "treatment")[0]);
    await openComposer(page);

    // A blank composition: Employee ticked, nothing typed.
    await expect(byTestId(page, "composer-subject")).toHaveValue("");
    await byTestId(page, "composer-body").fill("A body with no subject over it.");
    await byTestId(page, "composer-send").click();

    // Inline, at the field, and the field says it is invalid.
    await expect(byTestId(page, "composer-error")).toBeVisible();
    await expect(byTestId(page, "composer-subject")).toHaveAttribute("aria-invalid", "true");

    // Nothing was written — asserted against the API rather than against the
    // list on screen, which would agree with a client-side refusal that had
    // nonetheless posted.
    expect((await emailsOf(page)).total).toBe(before.total);

    // Whitespace is not a subject either, and the refusal survives it.
    await byTestId(page, "composer-subject").fill("   ");
    await byTestId(page, "composer-send").click();
    await expect(byTestId(page, "composer-error")).toBeVisible();
    expect((await emailsOf(page)).total).toBe(before.total);

    // And the refusal does not outlive the box stopping being empty.
    await byTestId(page, "composer-subject").fill("A subject, at last.");
    await expect(byTestId(page, "composer-error")).toHaveCount(0);
    await expect(byTestId(page, "composer-subject")).not.toHaveAttribute("aria-invalid", "true");
  });

  test("a send addressed to nobody is refused at the fieldset (AC 3)", async ({ page }) => {
    page.on("dialog", (dialog) => {
      throw new Error(`a native dialog appeared: ${dialog.message()}`);
    });

    await loginAs(page, PERSONAS.handler);
    const before = await emailsOf(page);

    await openWorkspace(page, claimIdsInStage(KAYA.name, KAYA.role, "treatment")[0]);
    await openComposer(page);

    await byTestId(page, "composer-subject").fill("Addressed to nobody.");
    // Employee is the only box a blank composition ticks, so un-ticking it
    // empties the set. The prototype allowed exactly this and logged a send
    // with no recipients; the story requires at least one.
    expect(await checkedRecipients(page)).toEqual([...BLANK_COMPOSE_RECIPIENTS]);
    await page
      .locator('[data-testid="composer-recipient"][data-recipient="employee"]')
      .click();
    expect(await checkedRecipients(page)).toEqual([]);

    await byTestId(page, "composer-send").click();
    await expect(byTestId(page, "composer-error")).toBeVisible();
    expect((await emailsOf(page)).total).toBe(before.total);

    // Ticking anybody clears it, and the modal is still open and usable.
    await page
      .locator('[data-testid="composer-recipient"][data-recipient="supervisor"]')
      .click();
    await expect(byTestId(page, "composer-error")).toHaveCount(0);
    await expect(byTestId(page, "email-composer")).toBeVisible();
  });

  test("a meeting's ✉ opens the merged confirmation and the send moves the pane (AC 5)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);
    const before = await emailsOf(page);

    // Addressed by the `(claim, type)` pair the seed oracle names, then by the
    // **server's** id — never by `.first()`. Story 4.1's rule, kept.
    const wanted = expectedMeetingsFor(KAYA.name, KAYA.role)[0];
    const book = await diaryOf(page);
    const meeting = book.items.find(
      (item) => item.claimId === wanted.claimId && item.meetingType === wanted.meetingType,
    );
    expect(meeting, "the seeded demo meeting is not in the diary").toBeDefined();
    expect(
      meeting!.participants.length,
      "a meeting with no participants proves nothing about pre-checking",
    ).toBeGreaterThan(0);

    // Open on a *different* claim than the meeting's, so the letter cannot be
    // right by accident: the confirmation is about the meeting's claim, which
    // is what `MergedEmailResponse.claimId` is echoed back for.
    const other = claimIdsInStage(KAYA.name, KAYA.role, "treatment").find(
      (id) => id !== meeting!.claimId,
    );
    expect(other, "the seed should give this handler a second treatment claim").toBeDefined();
    await openWorkspace(page, other!);

    await byTestId(page, "diary-subtab-meetings").click();
    await expect(byTestId(page, "meetings-subtab")).toBeVisible();

    // --- AC 5: enabled, from this story on --------------------------------
    // It shipped in 4.1 wrapped in a tooltip seam and disabled; 4.3 deleted the
    // wrapper. `4-1-meeting-scheduling-management.spec.ts` was amended to
    // assert the enabled control rather than deleted (AD-15).
    const email = meetingCard(page, meeting!.id).getByTestId("meeting-email");
    await expect(email).toBeEnabled();
    await email.click();
    await expect(byTestId(page, "email-composer")).toBeVisible();

    // What the server says the letter is. Fetched *after* the click so the
    // browser's request has already happened — this is the oracle for "the SPA
    // renders what the merge returned", which is the whole of AD-1 on this path.
    const draftResponse = await page.request.get(
      `/api/claims-diary/meetings/${meeting!.id}/email-draft`,
    );
    expect(draftResponse.status(), await draftResponse.text()).toBe(200);
    const draft = (await draftResponse.json()) as MergedEmail;

    await expect(byTestId(page, "composer-subject")).toHaveValue(draft.subject);
    await expect(byTestId(page, "composer-body")).toHaveValue(draft.body);
    expect(draft.subject).not.toContain("{{");
    expect(draft.body).not.toContain("{{");
    // The meeting's claim, not the workspace's — and the read-only field shows
    // the one the send will carry.
    expect(draft.claimId).toBe(meeting!.claimId);
    await expect(byTestId(page, "composer-claim")).toHaveAttribute(
      "data-claim-id",
      meeting!.claimId!,
    );
    expect(draft.subject).toContain(meeting!.claimId!);

    // The participants *are* the recipient vocabulary, so the mapping is the
    // identity and the prototype's substring match on labels is gone.
    expect([...draft.recipients].sort()).toEqual([...meeting!.participants].sort());
    expect(await checkedRecipients(page)).toEqual([...meeting!.participants].sort());

    // --- AC 4: and the send moves the pane to ✉ Emails --------------------
    // The only place this is observable: the composer was opened from 📅
    // Meetings, and `requestEmails()` is what puts the log in front of the
    // handler rather than leaving it somewhere to go and find.
    await byTestId(page, "composer-send").click();
    await expect(byTestId(page, "email-composer")).toBeHidden();
    await expect(byTestId(page, "toast-message")).toContainText("✓ Email logged to:");
    await expect(byTestId(page, "diary-subtab-emails")).toHaveAttribute("aria-selected", "true");
    await expect(byTestId(page, "emails-subtab")).toBeVisible();

    const written = await emailsOf(page);
    expect(written.total).toBe((before.total ?? 0) + 1);
    const logged = written.items[0];
    expect(logged.subject).toBe(draft.subject);
    expect(logged.claimId).toBe(meeting!.claimId);
    // A meeting's letter starts from no template at all — `template_id` is a
    // real FK and there is no row for "a meeting".
    expect(logged.templateKey).toBeNull();
    await expect(emailCard(page, logged.id)).toBeVisible();

    // AD-12: reading a meeting to compose a letter writes nothing to it.
    const after = await diaryOf(page);
    expect(after.total).toBe(book.total);
    const unchanged = after.items.find((item) => item.id === meeting!.id);
    expect(unchanged!.participants).toEqual(meeting!.participants);
  });

  test("with no claim selected the templates are disabled and say why (AC 2)", async ({
    page,
  }) => {
    // See `withNoSelection` on why reaching this state takes any work at all.
    await withNoSelection(page);
    await loginAs(page, PERSONAS.handler);
    const before = await emailsOf(page);

    await page.goto("/workspace");
    await expect(byTestId(page, "copilot")).toBeVisible();
    await expect(page).not.toHaveURL(/claim=/);

    await openComposer(page);

    // The six buttons are all disabled, and the reason is *stated* rather than
    // merely greyed — NFR-3 forbids a dead click, and a reason only a pointer
    // can reach is a dead click for everybody else.
    for (const key of EMAIL_TEMPLATE_KEYS) {
      await expect(
        page.locator(`[data-testid="composer-template"][data-template-key="${key}"]`),
      ).toBeDisabled();
    }
    await expect(byTestId(page, "composer-template-reason")).toBeVisible();
    await expect(byTestId(page, "composer-template-reason")).not.toBeEmpty();
    // …and announced, not only shown.
    const reasonId = await byTestId(page, "composer-template-reason").getAttribute("id");
    expect(reasonId).not.toBeNull();
    await expect(
      page.locator('[data-testid="composer-template"]').first(),
    ).toHaveAttribute("aria-describedby", reasonId!);

    await expect(byTestId(page, "composer-claim")).toHaveValue("No claim selected");
    await expect(byTestId(page, "composer-claim")).toHaveAttribute("data-claim-id", "");

    // --- …and free composition still works --------------------------------
    expect(await checkedRecipients(page)).toEqual([...BLANK_COMPOSE_RECIPIENTS]);
    await byTestId(page, "composer-subject").fill("General enquiry — no claim attached.");
    await byTestId(page, "composer-body").fill("Written by hand, logged against nothing.");
    await byTestId(page, "composer-priority").selectOption("urgent");
    await byTestId(page, "composer-send").click();

    await expect(byTestId(page, "email-composer")).toBeHidden();
    await expect(byTestId(page, "toast-message")).toContainText("✓ Email logged to:");

    const written = await emailsOf(page);
    expect(written.total).toBe((before.total ?? 0) + 1);
    const logged = written.items[0];
    expect(logged.subject).toBe("General enquiry — no claim attached.");
    // The ERD's optional `CLAIM |o--o{ EMAIL_LOG` edge, exercised.
    expect(logged.claimId).toBeNull();
    expect(logged.workerName).toBeNull();
    expect(logged.templateKey).toBeNull();
    expect(logged.priority).toBe("urgent");

    const card = emailCard(page, logged.id);
    await expect(card).toHaveAttribute("data-claim-id", "");
    // No claim, so no ` · worker` tail on the recipients line.
    await expect(card.getByTestId("email-recipients")).toHaveText("To: Employee");
    // Urgent is one of the two priorities that does carry a chip.
    await expect(card.getByTestId("email-priority")).toHaveText("Urgent");
  });

  test("a handler's sent log is their own (AD-7)", async ({ page }) => {
    // The author half of the scope predicate, which `employer_scope` alone
    // would not give: the seed puts two handlers on John Deere. Asserted across
    // a persona switch rather than in pytest only, because the session cookie is
    // what decides and this is where a session exists.
    await loginAs(page, PERSONAS.handler);
    const existing = await emailsOf(page);

    await openWorkspace(page, claimIdsInStage(KAYA.name, KAYA.role, "treatment")[0]);
    await openComposer(page);
    await byTestId(page, "composer-subject").fill("Kaya's own stakeholder letter.");
    await byTestId(page, "composer-send").click();
    await expect(byTestId(page, "email-composer")).toBeHidden();

    const kaya = await emailsOf(page);
    expect(kaya.total).toBe((existing.total ?? 0) + 1);
    expect(kaya.total).toBeGreaterThan(0);

    await switchPersona(page);
    await loginAs(page, PERSONAS.scopedHandler);
    const sarah = await emailsOf(page);

    expect(sarah.total).toBe(0);
    const kayaIds = new Set(kaya.items.map((item) => item.id));
    expect(sarah.items.some((item) => kayaIds.has(item.id))).toBe(false);

    // And on screen: Sarah's ✉ Emails is the empty state, not Kaya's log.
    await openWorkspace(page, claimIdsInStage("Sarah Williams", "handler", "treatment")[0]);
    await byTestId(page, "diary-subtab-emails").click();
    await expect(byTestId(page, "emails-empty")).toBeVisible();
    await expect(byTestId(page, "email-card")).toHaveCount(0);
  });

  test("a supervisor cannot log an email, and sees nobody's", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);

    const listing = await page.request.get("/api/claims-diary/emails");
    expect(listing.status()).toBe(200);
    // Scoped to the *caller*: a supervisor sends nothing, so the list is empty
    // rather than their handlers'.
    expect(((await listing.json()) as EmailLogList).total).toBe(0);

    const refused = await page.request.post("/api/claims-diary/emails", {
      data: { subject: "A supervisor's letter.", recipients: ["employee"] },
    });
    expect(refused.status()).toBe(403);
    expect((await refused.json()).type).toBe("/problems/edit-not-permitted");
  });
});
