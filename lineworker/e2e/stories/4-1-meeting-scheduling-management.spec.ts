import { PERSONAS, loginAs, switchPersona } from "../fixtures/login";
import {
  MEETING_TYPE_LABEL,
  claimIdsOnModifiedDuty,
  expectedMeetingsFor,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 4.1 — Meeting Scheduling & Management.
 *
 * Epic 4's first surface, and the first time the right-hand pane holds
 * anything. Four things are only true in a browser and are what this spec is
 * organised around.
 *
 * 1. **The pane exists, and one of its two tabs says why it is empty.** The
 *    copilot shell ships with ⚡ Actions disabled until Epic 6 and 📓 Diary
 *    live; the Notes and Emails sub-tabs name Stories 4.2 and 4.3. A disabled
 *    control is not a missing feature here — it is the cross-story seam, and
 *    the point is that a handler can see the work exists (NFR-3, no dead
 *    clicks).
 *
 * 2. **A meeting survives a reload.** The prototype's `meetingsStore` is a
 *    browser-lifetime object re-seeded at every login; the whole of this story
 *    is that a row is written instead. Only a real navigation can show that,
 *    and it is the one assertion no unit test can make.
 *
 * 3. **The date refusal is inline, not a native dialog.** The prototype calls
 *    `alert('Please select a date.')`. A `page.on("dialog")` listener here
 *    fails the test if one ever appears, which is a stronger statement than
 *    "an error message is visible".
 *
 * 4. **Story 3.5's meetings deep link now goes somewhere.** The action lives
 *    in the centre pane and the scheduler it opens lives in the right one, so
 *    this is the only place both ends are on screen at once.
 *
 * **What this spec deliberately does not assert.** The audit rows, the
 * compare-and-swap round trip and the scope refusals: `server/tests/
 * test_meetings.py` covers all three against the same database this stack
 * runs, and nothing in the product reads `audit_event` (Story 2.3's ruling —
 * inventing an endpoint so a spec could use one would be the test dictating
 * the surface). The seed's exact dates are not asserted either: migration
 * 0033 stamps its own run date, so an oracle naming a day would be wrong the
 * morning after the image was built.
 *
 * **The viewport is set explicitly.** The copilot aside is `hidden … xl:flex`
 * — below 1280px the right pane is mounted but not painted, so every
 * assertion about a card here would fail for a reason that has nothing to do
 * with the story. Playwright's Desktop Chrome default is exactly 1280 wide,
 * which is the breakpoint itself; stating a width above it makes the
 * dependency visible rather than lucky. One test deliberately goes *under* it,
 * because "mounted but not painted" is not the same as "not there" and the
 * difference is the scheduler still opening, portalled to `document.body`,
 * over a diary nobody can see.
 *
 * **These tests share one database.** The AD-15 fixture resets per spec
 * *file*, so each test below reads current state before it writes, as a client
 * does — and the delete test creates its own meeting rather than removing a
 * seeded one another test is asserting.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };
