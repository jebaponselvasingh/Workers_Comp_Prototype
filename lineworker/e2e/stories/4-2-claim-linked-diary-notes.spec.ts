import { PERSONAS, loginAs, switchPersona } from "../fixtures/login";
import { claimIdsInStage } from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 4.2 — Claim-Linked Diary Notes.
 *
 * Five things are only true in a browser, and this spec is organised around
 * them.
 *
 * 1. **A note survives a reload.** The prototype's `diaryNotes` is a
 *    browser-lifetime object keyed by handler name and erased by F5; the whole
 *    of FR-DIARY-1 is that a row is written instead. Only a real navigation can
 *    show that, and it is the one assertion no unit test can make.
 *
 * 2. **Story 3.5's "Log Diary Entry →" now goes somewhere, and lands focused.**
 *    The checklist row is in the centre pane and the input is in the right one,
 *    so this is the only place both ends are on screen at once — and
 *    `document.activeElement` is not a thing a component test can meaningfully
 *    assert about a pane that is `display: none` at jsdom's default width.
 *
 * 3. **Saving that note makes the row stop firing.** The completion is
 *    entity-backed by design — there is no fifth `ActionCommand` and no ✓ on
 *    the diary row — so the proof is a checklist that regenerates *without* it
 *    after a write, which spans two endpoints and a re-render.
 *
 * 4. **The greeting's count is the server's.** `upcomingCount` is over the
 *    whole book while the summary beneath it is one filtered day, so the two
 *    numbers differ in real seeded data and a browser-side count would be
 *    visibly wrong on screen.
 *
 * 5. **Open Claim drives the queue and the case file.** Three panes, one URL.
 *
 * **What this spec deliberately does not assert.** The audit rows, the scope
 * refusals and the cursor: `server/tests/test_diary_notes.py` covers all three
 * against the same database this stack runs, and nothing in the product reads
 * `audit_event` (Story 2.3's ruling — inventing an endpoint so a spec could use
 * one would be the test dictating the surface).
 *
 * **The viewport is set explicitly.** The copilot aside is `hidden … xl:flex` —
 * below 1280px the right pane is mounted but not painted, so every assertion
 * about the diary would fail for a reason that has nothing to do with the
 * story. Playwright's Desktop Chrome default is exactly 1280 wide, which is the
 * breakpoint itself; stating a width above it makes the dependency visible
 * rather than lucky. One test deliberately goes *under* it, because "mounted
 * but not painted" is not the same as "not there" and the difference here is a
 * deep link focusing an input nobody can see.
 *
 * **These tests share one database.** The AD-15 fixture resets per spec *file*,
 * and `diary_note` starts empty (there is no seed, deliberately), so each test
 * below reads current state before it writes rather than assuming a count.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

interface DiaryNote {
  id: number;
  claimId: string | null;
  noteText: string;
  notedAt: string;
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

interface DiaryNoteList {
  items: DiaryNote[];
  nextCursor: string | null;
  total: number;
}

interface Meeting {
  id: number;
  claimId: string | null;
  meetingDate: string;
  meetingTime: string | null;
  isDone: boolean;
  status: "upcoming" | "done";
}

interface MeetingList {
  items: Meeting[];
  nextCursor: string | null;
  total: number;
  upcomingCount: number;
}

interface ChecklistRow {
  id: string;
  target: string;
  enabled: boolean;
  disabledReason: string | null;
  command: string | null;
}

async function notesOf(page: Page): Promise<DiaryNoteList> {
  const response = await page.request.get("/api/claims-diary/notes");
  expect(response.status(), await response.text()).toBe(200);
  return (await response.json()) as DiaryNoteList;
}

async function checklistFor(page: Page, claimId: string): Promise<ChecklistRow[]> {
  const response = await page.request.get(`/api/claims/${claimId}/actions`);
  expect(response.status(), await response.text()).toBe(200);
  return ((await response.json()) as { items: ChecklistRow[] }).items;
}

/** Open the workspace on a claim with the copilot pane on screen. */
async function openWorkspace(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "copilot")).toBeVisible();
}

