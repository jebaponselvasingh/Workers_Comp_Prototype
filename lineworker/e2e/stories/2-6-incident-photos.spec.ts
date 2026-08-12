import { PERSONAS, loginAs } from "../fixtures/login";
import { psqlQuery } from "../fixtures/reset";
import {
  claimWithMostPhotos,
  expectedPhotosFor,
  firstClaimInStage,
  lastClaimInStage,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 2.6 — Incident Photos.
 *
 * Nothing is stubbed: the browser drives the real SPA against the real API,
 * and the expectations come from `fixtures/seed.ts` reading the same
 * `photos.json` the stack migrated with.
 *
 * **These tests share one database.** The AD-15 fixture resets per spec
 * *file*, and the empty-state test deletes one claim's photos, so it uses a
 * claim no other test in this file opens — Story 2.5 learned this the hard way
 * when a destructive test emptied the claim two other tests read, and the
 * failure surfaced three tests later pointing at the wrong code.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

/** Open a claim's Photos tab the way a handler does. */
async function openPhotos(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await byTestId(page, "tab-photos").click();
  await expect(byTestId(page, "photos-tab")).toBeVisible();
}

test.describe("@story:2-6 @epic:2 incident photos", () => {
  test("@smoke the grid, the tab count and the read-only viewer", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = claimWithMostPhotos(KAYA.name, KAYA.role);
    const photos = expectedPhotosFor(claimId);
    expect(photos.length, "the fixture claim must have more than one photo").toBeGreaterThan(1);

    await page.goto(`/workspace?claim=${claimId}`);

    // --- AC 1: the count is on the label before the tab is opened --------
    await expect(byTestId(page, "tab-photos")).toHaveText(`Photos (${photos.length})`);
    await expect(byTestId(page, "photos-tab")).toHaveCount(0);

    await byTestId(page, "tab-photos").click();
    await expect(byTestId(page, "photos-tab")).toBeVisible();

    // --- AC 1: one card per photo, caption and source, in filing order ---
    const cards = byTestId(page, "photo-card");
    await expect(cards).toHaveCount(photos.length);
    for (const [index, photo] of photos.entries()) {
      await expect(cards.nth(index)).toContainText(photo.caption);
      await expect(cards.nth(index)).toContainText(photo.source);
    }

    // The seeded book has no image files, so every thumbnail is the designed
    // placeholder rather than a broken image.
    await expect(byTestId(page, "photo-thumb").first()).toHaveAttribute("data-state", "absent");
    await expect(byTestId(page, "photos-tab").locator("img")).toHaveCount(0);

    // --- AC 2: the read-only viewer -------------------------------------
    // The **second** card, so that "the viewer shows the photo it was opened
    // by" is a real claim rather than one satisfied by any photo of the claim.
    await cards.nth(1).click();

    const viewer = byTestId(page, "photo-viewer");
    await expect(viewer).toBeVisible();
    await expect(byTestId(page, "photo-viewer-title")).toContainText(photos[1].caption);

    const rows = byTestId(page, "photo-sheet-row");
    await expect(rows).toHaveCount(3);
    await expect(
      page.locator('[data-testid="photo-sheet-row"][data-label="Caption"]'),
    ).toContainText(photos[1].caption);
    await expect(
      page.locator('[data-testid="photo-sheet-row"][data-label="Source"]'),
    ).toContainText(photos[1].source);
    await expect(
      page.locator('[data-testid="photo-sheet-row"][data-label="Claim"]'),
    ).toContainText(claimId);
    // **The three assertions above are the whole of "the viewer shows the
    // photo it was opened by"** — including the negative case, which is why
    // there is no fourth line here (code review, 2026-08-12).
    //
    // There was one, and it asserted nothing: it compared the *Caption* row
    // against `photos[0].source`, and a row whose text is a caption never
    // contains a source string, so it passed whatever the viewer rendered.
    //
    // The obvious repair — compare the *sources* instead — would have been
    // worse than the bug. 29 of the 100 seeded claims have two photographs
    // filed by the same person on the same day, so `photos[0].source ===
    // photos[1].source` verbatim and the assertion fails against a *correct*
    // viewer. (`services/claims/photos.py` says exactly this about the data;
    // the first draft of this spec did not read it.)
    //
    // The second repair — the same negative on the caption — is sound but
    // redundant: captions are unique within every one of the 100 claims, so
    // `toContainText(photos[1].caption)` on line 78 already excludes
    // `photos[0].caption`. Verified rather than assumed: with the viewer
    // broken to render a fixed index, this spec goes red at line 74, and with
    // it broken to render only the wrong *caption row*, at line 80 — the
    // negative never fires first, so it can only ever restate a failure. A
    // guard that cannot fail alone is coverage in appearance only, which is
    // the defect the original line actually had.
    expect(photos[0].caption).not.toBe(photos[1].caption);

    // Read-only: nothing to type into, nothing to submit, nothing to delete.
    await expect(viewer.locator("input, select, textarea")).toHaveCount(0);
    await expect(viewer.getByRole("button")).toHaveCount(1);
    // No native dialog anywhere (NFR-3, UX-DR11).
    await expect(page.locator("[role=alertdialog]")).toHaveCount(0);

    // --- AC 2: closing by backdrop click ---------------------------------
    // The prototype's `#mover` closes this way and Radix does too; asserted
    // here because it is the close a handler reaches for without looking.
    await page.mouse.click(5, 5);
    await expect(viewer).toBeHidden();
    // The grid is still there behind it — the dialog closed, not the tab.
    await expect(cards).toHaveCount(photos.length);
  });

  test("a photographed claim in another stage shows its own grid (AC 1)", async ({ page }) => {
    // The block sits outside the stage-variant union, so a settled claim's
    // evidence is as readable as a treatment claim's. A payload that had put
    // `photos` on a variant would render an empty tab here.
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimInStage(KAYA.name, KAYA.role, "settled");
    const photos = expectedPhotosFor(claimId);
    expect(photos.length).toBeGreaterThan(0);

    await openPhotos(page, claimId);

    await expect(byTestId(page, "tab-photos")).toHaveText(`Photos (${photos.length})`);
    await expect(byTestId(page, "photo-card")).toHaveCount(photos.length);
    await expect(byTestId(page, "photos-empty")).toHaveCount(0);
  });

  test("a claim with no photos shows Photos (0) and its empty state (AC 3, NFR-3)", async ({
    page,
  }) => {
    // Unreachable against the dev seed — every one of the 100 claims carries
    // two to four photos — so the state is produced rather than found. This
    // empties one claim's evidence for the rest of the run, so it takes the
    // **last** settled claim: the first is the one the stage test above reads,
    // and Story 2.5 shipped exactly that collision.
    await loginAs(page, PERSONAS.handler);

    const claimId = lastClaimInStage(KAYA.name, KAYA.role, "settled");
    expect(claimId).not.toBe(firstClaimInStage(KAYA.name, KAYA.role, "settled"));
    expect(claimId).not.toBe(claimWithMostPhotos(KAYA.name, KAYA.role));
    // The premise, asserted before it is destroyed: an empty state that was
    // already empty would prove nothing about the deletion.
    expect(expectedPhotosFor(claimId).length).toBeGreaterThan(0);

    psqlQuery(
      `DELETE FROM photo WHERE claim_id = (SELECT id FROM claim WHERE claim_id = '${claimId}')`,
    );

    await openPhotos(page, claimId);

    await expect(byTestId(page, "tab-photos")).toHaveText("Photos (0)");
    await expect(byTestId(page, "photos-empty")).toContainText("No photos on file");
    await expect(byTestId(page, "photo-card")).toHaveCount(0);

    // The rest of the case file is unaffected — a claim with no photographs is
    // not a claim with no documents, and a tab that emptied both would be
    // saying something the data does not.
    await byTestId(page, "tab-documents").click();
    await expect(byTestId(page, "document-row").first()).toBeVisible();
  });

  test("the API serves the count and the rows together, and no bytes (AC 1)", async ({
    page,
  }) => {
    // Read at the API rather than through the DOM: "the label's number is the
    // server's" is a statement about the payload, and the browser assertions
    // above cannot tell a published count from a well-behaved `.length`.
    await loginAs(page, PERSONAS.handler);

    const claimId = claimWithMostPhotos(KAYA.name, KAYA.role);
    const response = await page.request.get(`/api/claims/${claimId}`);
    expect(response.status()).toBe(200);

    const block = (await response.json()).photos as {
      count: number;
      photos: { id: number; caption: string; source: string; hasBlob: boolean; blobUrl: null }[];
    };

    expect(block.count).toBe(expectedPhotosFor(claimId).length);
    expect(block.photos).toHaveLength(block.count);
    // No seeded photo has bytes behind it — the prototype has no image files —
    // and the API says so rather than pointing at content no store can serve.
    for (const photo of block.photos) {
      expect(photo.hasBlob).toBe(false);
      expect(photo.blobUrl).toBeNull();
    }
  });
});