const SARAH = { name: "Sarah Williams", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

interface Meeting {
  id: number;
  claimId: string | null;
  workerName: string | null;
  meetingType: string;
  meetingDate: string;
  meetingTime: string | null;
  isDone: boolean;
  version: number;
  status: "upcoming" | "done";
}

/** A stable comparison for the `(claim, type)` pairs the seed oracle names. */
function byPair(
  left: { claimId: string | null; meetingType: string },
  right: { claimId: string | null; meetingType: string },
): number {
  return `${left.claimId}|${left.meetingType}`.localeCompare(
    `${right.claimId}|${right.meetingType}`,
  );
}

/**
 * The server's ordering rule, restated: `(meetingDate, COALESCE(time,'00:00'), id)`.
 *
 * The order assertion used to be "the list equals the seed fixture's array",
 * which is the migration's *insertion* order — and it agreed only because both
 * demo rows carry the same date and happen to have been inserted down the
 * clock. Restating the rule is what makes the assertion about the sort.
 */
function byServerOrder(left: Meeting, right: Meeting): number {
  const key = (meeting: Meeting) =>
    `${meeting.meetingDate}|${meeting.meetingTime ?? "00:00:00"}|${String(meeting.id).padStart(12, "0")}`;
  return key(left).localeCompare(key(right));
}

interface MeetingList {
  items: Meeting[];
  nextCursor: string | null;
  total: number;
  upcomingCount: number;
}

/**
 * The viewer's local calendar day, as `web/src/lib/clock.ts::todayIso` builds it.
 *
 * Restated here rather than imported: a spec that used the app's own helper
 * would be asserting that the helper equals itself. Playwright runs on the same
 * host as the browser, so "local" means the same thing on both sides.
 */
function todayIso(now = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * The caller's whole diary, **judged at the same clock the DOM was judged at**.
 *
 * `asOf` is not decoration here. The Meetings sub-tab sends the viewer's local
 * day since the follow-up review, so a fetch that omitted it would come back
 * judged at the server's UTC date and every `status` in it could disagree with
 * the card beside it — which is the very skew the parameter exists to close,
 * reintroduced by the oracle rather than by the code.
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

/**
 * Upcoming or done, **restated** — the spec's own copy of the horizon rule.
 *
 * The status assertions used to compare the DOM against the `status` field of
 * the response the test had just fetched, which is the same server answering
 * twice: flipping `>=` to `>` in `meeting_horizon.py` moved both sides together
 * and the whole spec passed. An e2e spec is allowed to restate a rule in order
 * to disagree with it — that is what an oracle is — and this is the one place
 * the restatement is worth having, because the seeded meetings are dated the
 * migration-run day and are therefore *on* the boundary the `>=` decides.
 */
function expectedStatus(meeting: Meeting, day: string): "upcoming" | "done" {
  if (meeting.isDone) return "done";
  return meeting.meetingDate >= day ? "upcoming" : "done";
}

/** Open the workspace on a claim with the copilot pane on screen. */
async function openWorkspace(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await expect(byTestId(page, "copilot")).toBeVisible();
}

/** One card, addressed by the server's id — never by index (Story 3.5). */
function cardFor(page: Page, meetingId: number) {
  return page.locator(`[data-testid="meeting-card"][data-meeting-id="${meetingId}"]`);
}

/** One checklist row, addressed by the action it carries — never by index. */
function actionRow(page: Page, actionId: string) {
  return page.locator(`[data-testid="action-row"][data-action="${actionId}"]`);
}

/**
 * A claim in Kaya's book whose checklist really carries a `meetings` action.
 *
 * The *candidate* claims come from the seed (`modified_duty` fires on a worker
 * back under therapy); which action a claim actually carries comes from the
 * server, because the SPA has no membership test to consult and neither does
 * this spec. Shared by the two tests that click the deep link, so they cannot
 * disagree about which claim is a valid one to click it on.
 */
async function meetingsActionTarget(page: Page): Promise<{ claimId: string; actionId: string }> {
  for (const claimId of claimIdsOnModifiedDuty(KAYA.name, KAYA.role)) {
    const response = await page.request.get(`/api/claims/${claimId}/actions`);
    const checklist = (await response.json()) as { items: { id: string; target: string }[] };
    const found = checklist.items.find((item) => item.target === "meetings");
    if (found) return { claimId, actionId: found.id };
  }
  throw new Error("no seeded claim in this book emits a meetings action");
}

test.use({ viewport: { width: 1440, height: 900 } });

test.describe("@story:4-1 @epic:4 meeting scheduling and management", () => {
  test("@smoke a handler schedules, completes and deletes a meeting", async ({ page }) => {
    // The prototype's `alert('Please select a date.')` must not survive — and
    // a listener that fails is a stronger statement than an assertion about a
    // message being visible (NFR-3, UX-DR11).
    page.on("dialog", (dialog) => {
      throw new Error(`a native dialog appeared: ${dialog.message()}`);
    });

    await loginAs(page, PERSONAS.handler);

    const seeded = await diaryOf(page);
    const expected = expectedMeetingsFor(KAYA.name, KAYA.role);
    const claimId = expected[0].claimId;

    // --- AC 6: the two seeded demo meetings, and only those --------------
    // Compared as a *set* of `(claim, type)` pairs plus an explicit ordering
    // assertion below, rather than as a list in the seed fixture's order: that
    // order is the migration's insertion order, and the two happen to agree only
    // because both rows carry the same date and the fixture happens to list them
    // by time. A change to the sort key would have gone unnoticed.
    expect(seeded.total).toBe(expected.length);
    const seededPairs = seeded.items.map((item) => ({
      claimId: item.claimId,
      meetingType: item.meetingType,
    }));
    expect([...seededPairs].sort(byPair)).toEqual([...expected].sort(byPair));
    // The server's ordering rule, restated: `(meetingDate, time or midnight, id)`.
    expect(seeded.items.map((item) => item.id)).toEqual(
      [...seeded.items].sort(byServerOrder).map((item) => item.id),
    );

    await openWorkspace(page, claimId);

    // --- AC 1: the shell, its disabled tab and its sub-tab seams ---------
    const actionsTab = byTestId(page, "copilot-tab-actions");
    await expect(actionsTab).toBeDisabled();
    await expect(actionsTab).toHaveAttribute("title", /Epic 6/);
    await expect(byTestId(page, "copilot-tab-diary")).toHaveAttribute("aria-selected", "true");
    // Notes is the sub-tab the pane opens on since Story 4.2 built it — it is
    // first in the strip and carries the greeting.
    await expect(byTestId(page, "diary-subtab-notes")).toHaveAttribute("aria-selected", "true");

    // Notes was a placeholder naming Story 4.2 when this spec was written; 4.2
    // built it, so what is left to assert here is that the strip still has
    // three tabs and that the *unbuilt* one still names its story. The Notes
    // sub-tab's own behaviour is `4-2-claim-linked-diary-notes.spec.ts`'s.
    await byTestId(page, "diary-subtab-notes").click();
    await expect(byTestId(page, "notes-subtab")).toBeVisible();
    await byTestId(page, "diary-subtab-emails").click();
    await expect(byTestId(page, "diary-empty-emails")).toHaveAttribute("data-story", "Story 4.3");
    await byTestId(page, "diary-subtab-meetings").click();

    // The list renders the seeded rows, in the server's order — and each one's
    // status is checked against the rule *restated here*, not against the field
    // the same request just delivered. Migration 0033 dates its demo meetings to
    // the migration-run day, so these two rows sit exactly on the boundary the
    // `>=` decides: flipping it to `>` renders both as done, which this notices
    // and a self-comparison did not.
    const today = todayIso();
    await expect(byTestId(page, "meeting-card")).toHaveCount(seeded.total);
    for (const item of seeded.items) {
      await expect(cardFor(page, item.id)).toHaveAttribute(
        "data-status",
        expectedStatus(item, today),
      );
      expect(item.status).toBe(expectedStatus(item, today));
    }

    // --- AC 5: the email control is disabled and names Story 4.3 ---------
    const email = cardFor(page, seeded.items[0].id).getByTestId("meeting-email");
    await expect(email).toBeDisabled();
    await expect(email).toHaveAttribute("title", /Story 4\.3/);

    // --- AC 2: a missing date is refused inline, not by a dialog ---------
    await byTestId(page, "meeting-schedule-open").click();
    await expect(byTestId(page, "meeting-scheduler")).toBeVisible();
    // `\S` at the end, not a bare trailing space: the field is
    // `WC-nnnn — Worker Name`, and the worker's name arrives on a *second*
    // request. A pattern that stopped at the em dash passed just as happily
    // against `WC-20017 — ` with nothing after it, which is the truncated
    // field this assertion exists to notice.
    await expect(byTestId(page, "scheduler-claim")).toHaveValue(
      new RegExp(`^${claimId} — \\S`),
    );

    await byTestId(page, "scheduler-date").fill("");
    await byTestId(page, "scheduler-save").click();
    await expect(byTestId(page, "scheduler-error")).toBeVisible();
    // Nothing was written: the list is still the two seeded rows.
    expect((await diaryOf(page)).total).toBe(seeded.total);

    // --- AC 1, AC 3: a valid save persists and the list re-renders -------
    await byTestId(page, "scheduler-date").fill("2099-12-24");
    await byTestId(page, "scheduler-time").fill("09:15");
    await byTestId(page, "scheduler-type").selectOption("ime_preparation");
    await byTestId(page, "scheduler-location").fill("Adjuster office");
    await byTestId(page, "scheduler-notes").fill("Prepare the IME bundle.");
    await byTestId(page, "scheduler-save").click();

    await expect(byTestId(page, "meeting-scheduler")).toBeHidden();
    await expect(byTestId(page, "meeting-card")).toHaveCount(seeded.total + 1);

    const withNew = await diaryOf(page);
    const created = withNew.items.find((item) => item.meetingType === "ime_preparation");
    expect(created, "the scheduled meeting is not in the diary").toBeDefined();
    expect(created!.claimId).toBe(claimId);
    // Server-derived, and the whole reason `status` is on the wire: a 2099
    // meeting nobody has ticked is upcoming.
    expect(created!.status).toBe("upcoming");

    const newCard = cardFor(page, created!.id);
    await expect(newCard.getByTestId("meeting-title")).toContainText(
      MEETING_TYPE_LABEL.ime_preparation,
    );
    await expect(newCard.getByTestId("meeting-when")).toContainText("Adjuster office");
    await expect(newCard.getByTestId("meeting-claim-ref")).toContainText(claimId);

    // --- The claim of the whole story: it survives a reload --------------
    await page.reload();
    // The pane comes back on **Notes** — Story 4.2 built it, and it is the
    // first tab in the strip and the one carrying the greeting. Sub-tab
    // selection is local UI state and deliberately not in the URL (only
    // `?claim=` is), so a reload returns to the default rather than to
    // wherever the handler was.
    await byTestId(page, "diary-subtab-meetings").click();
    await expect(cardFor(page, created!.id)).toBeVisible();

    // --- AC 4: ✓ Done persists, and the status the server derives moves --
    await cardFor(page, created!.id).getByTestId("meeting-done").click();
    await expect(cardFor(page, created!.id)).toHaveAttribute("data-status", "done");
    await expect(cardFor(page, created!.id).getByTestId("meeting-done")).toHaveCount(0);

    const afterDone = await diaryOf(page);
    const done = afterDone.items.find((item) => item.id === created!.id);
    expect(done!.isDone).toBe(true);
    expect(done!.version).toBe(created!.version + 1);

    // --- AC 4: Delete persists ------------------------------------------
    await cardFor(page, created!.id).getByTestId("meeting-delete").click();
    await expect(cardFor(page, created!.id)).toHaveCount(0);

    const afterDelete = await diaryOf(page);
    expect(afterDelete.total).toBe(seeded.total);
    expect(afterDelete.items.some((item) => item.id === created!.id)).toBe(false);
  });

  test("the meetings action opens the scheduler across panes (AC 5, Story 3.5's seam)", async ({
    page,
  }) => {
    // The checklist is in the centre pane and the scheduler is in the right
    // one, which is why this is a browser test at all. The *candidate* claims
    // come from the seed (`modified_duty` fires on a worker back under
    // therapy); which action a claim actually carries comes from the server,
    // because the SPA has no membership test to consult and neither does this
    // spec.
    await loginAs(page, PERSONAS.handler);
    const target = await meetingsActionTarget(page);

    await openWorkspace(page, target.claimId);

    const goTo = actionRow(page, target.actionId).getByTestId("action-goto");
    // Enabled because Story 4.1 removed the target from the server's seam
    // table — the SPA changed not at all, which is the property
    // `ActionTarget`'s docstring promises.
    await expect(goTo).toBeEnabled();
    await goTo.click();

    await expect(byTestId(page, "diary-subtab-meetings")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(byTestId(page, "meeting-scheduler")).toBeVisible();
    await expect(byTestId(page, "scheduler-claim")).toHaveValue(
      new RegExp(`^${target.claimId} — \\S`),
    );
  });

  test("below the xl breakpoint the diary is invisible but the scheduler still opens", async ({
    page,
  }) => {
    // **Pinning what actually happens, because two comments used to claim the
    // opposite.** The copilot aside is `hidden … xl:flex` — `display: none`,
    // but still *mounted* — the deep link switches it to Meetings, and the
    // Radix dialog portals to `document.body`. So the deep link below 1280px
    // is not the no-op it was documented as: the handler gets a working modal
    // over a workspace whose diary list they cannot see, and a meeting saved
    // from it is written correctly and stays invisible until the window
    // widens. Only a browser can tell that from "nothing happens", and the
    // difference matters — the honest fix is a layout decision this story is
    // not allowed to take (the pane has been `xl`-only since Story 2.1), so
    // what this test buys is that the next reader is not misled about it.
    await page.setViewportSize({ width: 1024, height: 800 });
    await loginAs(page, PERSONAS.handler);
    const target = await meetingsActionTarget(page);

    await page.goto(`/workspace?claim=${target.claimId}`);
    // The pane is in the DOM and not on screen — which is the premise.
    await expect(byTestId(page, "copilot-pane")).toBeAttached();
    await expect(byTestId(page, "copilot-pane")).toBeHidden();

    await actionRow(page, target.actionId).getByTestId("action-goto").click();

    await expect(byTestId(page, "meeting-scheduler")).toBeVisible();
    // …over a list that is not.
    await expect(byTestId(page, "meetings-subtab")).toBeHidden();

    // And the save really lands: the row is written even though the list that
    // would show it is invisible. This is the half a reader would most likely
    // guess wrong in the other direction.
    const before = await diaryOf(page);
    await byTestId(page, "scheduler-date").fill("2099-11-05");
    await byTestId(page, "scheduler-type").selectOption("ime_preparation");
    await byTestId(page, "scheduler-save").click();
    await expect(byTestId(page, "meeting-scheduler")).toBeHidden();

    const after = await diaryOf(page);
    expect(after.total).toBe(before.total + 1);
    const created = after.items.find((item) => item.meetingDate === "2099-11-05");
    expect(created, "the meeting scheduled below the breakpoint was not written").toBeDefined();
    expect(created!.claimId).toBe(target.claimId);

    // Widen, and the card the handler could not see is simply there.
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(cardFor(page, created!.id)).toBeVisible();
  });

  test("a handler's diary is their own (AD-7)", async ({ page }) => {
    // The owner half of the scope predicate, which `employer_scope` alone
    // would not give: the seed puts two handlers on John Deere. Asserted
    // across a persona switch rather than in pytest only, because the session
    // cookie is what decides, and this is where a session exists.
    await loginAs(page, PERSONAS.handler);
    const kaya = await diaryOf(page);
    expect(kaya.items).not.toHaveLength(0);

    await switchPersona(page);
    await loginAs(page, PERSONAS.scopedHandler);
    const sarah = await diaryOf(page);

    expect(
      sarah.items.map((item) => ({ claimId: item.claimId, meetingType: item.meetingType })),
    ).toEqual(expectedMeetingsFor(SARAH.name, SARAH.role));
    // No overlap at all: the two diaries share no row id.
    const kayaIds = new Set(kaya.items.map((item) => item.id));
    expect(sarah.items.some((item) => kayaIds.has(item.id))).toBe(false);
  });

  test("a supervisor cannot schedule, and cannot see anybody's diary", async ({ page }) => {
    await loginAs(page, PERSONAS.scopedSupervisor);

    const listing = await page.request.get("/api/claims-diary/meetings");
    expect(listing.status()).toBe(200);
    // Scoped to the *caller*: a supervisor holds no meetings, so the list is
    // empty rather than the whole book's.
    expect(((await listing.json()) as MeetingList).total).toBe(0);

    const refused = await page.request.post("/api/claims-diary/meetings", {
      data: { meetingType: "other", meetingDate: "2099-01-01", participants: [] },
    });
    expect(refused.status()).toBe(403);
    expect((await refused.json()).type).toBe("/problems/edit-not-permitted");
  });
});