/** One checklist row, addressed by the action it carries — never by index. */
function actionRow(page: Page, actionId: string) {
  return page.locator(`[data-testid="action-row"][data-action="${actionId}"]`);
}

/**
 * The caller's whole meeting book — **asserted to fit one page**.
 *
 * `upcomingCount` is a count over every meeting the caller has, and `items` is
 * one page of fifty. Comparing the two without checking that the page is the
 * whole book makes the oracle silently wrong the moment a seeded diary grows
 * past the page size — the pytest twin
 * (`test_the_two_renderings_of_the_upcoming_rule_agree`) asserts exactly this
 * and this spec did not. Bounded here, once, so every caller inherits it.
 */
async function diaryOf(page: Page): Promise<MeetingList> {
  // **`asOf` is the whole point of the oracle.** This used to fetch the
  // unfiltered list with no clock, so `upcomingCount` came back judged at the
  // server's UTC date — and it was then compared against a number on screen
  // that the greeting had asked for at the *browser's* local day. The two agree
  // for most of the day and disagree exactly when the timezone skew the
  // high-severity patch closed is real, so the oracle assumed away the thing it
  // was checking. Both sides now name the same day.
  const response = await page.request.get(
    `/api/claims-diary/meetings?asOf=${todayIso()}&limit=200`,
  );
  expect(response.status(), await response.text()).toBe(200);
  const list = (await response.json()) as MeetingList;
  expect(list.nextCursor, "the diary outgrew one page; walk the cursor").toBeNull();
  expect(list.items).toHaveLength(list.total);
  return list;
}

/** One meeting card, addressed by the server's id — never by index. */
function meetingCard(page: Page, meetingId: number) {
  return page.locator(`[data-testid="meeting-card"][data-meeting-id="${meetingId}"]`);
}

/** One note card, addressed by the server's id — never by index. */
function noteCard(page: Page, noteId: number) {
  return page.locator(`[data-testid="diary-note"][data-note-id="${noteId}"]`);
}

/**
 * A claim in Kaya's book whose checklist really carries the diary check-in.
 *
 * The *candidates* come from the seed (the rule fires on treatment claims);
 * which action a claim actually carries comes from the server, because the SPA
 * has no membership test to consult and neither does this spec. Shared by every
 * test that clicks the deep link, so they cannot disagree about which claim is
 * a valid one to click it on.
 */
async function diaryActionTarget(page: Page): Promise<{ claimId: string; actionId: string }> {
  for (const claimId of claimIdsInStage(KAYA.name, KAYA.role, "treatment")) {
    const found = (await checklistFor(page, claimId)).find((item) => item.target === "diary");
    if (found) return { claimId, actionId: found.id };
  }
  throw new Error("no seeded claim in this book emits a diary check-in");
}

test.use({ viewport: { width: 1440, height: 900 } });

