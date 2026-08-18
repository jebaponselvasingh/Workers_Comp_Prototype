/**
 * Story 4.3 — the toast primitive three specs deferred, and the two properties
 * that make it worth having its own file.
 *
 * The first is the **no-effect** discipline. `push()` schedules its own
 * dismissal from the event handler that called it, which is what keeps
 * `features/diary` free of the cascading `setState`-in-effect shape
 * `react-hooks/set-state-in-effect` refuses. A test that only pushed and read
 * the message would pass against an effect-based implementation too; what pins
 * the design is that the timer runs *without anything else happening* — no
 * re-render, no second interaction — which is what the fake clock below asserts.
 *
 * The second is that the live region is **always mounted**. A `role="status"`
 * element that appears at the same moment as its content is frequently not
 * announced at all (Story 3.5's note), and every live region in this codebase is
 * rendered empty for that reason. Here it is the host that is unconditional and
 * the toasts that come and go inside it.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { TOAST_DURATION_MS, TOAST_LIMIT, ToastHost, ToastProvider, useToast } from "./toast";

/** A consumer that can push, so the tests drive the API rather than the DOM. */
function Pusher() {
  const { push } = useToast();

  return (
    <>
      <button type="button" data-testid="push-ok" onClick={() => push({ tone: "ok", message: "✓ Email logged to: Employee" })}>
        push ok
      </button>
      <button type="button" data-testid="push-error" onClick={() => push({ tone: "error", message: "Something went wrong" })}>
        push error
      </button>
    </>
  );
}

function renderHost() {
  return render(
    <ToastProvider>
      <Pusher />
      <ToastHost />
    </ToastProvider>,
  );
}

afterEach(() => {
  vi.useRealTimers();
});

test("the live region is mounted before anything is pushed into it", () => {
  renderHost();

  const host = screen.getByTestId("toast-host");
  expect(host).toHaveAttribute("role", "status");
  expect(host).toHaveAttribute("aria-live", "polite");
  expect(screen.queryAllByTestId("toast")).toHaveLength(0);
});

test("a pushed toast renders its message and its tone", () => {
  renderHost();

  fireEvent.click(screen.getByTestId("push-ok"));

  const toast = screen.getByTestId("toast");
  expect(toast).toHaveAttribute("data-tone", "ok");
  expect(toast).toHaveTextContent("✓ Email logged to: Employee");
  // Inside the region, so it is announced rather than merely displayed.
  expect(screen.getByTestId("toast-host")).toContainElement(toast);
});

test("it takes itself away after its own duration, with nothing else happening", () => {
  // The whole of the no-effect claim: the timer was started by the click, so
  // advancing the clock is sufficient — no re-render, no second interaction.
  vi.useFakeTimers();
  renderHost();

  fireEvent.click(screen.getByTestId("push-ok"));
  expect(screen.getAllByTestId("toast")).toHaveLength(1);

  // One tick short, so the assertion is about *this* duration rather than
  // about "eventually".
  act(() => {
    vi.advanceTimersByTime(TOAST_DURATION_MS - 1);
  });
  expect(screen.getAllByTestId("toast")).toHaveLength(1);

  act(() => {
    vi.advanceTimersByTime(1);
  });
  expect(screen.queryAllByTestId("toast")).toHaveLength(0);
  // …and the region is still there, empty, ready for the next one.
  expect(screen.getByTestId("toast-host")).toBeInTheDocument();
});

test("the ✕ dismisses one toast without touching the others", () => {
  renderHost();

  fireEvent.click(screen.getByTestId("push-ok"));
  fireEvent.click(screen.getByTestId("push-error"));
  expect(screen.getAllByTestId("toast")).toHaveLength(2);

  fireEvent.click(screen.getAllByTestId("toast-dismiss")[0]);

  const left = screen.getAllByTestId("toast");
  expect(left).toHaveLength(1);
  expect(left[0]).toHaveAttribute("data-tone", "error");
});

test("two toasts pushed in one handler are two toasts, not one", () => {
  // The ids come from a ref rather than from `toasts.length`, which would give
  // both pushes in a single batch the same number — and React would then render
  // one of them.
  function Double() {
    const { push } = useToast();
    return (
      <button
        type="button"
        data-testid="push-two"
        onClick={() => {
          push({ tone: "ok", message: "first" });
          push({ tone: "ok", message: "second" });
        }}
      >
        push two
      </button>
    );
  }

  render(
    <ToastProvider>
      <Double />
      <ToastHost />
    </ToastProvider>,
  );

  fireEvent.click(screen.getByTestId("push-two"));

  expect(screen.getAllByTestId("toast").map((node) => node.textContent)).toEqual([
    "first✕",
    "second✕",
  ]);
});

test("the stack is capped, and it is the oldest that go", () => {
  // The host is `position: fixed` in a corner with no scroll of its own, so an
  // uncapped stack grows off the top of the viewport: several confirmations
  // inside one `TOAST_DURATION_MS` — a handler working through a list, or Epic
  // 6 replaying a batch — put the earliest messages and their ✕ out of reach of
  // a pointer. The newest survive, because a toast confirms something that has
  // already happened and the unread one is the one worth keeping.
  function Many() {
    const { push } = useToast();
    return (
      <button
        type="button"
        data-testid="push-many"
        onClick={() => {
          for (let n = 1; n <= TOAST_LIMIT + 1; n += 1) {
            push({ tone: "ok", message: `message ${n}` });
          }
        }}
      >
        push many
      </button>
    );
  }

  render(
    <ToastProvider>
      <Many />
      <ToastHost />
    </ToastProvider>,
  );

  fireEvent.click(screen.getByTestId("push-many"));

  const shown = screen.getAllByTestId("toast");
  expect(shown).toHaveLength(TOAST_LIMIT);
  expect(shown[0]).toHaveTextContent("message 2");
  expect(shown[shown.length - 1]).toHaveTextContent(`message ${TOAST_LIMIT + 1}`);
});

test("outside a provider push is a working no-op, not a throw", () => {
  // `DiaryNav`'s ruling, for its reason: `EmailsSubTab`, `MeetingCard` and the
  // composer are each rendered with no shell around them by their own files, and
  // a provider-or-throw would make those assert their way around a dependency
  // they have no interest in.
  render(
    <>
      <Pusher />
      <ToastHost />
    </>,
  );

  fireEvent.click(screen.getByTestId("push-ok"));

  expect(screen.queryAllByTestId("toast")).toHaveLength(0);
});
