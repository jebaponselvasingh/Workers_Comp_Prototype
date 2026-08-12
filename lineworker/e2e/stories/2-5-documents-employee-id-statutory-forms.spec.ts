import { PERSONAS, loginAs, switchPersona } from "../fixtures/login";
import { psqlQuery } from "../fixtures/reset";
import {
  claimPath,
  expectedDocumentsFor,
  expectedForm,
  expectedFormCodes,
  expectedIdCardFor,
  firstClaimInStage,
  firstClaimOnPath,
  lastClaimInStage,
} from "../fixtures/seed";
import { byTestId } from "../fixtures/selectors";
import { expect, test } from "../fixtures/test";

/**
 * Story 2.5 — Documents, Employee ID & Statutory Forms.
 *
 * Nothing is stubbed: the browser drives the real SPA, the API classifies
 * against the real rule document, and the expectations come from
 * `fixtures/seed.ts` reading the same seed files the stack migrated with.
 *
 * **The path is the thing under test, and the oracle computes it
 * independently.** The prototype's `pathDocsHTML` reads `c.path || "B"` against
 * a dataset that has no `path` field, so all 100 of its claims render the same
 * four Path B forms. A spec that read the path off the payload would pass
 * against exactly that build; `claimPath()` restates the rule from the story
 * instead, and the second test below asserts two claims get *different* form
 * sets — which is the assertion the prototype fails.
 *
 * **These tests share one database.** The AD-15 fixture resets per spec
 * *file*, and the empty-state test empties one claim's document list, so it
 * uses a claim no other test in this file opens.
 */

const KAYA = { name: "Kaya Johnson", role: "handler" };
const SARAH = { name: "Sarah Williams", role: "handler" };

type Page = Parameters<typeof byTestId>[0];

/** Open a claim's Documents & ID tab the way a handler does. */
async function openDocuments(page: Page, claimId: string): Promise<void> {
  await page.goto(`/workspace?claim=${claimId}`);
  await byTestId(page, "tab-documents").click();
  await expect(byTestId(page, "documents-tab")).toBeVisible();
}