test.describe("@story:4-2 @epic:4 claim-linked diary notes", () => {
  test("@smoke a handler writes a note, sees it top-of-list, and it survives a reload", async ({
    page,
  }) => {
    // The prototype silently ignores an empty input and the console must refuse
    // it inline instead (NFR-3, UX-DR11). A listener that fails is a stronger
    // statement than an assertion about a message being visible.
    page.on("dialog", (dialog) => {
      throw new Error(`a native dialog appeared: ${dialog.message()}`);
    });

    await loginAs(page, PERSONAS.handler);

    // --- AC 3: current state, read rather than assumed -------------------
    // The file header promises every test here reads what is there before it
    // writes; this one used to assert `total === 0` instead, which is true only
    // because it happens to run first and would fail on a shard, a `--grep` or a
    // re-order with a reason that named nothing.
    const before = await notesOf(page);

    const claimId = claimIdsInStage(KAYA.name, KAYA.role, "treatment")[0];
    await openWorkspace(page, claimId);

    // Notes is the sub-tab the pane opens on.
    await expect(byTestId(page, "diary-subtab-notes")).toHaveAttribute("aria-selected", "true");
    await expect(byTestId(page, "notes-subtab")).toBeVisible();

    // --- AC 1: the greeting card ----------------------------------------
    await expect(byTestId(page, "diary-greeting-line")).toContainText("Kaya 👋");
    await expect(byTestId(page, "diary-today")).not.toBeEmpty();
    await expect(byTestId(page, "diary-active-claim")).toContainText(claimId);
    await expect(byTestId(page, "notes-subtab")).toContainText(
      "Log notes below · Schedule meetings · Email stakeholders via tabs above.",
    );

    // --- AC 3: the empty state, in the prototype's own words -------------
    // Conditional on the state just read, not on the run order. `diary_note`
    // has no seed, so on a whole-file run this branch is the one taken.
    if (before.total === 0) {
      await expect(byTestId(page, "notes-empty")).toHaveText("No notes yet.");
    } else {
      await expect(byTestId(page, "diary-note")).toHaveCount(before.total);
    }

    // --- AC 2: the form says which claim the note will be tagged to ------
    // It said nothing before, on a write into a table with no edit and no
    // delete.
    await expect(byTestId(page, "note-claim-tag")).toHaveAttribute("data-claim-id", claimId);

    // --- AC 3: an empty note is refused inline and writes nothing --------
    await byTestId(page, "note-save").click();
    await expect(byTestId(page, "note-error")).toBeVisible();
    expect((await notesOf(page)).total).toBe(before.total);

    // --- AC 2: a valid note persists and appears at the top --------------
    await byTestId(page, "note-input").fill("Called the plant; light duty from Monday.");
    await byTestId(page, "note-save").click();

    await expect(byTestId(page, "diary-note")).toHaveCount(before.total + 1);
    // The input clears; the refusal does not linger.
    await expect(byTestId(page, "note-input")).toHaveValue("");
    await expect(byTestId(page, "note-error")).toHaveCount(0);

    const written = await notesOf(page);
    expect(written.total).toBe(before.total + 1);
    // Element 0 is the *server's* newest, which this test then checks is the
    // note it just wrote — an identity check on the ordering contract rather
    // than a positional shortcut past it.
    const note = written.items[0];
    expect(note.noteText).toBe("Called the plant; light duty from Monday.");
    // AC 2: tagged to the claim the workspace had selected.
    expect(note.claimId).toBe(claimId);

    const card = noteCard(page, note.id);
    await expect(card.getByTestId("diary-note-tag")).toHaveText(`📎 ${claimId}`);
    await expect(card.getByTestId("diary-note-when")).not.toBeEmpty();

    // --- AC 3: a second note lands above the first ----------------------
    await byTestId(page, "note-input").fill("Second entry, later in the day.");
    await byTestId(page, "note-save").click();
    await expect(byTestId(page, "diary-note")).toHaveCount(before.total + 2);

    const both = await notesOf(page);
    // Newest first is the *server's* order, so it is asserted against the
    // payload and then against what is on screen — the SPA renders the list it
    // was sent and sorts nothing.
    expect(both.items[0].noteText).toBe("Second entry, later in the day.");
    await expect(byTestId(page, "diary-note").first()).toContainText(
      "Second entry, later in the day.",
    );

    // --- The claim of the whole story: it survives a reload -------------
    await page.reload();
    await expect(byTestId(page, "diary-note")).toHaveCount(before.total + 2);
    await expect(noteCard(page, note.id)).toBeVisible();
    await expect(noteCard(page, note.id)).toContainText(
      "Called the plant; light duty from Monday.",
    );
  });

  test("the diary deep link opens Notes with the input focused (AC 4, Story 3.5's seam)", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);
    const target = await diaryActionTarget(page);

    // Enabled because Story 4.2 removed the target from the server's seam
    // table — one deletion there, plus `"diary"` in the card's
    // `NAVIGABLE_FROM_OVERVIEW` without which the row would render no control
    // at all.
    const row = (await checklistFor(page, target.claimId)).find(
      (item) => item.id === target.actionId,
    )!;
    expect(row.enabled).toBe(true);
    expect(row.disabledReason).toBeNull();
    // The note *is* the completion — no fifth `ActionCommand`, no ✓ on the row.
    expect(row.command).toBeNull();

    await openWorkspace(page, target.claimId);
    // Start from Meetings, so the deep link has to do both halves of its job.
    await byTestId(page, "diary-subtab-meetings").click();
    await expect(byTestId(page, "meetings-subtab")).toBeVisible();

    const goTo = actionRow(page, target.actionId).getByTestId("action-goto");
    await expect(goTo).toBeEnabled();
    await goTo.click();

    await expect(byTestId(page, "diary-subtab-notes")).toHaveAttribute("aria-selected", "true");
    // The point of the story's AC 4: the caret is *in the input*, which is what
    // the `noteFocusSession` key buys and what no unit test at jsdom's default
    // width can meaningfully check.
    await expect(byTestId(page, "note-input")).toBeFocused();

    // And a second click works too — the counter advances, the input remounts,
    // and focus returns. A boolean intent flag would already be set and do
    // nothing.
    await byTestId(page, "diary-subtab-meetings").click();
    await actionRow(page, target.actionId).getByTestId("action-goto").click();
    await expect(byTestId(page, "note-input")).toBeFocused();
  });

  test("a note closes the diary check-in, and the checklist says so (AC 5)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);
    const target = await diaryActionTarget(page);

    await openWorkspace(page, target.claimId);
    await expect(actionRow(page, target.actionId)).toBeVisible();

    // Read the current count rather than assuming one: the AD-15 fixture
    // resets per spec *file*, so earlier tests here have already written notes.
    const before = (await notesOf(page)).total;
    await byTestId(page, "note-input").fill("Weekly check-in: spoke to the NCM.");
    await byTestId(page, "note-save").click();
    await expect(byTestId(page, "diary-note")).toHaveCount(before + 1);

    // The completion is entity-backed: nothing was toggled, a row was written,
    // and the rule stopped firing. Asserted against the server first, because
    // that is where the rule lives…
    const after = await checklistFor(page, target.claimId);
    expect(after.some((item) => item.target === "diary")).toBe(false);

    // …and then **on screen, with no reload**, which is the half this test used
    // to hide. The checklist is its own query key and `refetchOnWindowFocus` is
    // off, so nothing asks the server again unless the note's mutation
    // invalidates that key — and it did not. A `page.reload()` here passed
    // against a console where the handler watched the row they had just
    // satisfied sit there, and wrote the note a second time.
    await expect(actionRow(page, target.actionId)).toHaveCount(0);

    // And it stays gone across a reload, which is the ordinary persistence
    // claim rather than the invalidation one.
    await page.reload();
    await expect(byTestId(page, "actions-card")).toBeVisible();
    await expect(actionRow(page, target.actionId)).toHaveCount(0);
  });

  test("the greeting's upcoming count is the server's, not the summary's length", async ({
    page,
  }) => {
    // `upcomingCount` is over the whole book and the summary is one filtered
    // day of it, so on seeded data the two genuinely differ: migration 0033
    // dates its two demo meetings to the migration-run date, which may or may
    // not be today, while both are upcoming for as long as they are not ticked.
    await loginAs(page, PERSONAS.handler);
    // `diaryOf` asserts the book fits one page, which is what makes the next
    // line an oracle at all: `upcomingCount` is over every meeting the caller
    // has, and counting one *page* of them would agree by luck until a seeded
    // diary outgrew fifty rows. The pytest twin bounds it the same way.
    const book = await diaryOf(page);
    // The oracle is the payload's own rows judged by the server's `status`,
    // never a date comparison written here — this spec must not become the
    // second copy of the horizon rule either.
    expect(book.upcomingCount).toBe(book.items.filter((item) => item.status === "upcoming").length);

    const claimId = claimIdsInStage(KAYA.name, KAYA.role, "treatment")[0];
    await openWorkspace(page, claimId);

    if (book.upcomingCount === 0) {
      await expect(byTestId(page, "diary-upcoming-count")).toHaveCount(0);
    } else {
      await expect(byTestId(page, "diary-upcoming-count")).toContainText(
        `📅 ${book.upcomingCount} upcoming meeting`,
      );
    }
  });

  test("today's meetings drive ✓ Done and Open Claim across panes (AC 1)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = claimIdsInStage(KAYA.name, KAYA.role, "treatment")[0];
    await openWorkspace(page, claimId);

    // Schedule one for *today* through the shipped modal, so the summary has a
    // row without this spec inventing a date the server would disagree about:
    // the scheduler's default date is the viewer's own today, which is exactly
    // the day the Notes sub-tab asks the server for.
    await byTestId(page, "diary-subtab-meetings").click();
    await byTestId(page, "meeting-schedule-open").click();
    await expect(byTestId(page, "meeting-scheduler")).toBeVisible();
    await byTestId(page, "scheduler-type").selectOption("ncm_care_coordination");
    await byTestId(page, "scheduler-time").fill("08:15");
    await byTestId(page, "scheduler-save").click();
    await expect(byTestId(page, "meeting-scheduler")).toBeHidden();

    // **Addressed by the server's id, never by `.first()`.** The summary
    // re-sorts as its own query settles — the new 08:15 meeting lands *ahead*
    // of the seeded ones, which is the time-ordering Story 4.2 added — so a
    // positional locator read before that refetch names one card and clicks
    // another.
    const scheduled = (await diaryOf(page)).items.find(
      (item) => item.meetingTime === "08:15:00",
    );
    expect(scheduled, "the meeting scheduled for today was not written").toBeDefined();
    const card = meetingCard(page, scheduled!.id);

    await byTestId(page, "diary-subtab-notes").click();
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute("data-variant", "compact");
    await expect(byTestId(page, "diary-today-heading")).toContainText("Today's Meetings");
    // The compact variant: ✓ Done and Open Claim, no Delete, no 4.3 seam.
    await expect(card.getByTestId("meeting-delete")).toHaveCount(0);
    await expect(card.getByTestId("meeting-email")).toHaveCount(0);

    // The summary reads down the clock, and the ordering it reads is the
    // **server's** — Story 4.2 made the sort key `(date, COALESCE(time,'00:00'),
    // id)` globally so one query could serve both the whole diary and a single
    // day. Asserted as "these ids, in the order the server puts them in" rather
    // than as `shown[0] === the new one`: a positional assertion is a claim
    // about the seed as much as about the sort, and migration 0033 dates its
    // demo meetings to the migration-run date, which may or may not be today.
    const shown = await page
      .locator('[data-testid="meeting-card"][data-variant="compact"]')
      .evaluateAll((cards) => cards.map((node) => node.getAttribute("data-meeting-id")));
    expect(shown).toContain(String(scheduled!.id));
    const serverOrder = (await diaryOf(page)).items
      .map((item) => String(item.id))
      .filter((id) => shown.includes(id));
    expect(shown).toEqual(serverOrder);

    // --- ✓ Done from the summary persists, and both surfaces agree -------
    await card.getByTestId("meeting-done").click();
    await expect(card).toHaveAttribute("data-status", "done");

    await byTestId(page, "diary-subtab-meetings").click();
    // The same row in the *other* sub-tab, which is what the prefix
    // invalidation buys: two cache entries over the same meetings, and a ✓ in
    // one must not leave the other showing it as still ahead.
    await expect(meetingCard(page, scheduled!.id)).toHaveAttribute("data-status", "done");

    const persisted = (await diaryOf(page)).items.find((item) => item.id === scheduled!.id);
    expect(persisted!.isDone).toBe(true);

    // --- Open Claim drives the queue selection and the case file ---------
    // **From a different claim**, which this test used to skip. It opened the
    // workspace on `claimId`, scheduled the meeting against `claimId`, and then
    // clicked Open Claim on that same claim — and `useSelectClaim` early-returns
    // when the id it is handed is already selected, so both assertions below
    // were true *before* the click. The navigation was never exercised.
    const other = claimIdsInStage(KAYA.name, KAYA.role, "treatment").find((id) => id !== claimId);
    expect(other, "the seed should give this handler a second treatment claim").toBeDefined();

    await openWorkspace(page, other!);
    await expect(page).toHaveURL(new RegExp(`claim=${other!}`));
    await byTestId(page, "diary-subtab-notes").click();

    // The summary is the caller's whole day and not the selected claim's, so
    // the card is still here — pointing at the claim the workspace has left.
    const open = meetingCard(page, scheduled!.id).getByTestId("meeting-open-claim");
    expect(await open.getAttribute("data-claim-id")).toBe(claimId);
    await open.click();

    await expect(page).toHaveURL(new RegExp(`claim=${claimId}`));
    await expect(byTestId(page, "copilot-context")).toContainText(claimId);
  });

  test("a handler's notes are their own (AD-7)", async ({ page }) => {
    // The author half of the scope predicate, which `employer_scope` alone
    // would not give: the seed puts two handlers on John Deere. Asserted across
    // a persona switch rather than in pytest only, because the session cookie
    // is what decides and this is where a session exists.
    await loginAs(page, PERSONAS.handler);
    const claimId = claimIdsInStage(KAYA.name, KAYA.role, "treatment")[0];
    await openWorkspace(page, claimId);
    // Read the current count rather than assuming one: the AD-15 fixture
    // resets per spec *file*, so earlier tests here have already written notes.
    const existing = (await notesOf(page)).total;
    await byTestId(page, "note-input").fill("Kaya's own working note.");
    await byTestId(page, "note-save").click();
    await expect(byTestId(page, "diary-note")).toHaveCount(existing + 1);

    const kaya = await notesOf(page);
    expect(kaya.total).toBeGreaterThan(0);

    await switchPersona(page);
    await loginAs(page, PERSONAS.scopedHandler);
    const sarah = await notesOf(page);

    expect(sarah.total).toBe(0);
    const kayaIds = new Set(kaya.items.map((item) => item.id));
    expect(sarah.items.some((item) => kayaIds.has(item.id))).toBe(false);
  });

  test("a supervisor cannot write a note, and sees nobody's diary", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);

    const listing = await page.request.get("/api/claims-diary/notes");
    expect(listing.status()).toBe(200);
    // Scoped to the *caller*: a supervisor writes no notes, so the list is
    // empty rather than their handlers'.
    expect(((await listing.json()) as DiaryNoteList).total).toBe(0);

    const refused = await page.request.post("/api/claims-diary/notes", {
      data: { noteText: "A supervisor's note." },
    });
    expect(refused.status()).toBe(403);
    expect((await refused.json()).type).toBe("/problems/edit-not-permitted");
  });

  test("below the xl breakpoint the deep link focuses an input nobody can see", async ({
    page,
  }) => {
    // The same rough edge Story 4.1 pinned for the scheduler, in the shape 4.2
    // gives it. The copilot aside is `hidden … xl:flex` — `display: none`, but
    // still *mounted* — so the deep link really does switch the sub-tab and
    // really does mount the input. The difference from the scheduler is that a
    // dialog portals to `document.body` and an input does not, so here the
    // focus lands somewhere invisible rather than over the workspace.
    //
    // Pinned rather than assumed away, because the honest fix is a layout
    // decision this story is not allowed to take (the pane has been `xl`-only
    // since Story 2.1) and the next reader should not have to guess.
    await page.setViewportSize({ width: 1024, height: 800 });
    await loginAs(page, PERSONAS.handler);
    const target = await diaryActionTarget(page);

    await page.goto(`/workspace?claim=${target.claimId}`);
    await expect(byTestId(page, "copilot-pane")).toBeAttached();
    await expect(byTestId(page, "copilot-pane")).toBeHidden();

    await actionRow(page, target.actionId).getByTestId("action-goto").click();

    await expect(byTestId(page, "note-input")).toBeAttached();
    await expect(byTestId(page, "note-input")).toBeHidden();

    // Widen, and the input the handler could not see is simply there.
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(byTestId(page, "note-input")).toBeVisible();
  });
});
