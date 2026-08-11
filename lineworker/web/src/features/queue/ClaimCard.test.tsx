import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import type { ClaimCard as ClaimCardData } from "@/api/claims";
import { LOUD_CARD, QUIET_CARD } from "@/test/api-mock";

import { ClaimCard } from "./ClaimCard";

/**
 * Story 2.1 AC 5 — the card shows the payload, and only the payload.
 *
 * Every fixture below carries a combination the component could not have
 * derived: `LOUD_CARD` is high risk with all four badges and a marker;
 * `QUIET_CARD` has none of them. A component that recomputed a flag from,
 * say, `priorityScore` would fail on one of the two.
 */

function renderCard(card: ClaimCardData, selected = false) {
  const onSelect = vi.fn();
  render(<ClaimCard card={card} selected={selected} onSelect={onSelect} />);
  return onSelect;
}

test("all four rows render the fields the server sent", () => {
  renderCard(LOUD_CARD as ClaimCardData);

  expect(screen.getByTestId("queue-card-id")).toHaveTextContent("WC-20017");
  // The day count on its own testid: "140d" contains "40", and a
  // card-level substring check would accept the wrong number.
  expect(screen.getByTestId("queue-card-days-open")).toHaveTextContent(/^140d$/);
  expect(screen.getByTestId("queue-card-worker")).toHaveTextContent("Marcus Delgado");
  expect(screen.getByTestId("queue-card-injury")).toHaveTextContent("Fall from Height");
  expect(screen.getByTestId("queue-card-stage")).toHaveTextContent("Treatment");
  expect(screen.getByTestId("queue-card-employer")).toHaveTextContent("Caterpillar");
});

test("the risk dot reports the band the server banded, not a score", () => {
  renderCard(LOUD_CARD as ClaimCardData);
  expect(screen.getByTestId("queue-card-risk")).toHaveAttribute("data-risk", "high");
  expect(screen.getByTestId("queue-card-risk")).toHaveClass("bg-error");
});

test("every flag on the payload becomes its badge", () => {
  renderCard(LOUD_CARD as ClaimCardData);

  expect(screen.getByTestId("queue-card-badge-fraudFlag")).toHaveTextContent("FRAUD");
  expect(screen.getByTestId("queue-card-badge-litigationFlag")).toHaveTextContent("LITIG");
  expect(screen.getByTestId("queue-card-badge-paymentDue")).toHaveTextContent("PAY DUE");
  expect(screen.getByTestId("queue-card-badge-siuReview")).toHaveTextContent("SIU");
});

test("a claim with no flags carries no badges at all", () => {
  renderCard(QUIET_CARD as ClaimCardData);

  for (const field of ["fraudFlag", "litigationFlag", "paymentDue", "siuReview"]) {
    expect(screen.queryByTestId(`queue-card-badge-${field}`)).not.toBeInTheDocument();
  }
  expect(screen.getByTestId("queue-card-risk")).toHaveClass("bg-ok");
});

test("a marked claim carries the priority marker and says so out loud", () => {
  renderCard(LOUD_CARD as ClaimCardData);
  expect(screen.getByTestId("queue-card-marker")).toBeInTheDocument();
  // The 🔺 is `aria-hidden`; "red triangle pointing up" is not a fact about
  // a claim, so the meaning rides an `sr-only` word beside it.
  expect(screen.getByTestId("queue-card-id")).toHaveTextContent("Priority.");
});

test("an unmarked claim carries no marker", () => {
  renderCard(QUIET_CARD as ClaimCardData);
  expect(screen.queryByTestId("queue-card-marker")).not.toBeInTheDocument();
});

test("a day count of zero renders as 0d, not as an em dash", () => {
  // Deliberately unlike the top bar's tiles, where an em dash means "the
  // server could not compute this". Here the server *did* compute it: a
  // claim reported today is zero days old, which is a fact.
  renderCard(QUIET_CARD as ClaimCardData);
  expect(screen.getByTestId("queue-card-days-open")).toHaveTextContent(/^0d$/);
});

test("the selected card marks itself current", () => {
  renderCard(LOUD_CARD as ClaimCardData, true);
  expect(screen.getByRole("button")).toHaveAttribute("aria-current", "true");
});

test("clicking reports the business id, not an index", async () => {
  const onSelect = renderCard(QUIET_CARD as ClaimCardData);
  await userEvent.click(screen.getByRole("button"));
  expect(onSelect).toHaveBeenCalledWith("WC-20044");
});
