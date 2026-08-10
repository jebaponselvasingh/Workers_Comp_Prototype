import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";

import { createQueryClient } from "@/api/queryClient";
import { SEEDED_PERSONAS, stubApi } from "@/test/api-mock";

import { LoginScreen } from "./LoginScreen";

function renderLogin() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={["/"]}>
        <LoginScreen />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function personaOptions(): string[] {
  const select = screen.getByLabelText("Log in as") as HTMLSelectElement;
  return [...select.options].map((option) => option.textContent ?? "");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("supervisor is pre-selected and its personas are pre-populated", async () => {
  stubApi({});
  renderLogin();

  expect(screen.getByRole("radio", { name: /Supervisor/ })).toBeChecked();
  await waitFor(() =>
    expect(personaOptions()).toEqual([
      "David Bline — WC Supervisor (Full portfolio)",
      "Jennifer Park — WC Supervisor (3M/GM/Toyota)",
    ]),
  );
});

test("clicking a role card repopulates the dropdown with only that role's personas", async () => {
  stubApi({});
  renderLogin();
  await waitFor(() => expect(personaOptions()).toHaveLength(2));

  fireEvent.click(screen.getByRole("radio", { name: /Claims Handler/ }));

  await waitFor(() =>
    expect(personaOptions()).toEqual(["Kaya Johnson — Handler (Caterpillar · GE)"]),
  );

  fireEvent.click(screen.getByRole("radio", { name: /Data Analyst/ }));
  await waitFor(() =>
    expect(personaOptions()).toEqual(["David Bline — WC Supervisor (Analyst view)"]),
  );
});

test("switching roles resets the selection so a stale persona id cannot submit", async () => {
  stubApi({});
  renderLogin();
  await waitFor(() => expect(personaOptions()).toHaveLength(2));

  const select = screen.getByLabelText("Log in as") as HTMLSelectElement;
  fireEvent.change(select, { target: { value: "8" } }); // Jennifer Park
  expect(select.value).toBe("8");

  fireEvent.click(screen.getByRole("radio", { name: /Claims Handler/ }));

  // Kaya Johnson (id 1), not the supervisor id that was selected a moment ago.
  await waitFor(() => expect(select.value).toBe("1"));
});

test("a failed login renders inline, not in an alert() dialog", async () => {
  stubApi({
    login: {
      status: 401,
      body: {
        type: "/problems/unauthenticated",
        title: "Unauthenticated",
        status: 401,
        detail: "That persona is not available.",
      },
    },
  });
  renderLogin();
  await waitFor(() => expect(personaOptions()).toHaveLength(2));

  fireEvent.click(screen.getByRole("button", { name: /Enter Console/ }));

  const error = await screen.findByRole("alert");
  expect(error).toHaveTextContent("That persona is not available.");
});

test("an unreachable personas endpoint says so instead of showing an empty picker", async () => {
  // 404: the proxy is up but not routing /api — a real misconfiguration,
  // and a client error, so it surfaces immediately rather than after the
  // retry backoff that a 5xx (rightly) gets.
  stubApi({ personas: { status: 404, body: { detail: "Not Found" } } });
  renderLogin();

  const error = await screen.findByRole("alert");
  expect(error).toHaveTextContent(/Could not load personas/);
});

test("the dataset banner and role cards match the prototype's copy", async () => {
  stubApi({});
  renderLogin();

  expect(
    screen.getByText(/Dataset: WC_Manufacturing_Claims_2026\.xlsx · 100 unique employees/),
  ).toBeInTheDocument();
  expect(screen.getByText("Portfolio, SLA, handler performance")).toBeInTheDocument();
  expect(screen.getByText("Caseload, case detail, copilot")).toBeInTheDocument();
  expect(screen.getByText("Fraud, trends, KPI drill-down")).toBeInTheDocument();
  // Guard against the stub drifting from what the screen actually filters.
  expect(SEEDED_PERSONAS.body.items).toHaveLength(4);
});

test("a role with no personas says so rather than showing a dead form", async () => {
  stubApi({
    personas: { status: 200, body: { items: [], nextCursor: null, total: 0 } },
  });
  renderLogin();

  const error = await screen.findByRole("alert");
  expect(error).toHaveTextContent(/No personas are configured for this role/);
});

test("an error body that is not a problem document still explains itself", async () => {
  // A proxy answering {"detail": ...} parses as JSON but carries none of
  // the RFC 9457 fields; the message must not come out empty.
  stubApi({ login: { status: 503, body: { oops: "gateway" } } });
  renderLogin();
  await waitFor(() => expect(personaOptions()).toHaveLength(2));

  fireEvent.click(screen.getByRole("button", { name: /Enter Console/ }));

  const error = await screen.findByRole("alert");
  expect(error).toHaveTextContent(/Could not enter the console\..*503/);
});

test("the unroutable-role alert clears on the next attempt (code review)", async () => {
  // The server hands back a role this build has no shell for, so the login
  // succeeds but cannot navigate.
  stubApi({
    login: { status: 200, body: { id: 99, name: "Nia Osei", role: "auditor", initials: "NO" } },
  });
  renderLogin();
  await waitFor(() => expect(personaOptions()).toHaveLength(2));

  fireEvent.click(screen.getByRole("button", { name: /Enter Console/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/no workspace for the role/);

  // Picking a different role is a new intention: the alert described the
  // previous attempt and was pinned to the form until this fix.
  fireEvent.click(screen.getByRole("radio", { name: /Claims Handler/ }));

  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

test("a later failure reports its own reason, not the stale unroutable alert", async () => {
  stubApi({
    login: { status: 200, body: { id: 99, name: "Nia Osei", role: "auditor", initials: "NO" } },
  });
  renderLogin();
  await waitFor(() => expect(personaOptions()).toHaveLength(2));

  fireEvent.click(screen.getByRole("button", { name: /Enter Console/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/no workspace for the role/);

  // A successful login clears the cache (so a second persona cannot
  // inherit the first one's server state), which on this stay-on-screen
  // path also drops and refetches the public persona list. Wait for it to
  // come back — until it does there is no persona to submit.
  stubApi({ login: { status: 503, body: { oops: "gateway" } } });
  await waitFor(() => expect(personaOptions()).toHaveLength(2));

  // Same form, next attempt, different failure: the ternary reached the
  // stale unroutable branch first and hid the real message.
  fireEvent.click(screen.getByRole("button", { name: /Enter Console/ }));

  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(/Could not enter the console\..*503/),
  );
});
