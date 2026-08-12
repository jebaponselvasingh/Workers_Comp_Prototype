/**
 * Story 2.3 — the three answers an inline edit can get, in the browser's
 * terms.
 *
 * Everything here is behaviour the *server* cannot assert: that the typed
 * value appears before the response lands, that a 409 rolls it back and
 * shows the value that won, that a 422 keeps what was typed with the reason
 * beside it, and that none of the three is a dialog (NFR-3).
 *
 * The stub answers HTTP, so the real generated client, the real problem+json
 * translation and the real TanStack mutation all run — only the network is
 * replaced. The same paths against the real API are the e2e spec's job.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import {
  applyOptimisticEdit,
  type ClaimDetail,
  type InvestigationOverviewData,
  type TreatmentOverviewData,
} from "@/api/claims";
import { createQueryClient } from "@/api/queryClient";
import {
  CLAIM_DETAIL_INVESTIGATION,
  CLAIM_DETAIL_TREATMENT,
  ME_HANDLER,
  stubApi,
} from "@/test/api-mock";

import { ClaimDetailPane } from "./ClaimDetailPane";

/**
 * One canned PATCH answer. Narrower than the shared `StubRoute`, which also
 * has a `"pending"` arm: a write that never settles is not a state this
 * story has behaviour for, and admitting it here would mean guarding every
 * `answer.status` for a case no test writes.
 */
interface CannedResponse {
  status: number;
  body: unknown;
}

/**
 * `"pending"` is a PATCH that never settles — the only way to observe the
 * optimistic state and the disabled-while-saving rule, both of which are
 * defined entirely by what is true *before* a response arrives.
 */
type CannedPatch = CannedResponse | "pending";

const CLAIM = "WC-20051";
const DETAIL = CLAIM_DETAIL_INVESTIGATION.body as unknown as ClaimDetail;
// Narrowed once: `overview` is a discriminated union, and every assertion
// below is about the investigation arm of it.
const OVERVIEW = DETAIL.overview as InvestigationOverviewData;

/**
 * Render the pane with a GET answer and a queue of PATCH answers.
 *
 * The PATCH answers are a *list* rather than one value because the point of
 * several tests is what the second attempt does — a stub that answered the
 * same way forever could not tell "rolled back" from "never changed".
 */
function renderInvestigation(patchAnswers: CannedPatch[] = []) {
  const patches: unknown[] = [];
  let index = 0;

  // The shared stub routes by URL alone, and the GET and the PATCH share
  // one. So it answers every *read* with the case file, and the wrapper
  // below takes the writes off it — which also keeps the recorded bodies in
  // one place.
  stubApi({ me: ME_HANDLER, claimDetail: CLAIM_DETAIL_INVESTIGATION });
  const inner = globalThis.fetch as unknown as typeof fetch;
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? (input instanceof Request ? input.method : "GET");
    if (method === "PATCH") {
      const raw = init?.body ?? (input instanceof Request ? await input.clone().text() : null);
      patches.push(JSON.parse(String(raw)));
      const answer = patchAnswers[Math.min(index, patchAnswers.length - 1)];
      index += 1;
      if (!answer) return new Response(null, { status: 500 });
      if (answer === "pending") return new Promise<Response>(() => {});
      return new Response(JSON.stringify(answer.body), {
        status: answer.status,
        headers: {
          "content-type":
            answer.status >= 400 ? "application/problem+json" : "application/json",
        },
      });
    }
    return inner(input as RequestInfo, init);
  });

  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[`/workspace?claim=${CLAIM}`]}>
        <ClaimDetailPane />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { patches };
}

function accepted(overrides: Partial<InvestigationOverviewData> = {}, version = 4) {
  return {
    status: 200,
    body: { ...DETAIL, version, overview: { ...OVERVIEW, ...overrides } },
  };
}

const CONFLICT = {
  status: 409,
  body: {
    type: "/problems/stale-write",
    title: "Conflict",
    status: 409,
    detail: "This claim was changed by someone else while you were editing.",
    claim: {
      ...DETAIL,
      version: 9,
      overview: { ...OVERVIEW, cause: "Struck by Falling Tooling" },
      header: { ...DETAIL.header, cause: "Struck by Falling Tooling" },
    },
  },
};

