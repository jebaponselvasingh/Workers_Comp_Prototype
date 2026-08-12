import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import {
  CLAIM_DETAIL_TREATMENT,
  ME_HANDLER,
  PHOTOS_BLOCK,
  PHOTOS_BLOCK_EMPTY,
  PHOTOS_BLOCK_MISCOUNTED,
  PHOTOS_BLOCK_WITH_IMAGE,
  stubApi,
} from "@/test/api-mock";

import { ClaimDetailPane } from "../ClaimDetailPane";

/**
 * Story 2.6 — the Photos tab, rendered from a payload.
 *
 * Every assertion is "the tab shows what the server sent". Nothing here is
 * computed from another field: the tab label's number is *not* the grid's
 * length, the thumbnail treatment is *not* inferred from a null URL, and the
 * viewer's rows are *not* composed from the claim the dialog is rendered over.
 * A component that did any of those would pass no test in this file.
 *
 * Rendered through `ClaimDetailPane` rather than by mounting `PhotosTab`
 * directly, because the tab label is part of what this story changed and the
 * label lives in `DetailTabs`, one level up from the panel.
 */

function renderPane(photos: unknown = PHOTOS_BLOCK) {
  stubApi({
    me: ME_HANDLER,
    claimDetail: { status: 200, body: { ...CLAIM_DETAIL_TREATMENT.body, photos } },
  });
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <ClaimDetailPane />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openTab(photos: unknown = PHOTOS_BLOCK) {
  renderPane(photos);
  await userEvent.click(await screen.findByTestId("tab-photos"));
  return screen.getByTestId("photos-tab");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- AC 1: the grid ------------------------------------------------------

test("the grid renders one card per photo, with its caption and its source", async () => {
  const tab = await openTab();

  const cards = within(tab).getAllByTestId("photo-card");
  expect(cards).toHaveLength(3);

  expect(cards[0]).toHaveTextContent("Platform / Scaffolding Fall area — post-incident overview");
  expect(cards[0]).toHaveTextContent("Plant safety, 03/22");
  expect(cards[1]).toHaveTextContent("OSHA investigation — incident scene documentation");
  expect(cards[1]).toHaveTextContent("OSHA inspector, 03/24");
});

test("a card is addressed by the photo's id, not by its position", async () => {
  // `openPhoto(c, i)` in the prototype takes an array index — a handle whose
  // meaning changes the moment anything is filed. The ids come from the row.
  const tab = await openTab();

  expect(
    within(tab)
      .getAllByTestId("photo-card")
      .map((card) => card.dataset.photoId),
  ).toEqual(["31", "32", "33"]);
});

// --- AC 1: the tab label's count -----------------------------------------

test("the tab label carries the count the server sent", async () => {
  renderPane();

  expect(await screen.findByTestId("tab-photos")).toHaveTextContent("Photos (3)");
});

test("the label reads the payload's count, not the length of the grid", async () => {
  // The assertion that tells the two implementations apart. Against a faithful
  // payload a component reading `photos.length` is indistinguishable from one
  // reading `count`; this payload disagrees with itself on purpose, and only
  // the second answers 9.
  renderPane(PHOTOS_BLOCK_MISCOUNTED);

  expect(await screen.findByTestId("tab-photos")).toHaveTextContent("Photos (9)");
});

test("the count is on the label before the tab has ever been opened", async () => {
  // The label is what a handler reads first, and the panel is not mounted
  // until its tab is selected — so a count sourced from the panel would render
  // as `Photos ()` on arrival and correct itself on click.
  renderPane();

  expect(await screen.findByTestId("tab-photos")).toHaveTextContent("Photos (3)");
  expect(screen.queryByTestId("photos-tab")).not.toBeInTheDocument();
});

// --- AC 1: what a thumbnail shows when there are no bytes ----------------

test("a photo with no file renders the placeholder treatment", async () => {
  const tab = await openTab();

  const thumbs = within(tab).getAllByTestId("photo-thumb");
  expect(thumbs).toHaveLength(3);
  for (const thumb of thumbs) {
    expect(thumb).toHaveAttribute("data-state", "absent");
  }
  expect(within(tab).queryByRole("img")).not.toBeInTheDocument();
});

test("a photo with a link renders the image itself", async () => {
  const tab = await openTab(PHOTOS_BLOCK_WITH_IMAGE);

  const image = within(tab).getByRole("img", {
    name: "Platform / Scaffolding Fall area — post-incident overview",
  });
  expect(image).toHaveAttribute("src", "https://blobs.example/photo-31.jpg");
});

test("a photo that exists but cannot be linked to is not reported as missing", async () => {
  // A mounted-volume deployment: `BlobStore.url` answers null *by design*, and
  // the bytes are there. A component that read the null URL as "no photo"
  // would show "no image on file" across a grid of real photographs.
  const tab = await openTab(PHOTOS_BLOCK_WITH_IMAGE);

  const thumbs = within(tab).getAllByTestId("photo-thumb");
  expect(thumbs[1]).toHaveAttribute("data-state", "unlinked");
  expect(thumbs[1]).not.toHaveAttribute("data-state", "absent");
});

// --- AC 3: the empty state -----------------------------------------------

test("a claim with no photos says so, and its label reads zero", async () => {
  renderPane(PHOTOS_BLOCK_EMPTY);

  expect(await screen.findByTestId("tab-photos")).toHaveTextContent("Photos (0)");

  await userEvent.click(screen.getByTestId("tab-photos"));
  expect(screen.getByTestId("photos-empty")).toHaveTextContent("No photos on file.");
  expect(screen.queryAllByTestId("photo-card")).toHaveLength(0);
});

// --- AC 2: the read-only viewer ------------------------------------------

test("clicking a card opens a viewer showing its caption, source and claim", async () => {
  const tab = await openTab();
  await userEvent.click(within(tab).getAllByTestId("photo-card")[1]);

  const viewer = screen.getByTestId("photo-viewer");
  expect(viewer).toBeVisible();
  expect(viewer).toHaveTextContent("OSHA investigation — incident scene documentation");
  expect(viewer).toHaveTextContent("OSHA inspector, 03/24");
  // The claim's business id — the third row `openPhoto` prints.
  expect(viewer).toHaveTextContent("WC-20017");
});

test("the viewer offers nothing to edit, delete or submit", async () => {
  // Structural, not a promise in a docstring: `photo`'s grant is SELECT and
  // no command writes one, so a viewer with an affordance would be an
  // affordance for something the API cannot do.
  const tab = await openTab();
  await userEvent.click(within(tab).getAllByTestId("photo-card")[0]);

  const viewer = screen.getByTestId("photo-viewer");
  expect(viewer.querySelectorAll("input, select, textarea")).toHaveLength(0);
  expect(within(viewer).getAllByRole("button")).toHaveLength(1);
  expect(within(viewer).getByRole("button", { name: "Close" })).toBeVisible();
});

test("the viewer closes on ✕", async () => {
  const tab = await openTab();
  await userEvent.click(within(tab).getAllByTestId("photo-card")[0]);

  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByTestId("photo-viewer")).not.toBeInTheDocument();
});

test("opening the viewer asks the server for nothing", async () => {
  // The one departure from Story 2.5's document viewer, and it is deliberate:
  // a document sheet is a dozen rows of the *claim's* data assembled per
  // document, so folding every sheet into the case file would multiply the
  // most-fetched payload in the console. A photo's viewer shows three fields
  // the grid already holds — a query for them would be a round trip for data
  // the client is rendering behind the dialog.
  const tab = await openTab();
  const before = vi.mocked(globalThis.fetch).mock.calls.length;

  await userEvent.click(within(tab).getAllByTestId("photo-card")[0]);
  expect(screen.getByTestId("photo-viewer")).toBeVisible();

  expect(vi.mocked(globalThis.fetch).mock.calls).toHaveLength(before);
});

test("a card is a button, so the viewer opens from the keyboard", async () => {
  // The prototype attaches `data-pi` to a `div` and delegates, which makes its
  // photo viewer unopenable without a mouse and invisible to a screen reader.
  // Story 2.5's document rows fixed the same defect in the same way.
  const tab = await openTab();
  const card = within(tab).getAllByTestId("photo-card")[0];

  expect(card.tagName).toBe("BUTTON");

  card.focus();
  expect(card).toHaveFocus();
  await userEvent.keyboard("{Enter}");

  expect(screen.getByTestId("photo-viewer")).toBeVisible();
});

// --- NFR-3: the tab's loading and error states ---------------------------

test("the tab is not reachable while the case file is still loading", async () => {
  // Loading and error are the *pane's* states, not a second set of the tab's:
  // the photos ride the shared `claimDetail` query (AD-9), so there is one
  // request, one skeleton and one error message for the whole case file. A tab
  // with a skeleton of its own would imply a fetch that does not happen.
  stubApi({ me: ME_HANDLER, claimDetail: "pending" });
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <ClaimDetailPane />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByTestId("detail-skeleton")).toBeVisible();
  expect(screen.queryByTestId("tab-photos")).not.toBeInTheDocument();
});

test("a failed case file reports the failure instead of an empty grid", async () => {
  // A 403 rather than a 500: the shared client retries 5xx twice with backoff,
  // so a 500 races the retry timer instead of testing the branch (Story 1.4's
  // lesson, restated by `ClaimDetailPane.test.tsx`).
  stubApi({
    me: ME_HANDLER,
    claimDetail: {
      status: 403,
      body: { type: "/problems/forbidden", title: "Forbidden", status: 403, detail: "no" },
    },
  });
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/workspace?claim=WC-20017"]}>
        <ClaimDetailPane />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByTestId("detail-error")).toBeVisible();
  expect(screen.queryByTestId("photos-empty")).not.toBeInTheDocument();
});