test.describe("@story:2-5 @epic:2 documents, employee ID and statutory forms", () => {
  test("@smoke the forms card, the ID card and a FROI viewer on a Path-B claim", async ({
    page,
  }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimOnPath(KAYA.name, KAYA.role, "b");
    const codes = expectedFormCodes("b");
    const idCard = expectedIdCardFor(claimId);
    const documents = expectedDocumentsFor(claimId);

    await openDocuments(page, claimId);

    // --- AC 1: the required-forms card, for the classified path ----------
    await expect(byTestId(page, "required-forms")).toHaveAttribute("data-path", "b");
    await expect(byTestId(page, "required-forms-banner")).toContainText(
      "Path B — Follow-Up Treatment",
    );

    const forms = byTestId(page, "required-form");
    await expect(forms).toHaveCount(codes.length);
    for (const [index, code] of codes.entries()) {
      await expect(forms.nth(index)).toHaveAttribute("data-form-code", code);
    }
    // The prose is the regulator's, not a paraphrase: description and timing
    // both render, because a form code alone tells a handler nothing about
    // when the filing is due.
    const c3 = expectedForm("b", "C-3");
    await expect(forms.first()).toContainText(c3.form_name);
    await expect(forms.first()).toContainText(c3.timing);
    await expect(byTestId(page, "required-form-download").first()).toHaveAttribute(
      "href",
      c3.download_url,
    );

    // --- AC 3: the employee ID card and the documents list ---------------
    await expect(byTestId(page, "employee-id-card")).toContainText(idCard.policyNum);
    await expect(byTestId(page, "employee-id-plant")).toHaveText(idCard.plant);
    await expect(byTestId(page, "employee-id-card")).toContainText(
      `${idCard.state} · ${idCard.region}`,
    );

    const rows = byTestId(page, "document-row");
    await expect(rows).toHaveCount(documents.length);
    await expect(byTestId(page, "document-count")).toContainText(`${documents.length} files`);

    // --- AC 4: the read-only viewer, on the claim's own FROI -------------
    const froiIndex = documents.findIndex((doc) => doc.doc_type === "froi");
    expect(froiIndex, "every seeded claim has a FROI").toBeGreaterThanOrEqual(0);
    await rows.nth(froiIndex).click();

    const viewer = byTestId(page, "document-viewer");
    await expect(viewer).toBeVisible();
    await expect(byTestId(page, "document-sheet")).toHaveAttribute("data-variant", "froi");
    await expect(byTestId(page, "document-viewer-title")).toContainText(
      documents[froiIndex].name,
    );
    // The full injury detail, which is what makes a FROI sheet a FROI sheet.
    for (const label of ["Date of Injury", "Injury Type", "Body Part", "ICD-10", "Severity"]) {
      await expect(
        page.locator(`[data-testid="document-sheet-row"][data-label="${label}"]`),
      ).toBeVisible();
    }
    // Money crossed as cents and was formatted here (AWW), not on the server.
    await expect(
      page.locator('[data-testid="document-sheet-row"][data-label="AWW"]'),
    ).toContainText("$");
    await expect(byTestId(page, "document-sheet-signatures")).toContainText("Adjuster / Date");
    // Read-only: nothing to type into, nothing to submit.
    await expect(viewer.locator("input, select, textarea")).toHaveCount(0);
    // No native dialog anywhere (NFR-3, UX-DR11).
    await expect(page.locator("[role=alertdialog]")).toHaveCount(0);

    await viewer.getByRole("button", { name: "Close" }).click();
    await expect(viewer).toBeHidden();
  });

  test("a Path-A claim shows the minor-injury form set (AC 1, AC 2)", async ({ page }) => {
    // The assertion the prototype fails. Two claims of the same handler, on
    // two paths, with disjoint statutory obligations — which is only possible
    // if something classified them.
    await loginAs(page, PERSONAS.handler);

    const minorClaim = firstClaimOnPath(KAYA.name, KAYA.role, "a");
    const ordinaryClaim = firstClaimOnPath(KAYA.name, KAYA.role, "b");
    expect(claimPath(minorClaim)).not.toBe(claimPath(ordinaryClaim));

    await openDocuments(page, minorClaim);

    await expect(byTestId(page, "required-forms")).toHaveAttribute("data-path", "a");
    await expect(byTestId(page, "required-forms-banner")).toContainText("Path A — Minor Injury");

    const codes = expectedFormCodes("a");
    await expect(byTestId(page, "required-form")).toHaveCount(codes.length);
    for (const [index, code] of codes.entries()) {
      await expect(byTestId(page, "required-form").nth(index)).toHaveAttribute(
        "data-form-code",
        code,
      );
    }
    // …and not one of Path B's, which is the half that fails against a build
    // that always renders the same set.
    for (const code of expectedFormCodes("b")) {
      await expect(
        page.locator(`[data-testid="required-form"][data-form-code="${code}"]`),
      ).toHaveCount(0);
    }
  });

  test("a non-FROI document opens the summary sheet instead (AC 4)", async ({ page }) => {
    await loginAs(page, PERSONAS.handler);

    const claimId = firstClaimOnPath(KAYA.name, KAYA.role, "b");
    const documents = expectedDocumentsFor(claimId);
    const otherIndex = documents.findIndex((doc) => doc.doc_type !== "froi");
    expect(otherIndex).toBeGreaterThanOrEqual(0);

    await openDocuments(page, claimId);
    await byTestId(page, "document-row").nth(otherIndex).click();

    await expect(byTestId(page, "document-sheet")).toHaveAttribute("data-variant", "summary");
    // The distinction that makes two variants worth having: a wage statement
    // or a medical authorization does not carry the claimant's diagnosis.
    await expect(
      page.locator('[data-testid="document-sheet-row"][data-label="ICD-10"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-testid="document-sheet-row"][data-label="Status"]'),
    ).toContainText("On file");
  });

  test("a claim with no documents shows its empty state (AC 5, NFR-3)", async ({ page }) => {
    // Unreachable against the dev seed — every claim carries three to eight
    // documents — so the state is produced rather than found. This empties one
    // claim's file for the rest of the run, so it takes the **last** settled
    // claim: "the first settled claim" is the same row `firstClaimOnPath(…,
    // "b")` returns, and the first run of this spec failed two tests later
    // because of it.
    await loginAs(page, PERSONAS.handler);
    const claimId = lastClaimInStage(KAYA.name, KAYA.role, "settled");
    expect(claimId).not.toBe(firstClaimOnPath(KAYA.name, KAYA.role, "b"));
    expect(claimId).not.toBe(firstClaimOnPath(KAYA.name, KAYA.role, "a"));

    psqlQuery(
      `DELETE FROM document WHERE claim_id = (SELECT id FROM claim WHERE claim_id = '${claimId}')`,
    );

    await openDocuments(page, claimId);

    await expect(byTestId(page, "document-list-empty")).toContainText("No documents on file");
    await expect(byTestId(page, "document-row")).toHaveCount(0);
    await expect(byTestId(page, "document-count")).toContainText("0 files");
    // The statutory obligations do not go away because nothing has been filed.
    await expect(byTestId(page, "required-form")).toHaveCount(
      expectedFormCodes(claimPath(claimId)).length,
    );
  });

  test("the API refuses another handler's document and an id that is not there (AD-7)", async ({
    page,
  }) => {
    // **The out-of-scope leg needs a document that really is on the foreign
    // claim** (code review, 2026-08-12). The first version of this test paired
    // Sarah's *claim id* with one of Kaya's *document ids*, which the
    // claim/document mismatch predicate refuses on its own — so it asserted
    // nothing about scope while its comment said it did. Sarah's own document
    // id is read while logged in as Sarah, then asked for as Kaya.
    await loginAs(page, PERSONAS.scopedHandler);
    const someoneElses = firstClaimInStage(SARAH.name, SARAH.role, "treatment");
    const hers = await page.request.get(`/api/claims/${someoneElses}`);
    expect(hers.status()).toBe(200);
    const herDocuments = (await hers.json()).documents.documents as { id: number }[];
    expect(herDocuments.length).toBeGreaterThan(0);

    // The session persists, so `loginAs` on an authenticated page lands on the
    // workspace and never sees the role picker. Switching personas is how a
    // handler leaves the console, and it is what the login fixture exposes.
    await switchPersona(page);
    await loginAs(page, PERSONAS.handler);
    const claimId = firstClaimOnPath(KAYA.name, KAYA.role, "b");

    // Three refusals, all asked as Kaya so that they are answers she can
    // compare: a claim outside her book, a document that is not on the claim
    // she named, and an id that does not exist. Same status and same problem
    // type, so walking the id space teaches her nothing.
    const outOfScope = await page.request.get(
      `/api/claims/${someoneElses}/documents/${herDocuments[0].id}/content`,
    );
    const wrongClaim = await page.request.get(
      `/api/claims/${claimId}/documents/${herDocuments[0].id}/content`,
    );
    const absent = await page.request.get(
      `/api/claims/${claimId}/documents/10000000/content`,
    );

    for (const refused of [outOfScope, wrongClaim, absent]) {
      expect(refused.status()).toBe(404);
      expect((await refused.json()).type).toBe((await absent.json()).type);
    }
  });
});