const INVALID = {
  status: 422,
  body: {
    type: "/problems/invalid-patch",
    title: "Unprocessable Content",
    status: 422,
    detail: "ICD-10 must look like `S61.412A`",
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- AC 1: what the handler edits reaches the command --------------------

test("committing a text field sends the version it was read at", async () => {
  const user = userEvent.setup();
  const { patches } = renderInvestigation([accepted({ cause: "Slip on Coolant" })]);

  const cause = await screen.findByTestId("edit-cause");
  await user.clear(cause);
  await user.type(cause, "Slip on Coolant");
  await user.tab();

  await waitFor(() => expect(patches).toHaveLength(1));
  // Only the edited field, plus the version the compare-and-swap runs on —
  // a body carrying the other five would be a PATCH that overwrites fields
  // the handler did not touch with values that may have moved.
  expect(patches[0]).toEqual({ expectedVersion: DETAIL.version, cause: "Slip on Coolant" });
});

test("a select commits on choice, as the prototype does", async () => {
  const user = userEvent.setup();
  const { patches } = renderInvestigation([accepted({ recovery: "weeks_6_8" })]);

  await user.selectOptions(await screen.findByTestId("edit-recovery"), "weeks_6_8");

  await waitFor(() => expect(patches).toHaveLength(1));
  expect(patches[0]).toEqual({ expectedVersion: DETAIL.version, recovery: "weeks_6_8" });
});

test("the body-part select is keyed on the diagram key, not the stored label", async () => {
  const user = userEvent.setup();
  const { patches } = renderInvestigation([accepted()]);

  const select = (await screen.findByTestId("edit-bodyKey")) as HTMLSelectElement;
  // The header's `bodyKey`, not its `bodyPart` — the two are different
  // vocabularies and the fixture keeps them different on purpose.
  expect(select.value).toBe("lumbar");
  expect(select.options).toHaveLength(11);

  await user.selectOptions(select, "hand_right");
  await waitFor(() => expect(patches).toHaveLength(1));
  expect(patches[0]).toEqual({ expectedVersion: DETAIL.version, bodyKey: "hand_right" });
});

test("blurring without changing anything sends no request", async () => {
  const user = userEvent.setup();
  const { patches } = renderInvestigation([accepted()]);

  const cause = await screen.findByTestId("edit-cause");
  await user.click(cause);
  await user.tab();

  expect(patches).toHaveLength(0);
});

test("Escape abandons the edit rather than committing it", async () => {
  const user = userEvent.setup();
  const { patches } = renderInvestigation([accepted()]);

  const injury = await screen.findByTestId("edit-injuryType");
  await user.clear(injury);
  await user.type(injury, "Typed by mistake{Escape}");

  expect((injury as HTMLInputElement).value).toBe(OVERVIEW.injuryType);
  await user.tab();
  expect(patches).toHaveLength(0);
});

// --- AC 1/3: the optimistic value, and what waits for the server ---------

test("the edited scalar appears before the server answers, and only it", () => {
  // Asserted on the function rather than through a render, because what is
  // interesting is what it *leaves alone*: a component test can see the new
  // cause on screen either way, but only this can see that the version and
  // the derived band did not move with it.
  const optimistic = applyOptimisticEdit(DETAIL, "cause", "Slip on Coolant");

  expect(optimistic.overview).toMatchObject({ cause: "Slip on Coolant" });
  expect(optimistic.header.cause).toBe("Slip on Coolant");
  // Everything the server decides waits for the server (AD-9, AD-10).
  expect(optimistic.version).toBe(DETAIL.version);
  expect(optimistic.header.risk).toBe(DETAIL.header.risk);
  expect(optimistic.header.severityScore).toBe(DETAIL.header.severityScore);
  expect(optimistic.overview).toMatchObject({ costSplit: OVERVIEW.costSplit });
});

test("the treatment variant echoes its one editable field too", () => {
  // `applyOptimisticEdit` handled the investigation arm only, so the
  // treatment card's recovery `<select>` re-rendered the *old* option the
  // instant a handler picked a new one — the choice appearing to be
  // rejected, then jumping when the response landed (code review,
  // 2026-08-12).
  const treatment = CLAIM_DETAIL_TREATMENT.body as unknown as ClaimDetail;
  const optimistic = applyOptimisticEdit(treatment, "recovery", "over_1_year");

  expect((optimistic.overview as TreatmentOverviewData).recovery).toBe("over_1_year");
  // The derived figures beside it still wait for the server: `expectedDays`
  // and the phase are the rules tier's answer, not the browser's (AD-9).
  expect((optimistic.overview as TreatmentOverviewData).expectedDays).toBe(
    (treatment.overview as TreatmentOverviewData).expectedDays,
  );
  expect((optimistic.overview as TreatmentOverviewData).phase).toBe(
    (treatment.overview as TreatmentOverviewData).phase,
  );
});

test("choosing a body part does not guess the label that goes with it", () => {
  // The key→label mapping is the server's (`services/claims/reference.py`).
  // A client that filled the label in would be a second copy of a
  // vocabulary — and the one that is wrong whenever the two drift.
  const optimistic = applyOptimisticEdit(DETAIL, "bodyKey", "hand_right");

  expect(optimistic.header.bodyKey).toBe("hand_right");
  expect(optimistic.header.bodyPart).toBe(DETAIL.header.bodyPart);
});

test("the primary marker moves with the body part, and nothing else on it does", () => {
  // `claim.body_key` reaches the payload twice — `header.bodyKey` and
  // `injury.markers[0].bodyKey` — and only the header was echoed (Story 2.6's
  // review pass, on the 2.4 surface). Both are on screen together on the
  // Injury Diagram tab, so the select moved instantly while the pulsing marker
  // sat on the old hotspot until the response landed, then jumped: the same
  // defect 2.4's own review fixed for the treatment recovery select.
  const optimistic = applyOptimisticEdit(DETAIL, "bodyKey", "hand_right");
  const [primary, ...secondaries] = optimistic.injury.markers;

  expect(primary.primary).toBe(true);
  expect(primary.bodyKey).toBe("hand_right");

  // The derived values on the same marker still wait for the server — AD-9 is
  // explicit that a band is not the browser's to guess, and `bodyPart` is the
  // label this file's test above already refuses to invent.
  expect(primary.band).toBe(DETAIL.injury.markers[0].band);
  expect(primary.severityScore).toBe(DETAIL.injury.markers[0].severityScore);
  expect(primary.bodyPart).toBe(DETAIL.injury.markers[0].bodyPart);

  // A secondary injury is its own `additional_injury` row and this command
  // does not touch it.
  expect(secondaries).toEqual(DETAIL.injury.markers.slice(1));
});

test("a successful edit renders the server's entity, not the typed value", async () => {
  const user = userEvent.setup();
  // **The response value differs from both the typed text and the cached
  // one.** The first version of this test typed `"  s61.412a  "` and expected
  // `"S61.412A"` — which the fixture already held, so it passed with the
  // canned response changed to `WRONG.9`, and would have passed if the
  // mutation never fired at all (code review). Here only a render of the
  // server's entity produces the expected value.
  renderInvestigation([accepted({ icd: "M54.50" }, 4)]);

  const icd = await screen.findByTestId("edit-icd");
  await user.clear(icd);
  await user.type(icd, "  m54.50  ");
  await user.tab();

  await waitFor(() => expect((icd as HTMLInputElement).value).toBe("M54.50"));
  expect((icd as HTMLInputElement).value).not.toBe(OVERVIEW.icd);
});

test("the ICD code and its description are sent as one patch", async () => {
  const user = userEvent.setup();
  const { patches } = renderInvestigation([accepted({ icd: "M54.50" })]);

  const icd = await screen.findByTestId("edit-icd");
  await user.clear(icd);
  await user.type(icd, "M54.50");
  await user.tab();

  await waitFor(() => expect(patches).toHaveLength(1));
  // The command refuses one without the other, so the row sends the
  // description it is displaying alongside the code. The unchanged half is
  // dropped server-side, so the audit diff still records only what moved.
  expect(patches[0]).toEqual({
    expectedVersion: DETAIL.version,
    icd: "M54.50",
    icdDesc: OVERVIEW.icdDesc,
  });
});

test("the typed value is on screen before the server answers", async () => {
  const user = userEvent.setup();
  // A PATCH that never settles, so the optimistic state is observable at
  // all. Nothing covered this: the pure-function test asserts what
  // `applyOptimisticEdit` leaves alone, and renders nothing (code review).
  renderInvestigation(["pending"]);

  const cause = await screen.findByTestId("edit-cause");
  await user.clear(cause);
  await user.type(cause, "Slip on Coolant");
  await user.tab();

  await waitFor(() =>
    expect((screen.getByTestId("edit-cause") as HTMLInputElement).value).toBe(
      "Slip on Coolant",
    ),
  );
  // …and the header, which shows the same scalar, moved with it. Lowercased
  // because that is how the header renders a cause — the assertion is that
  // the optimistic write reached it, not that it reached it verbatim.
  expect(screen.getByTestId("case-header-injury")).toHaveTextContent("slip on coolant");
});

test("every field is disabled while a commit is in flight", async () => {
  const user = userEvent.setup();
  renderInvestigation(["pending"]);

  const cause = await screen.findByTestId("edit-cause");
  await user.clear(cause);
  await user.type(cause, "First Edit");
  await user.tab();

  // Not just the field being saved. `expectedVersion` comes from the cache
  // and the optimistic update does not advance it, so a second commit would
  // carry a version the first has already consumed — a 409 telling the
  // handler somebody else changed the claim, about their own edit.
  await waitFor(() => expect(screen.getByTestId("edit-recovery")).toBeDisabled());
  expect(screen.getByTestId("edit-cause")).toBeDisabled();
  expect(screen.getByTestId("edit-icd")).toBeDisabled();
});

test("a refusal on one field survives editing another", async () => {
  const user = userEvent.setup();
  renderInvestigation([INVALID, accepted({ cause: "Slip on Coolant" })]);

  const icd = await screen.findByTestId("edit-icd");
  await user.clear(icd);
  await user.type(icd, "not-a-code");
  await user.tab();
  await screen.findByTestId("edit-icd-invalid");

  const cause = screen.getByTestId("edit-cause");
  await user.clear(cause);
  await user.type(cause, "Slip on Coolant");
  await user.tab();

  // The single feedback slot used to clear here, taking the rejected value
  // and the message with it — the "we kept what you typed" promise
  // evaporating on the next keystroke elsewhere (code review).
  await waitFor(() => expect(screen.getByTestId("edit-icd-invalid")).toBeInTheDocument());
  expect((screen.getByTestId("edit-icd") as HTMLInputElement).value).toBe("not-a-code");
});

test("a 409 whose body is not a case file is not written into the cache", async () => {
  const user = userEvent.setup();
  // A proxy-truncated conflict body. Written straight in, it left
  // `detail.data.header` undefined, `CaseHeader` threw, and — there being no
  // error boundary — the pane blanked (code review).
  renderInvestigation([
    {
      status: 409,
      body: { ...CONFLICT.body, claim: { claimId: "WC-20051" } },
    },
  ]);

  const cause = await screen.findByTestId("edit-cause");
  await user.clear(cause);
  await user.type(cause, "Loser");
  await user.tab();

  await waitFor(() => expect(screen.getByTestId("edit-cause-conflict")).toBeInTheDocument());
  // The card is still standing, showing the value it had before the edit.
  expect(screen.getByTestId("investigation-injury")).toBeInTheDocument();
  expect((screen.getByTestId("edit-cause") as HTMLInputElement).value).toBe(OVERVIEW.cause);
});

// --- AC 2: the conflict -------------------------------------------------

test("a 409 rolls the optimistic value back and renders the fresh one inline", async () => {
  const user = userEvent.setup();
  renderInvestigation([CONFLICT]);

  const cause = await screen.findByTestId("edit-cause");
  await user.clear(cause);
  await user.type(cause, "Second Writer Loses");
  await user.tab();

  // The value that won, from the problem document's own entity — not a
  // refetch, and not a merge of the two.
  await waitFor(() =>
    expect((cause as HTMLInputElement).value).toBe("Struck by Falling Tooling"),
  );
  expect(screen.getByTestId("edit-cause-conflict")).toHaveTextContent(
    "Updated by someone else",
  );
});

test("a conflict refreshes the version the next edit is compared against", async () => {
  const user = userEvent.setup();
  const { patches } = renderInvestigation([CONFLICT, accepted({}, 10)]);

  const cause = await screen.findByTestId("edit-cause");
  await user.clear(cause);
  await user.type(cause, "First Attempt");
  await user.tab();
  await waitFor(() => expect(screen.getByTestId("edit-cause-conflict")).toBeInTheDocument());

  await user.clear(cause);
  await user.type(cause, "Second Attempt");
  await user.tab();

  await waitFor(() => expect(patches).toHaveLength(2));
  // The retry is the *handler's*, and it carries the version the conflict
  // taught the client. A silent retry on the old version is the thing AD-9
  // forbids; a deliberate one on the new version is the resolution.
  expect(patches[0]).toMatchObject({ expectedVersion: DETAIL.version });
  expect(patches[1]).toMatchObject({ expectedVersion: 9, cause: "Second Attempt" });
});

test("a conflict is rendered inline, never as a dialog (NFR-3)", async () => {
  const user = userEvent.setup();
  renderInvestigation([CONFLICT]);

  const cause = await screen.findByTestId("edit-cause");
  await user.clear(cause);
  await user.type(cause, "Loser");
  await user.tab();

  await waitFor(() => expect(screen.getByTestId("edit-cause-conflict")).toBeInTheDocument());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});

// --- AC 1: the validation refusal ---------------------------------------

test("a 422 shows the server's reason at the field and keeps what was typed", async () => {
  const user = userEvent.setup();
  renderInvestigation([INVALID]);

  const icd = await screen.findByTestId("edit-icd");
  await user.clear(icd);
  await user.type(icd, "not-a-code");
  await user.tab();

  const message = await screen.findByTestId("edit-icd-invalid");
  expect(message).toHaveTextContent("ICD-10 must look like");
  // Kept, so the handler corrects it instead of retyping it — and marked
  // invalid so a screen reader hears the same thing the border says.
  expect((icd as HTMLInputElement).value).toBe("not-a-code");
  expect(icd).toHaveAttribute("aria-invalid", "true");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("a refused description keeps the description, not the code beside it", async () => {
  // The ICD pair is the only two-field commit, and its key order is always
  // `icd, icdDesc` — so a single `attempted` taken from the first key put
  // the *code* into the *description* input, ready to be committed as the
  // description if the handler typed on top of it (code review,
  // 2026-08-12). Both halves are asserted, because the mirror case blanked
  // the description instead.
  const user = userEvent.setup();
  renderInvestigation([
    {
      status: 422,
      body: {
        type: "/problems/invalid-patch",
        title: "Unprocessable Content",
        status: 422,
        detail: "ICD-10 description cannot be empty",
      },
    },
  ]);

  const description = await screen.findByTestId("edit-icdDesc");
  await user.clear(description);
  await user.tab();

  await screen.findByTestId("edit-icdDesc-invalid");
  // What was submitted for *this* field — nothing — rather than the code
  // that rode along unchanged in the same patch.
  expect((description as HTMLInputElement).value).toBe("");
  // …and the untouched half of the pair still shows its own value.
  expect((screen.getByTestId("edit-icd") as HTMLInputElement).value).toBe(OVERVIEW.icd);
});

test("a refused code keeps the code, and leaves the description alone", async () => {
  const user = userEvent.setup();
  renderInvestigation([INVALID]);

  const icd = await screen.findByTestId("edit-icd");
  await user.clear(icd);
  await user.type(icd, "not-a-code");
  await user.tab();

  await screen.findByTestId("edit-icd-invalid");
  expect((icd as HTMLInputElement).value).toBe("not-a-code");
  // The description rode along unchanged, so it shows what it always showed
  // — not the string typed into the field next to it.
  expect((screen.getByTestId("edit-icdDesc") as HTMLInputElement).value).toBe(
    OVERVIEW.icdDesc,
  );
});

test("any other failure says so at the field without claiming a reason", async () => {
  const user = userEvent.setup();
  renderInvestigation([{ status: 503, body: { detail: "upstream" } }]);

  const cause = await screen.findByTestId("edit-cause");
  await user.clear(cause);
  await user.type(cause, "Anything");
  await user.tab();

  expect(await screen.findByTestId("edit-cause-failed")).toHaveTextContent("Could not save");
  // Rolled back to the server's value: an optimistic value left on screen
  // after a failed write is a claim the handler has no reason to doubt.
  expect((cause as HTMLInputElement).value).toBe(OVERVIEW.cause);
});
