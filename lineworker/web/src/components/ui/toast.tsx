/**
 * The non-blocking confirmation primitive (Story 4.3, NFR-3, UX-DR11).
 *
 * Three specs deferred this — 3.4 wrote it down first ("the primitive belongs
 * to the first story owning *two* surfaces that need one"), 4.1 owned one
 * surface, 4.2 owned one — and 4.3 is where the second arrives with the email
 * send. It is deliberately small, and the three decisions worth stating are why.
 *
 * **No `useEffect`, anywhere.** A toast that scheduled its own dismissal from
 * an effect is the cascading `setState`-in-effect shape
 * `react-hooks/set-state-in-effect` refuses, and `features/diary` has stayed
 * effect-free through two whole stories. `push()` is only ever called *from an
 * event handler*, so it schedules its own `setTimeout(() => dismiss(id),
 * TOAST_DURATION_MS)` right there: the timer is started by the same click that
 * created the toast, which is also the only honest moment to start it.
 *
 * **No dependency.** `radix-ui`'s Toast brings focus management, swipe
 * gestures and a viewport with hotkey semantics — none of which this needs, and
 * all of which would have to be reasoned about. The house already hand-rolls
 * its live regions (`MeetingsSubTab`, `NotesSubTab`, `ActionsCard`), and this is
 * that pattern with a queue and a ✕ in front of it.
 *
 * **The host is always mounted, and it is one element.** A `role="status"` that
 * appears at the same moment as its content is frequently not announced at all
 * — Story 3.5's note, and the reason every live region in this codebase is
 * rendered empty rather than conditionally. So `ToastHost` renders its region
 * unconditionally and the toasts go inside it.
 *
 * **Only successes come here.** A refusal stays inline at the control that
 * caused it, with `role="alert"`, `aria-invalid` and `aria-describedby` — a
 * message that disappears after a few seconds is a worse answer than one that
 * stays where the handler has to fix something. That rule lives with the
 * callers; what this module guarantees is merely that it is polite and
 * dismissible.
 *
 * **The two shipped surfaces are deliberately not migrated.** `MeetingsSubTab`
 * and `NotesSubTab` keep their `sr-only` `role="status"` regions exactly as they
 * are: rewiring them would churn a dozen passing tests for no behaviour change,
 * and the host below announces on the same channel. Recorded as follow-up.
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

/**
 * How long a toast stays before it takes itself away.
 *
 * Here rather than in the feature that pushes one, for `fieldLimits.ts`'s
 * reason: `features/queue/noDerivation.test.ts` scans `features/diary` and
 * refuses a named numeric constant there, bluntly and on purpose. `components/ui`
 * is outside every scanned root, which is also where the number belongs on the
 * merits — it is a property of the primitive, not of any one message.
 */
export const TOAST_DURATION_MS = 5000;

/**
 * How many toasts the stack will show at once.
 *
 * `push` appended without a cap, and the host is `position: fixed` in a corner
 * with no scroll of its own: several confirmations inside one `TOAST_DURATION_MS`
 * — a handler ticking meetings done, or Epic 6 replaying a batch — grew a column
 * that ran off the top of the viewport, taking the *oldest* messages out of
 * reach of a pointer and leaving their ✕ unclickable. Four is what fits above
 * the fold at this width; the number is here rather than at any caller because
 * it is a property of the stack, not of any one message.
 *
 * The **oldest** are dropped rather than the newest refused: a toast confirms
 * something that has already happened, so the one worth keeping is the one the
 * handler has not read yet. Each dropped toast's timer still fires later and
 * `dismiss` is idempotent, so nothing leaks.
 */
export const TOAST_LIMIT = 4;

/** Success or failure, which is all the styling this needs to tell apart. */
export type ToastTone = "ok" | "error";

export interface Toast {
  /** Monotonic within a provider; never a business id. */
  id: number;
  tone: ToastTone;
  message: string;
}

export interface ToastApi {
  toasts: readonly Toast[];
  /** Show a message, and schedule its own removal. Call from an event handler. */
  push: (toast: { tone: ToastTone; message: string }) => void;
  /** Take one away now — the ✕, and nothing else. */
  dismiss: (id: number) => void;
}

/**
 * A working no-op outside a provider, never a throw.
 *
 * `DiaryNav`'s ruling, for its reason: `EmailsSubTab`, `MeetingCard` and the
 * composer are each rendered directly by their own vitest files with no shell
 * around them, and a provider-or-throw would make those files assert their way
 * around a dependency they have no interest in. Inside the app the provider is
 * mounted once, in `App.tsx`, above every route.
 */
const NO_TOASTS: ToastApi = {
  toasts: [],
  push: () => {},
  dismiss: () => {},
};

const ToastContext = createContext<ToastApi>(NO_TOASTS);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<readonly Toast[]>([]);
  // A ref rather than a counter in state: the id is not rendered and nothing
  // re-renders because it moved, and two toasts pushed inside one event handler
  // must not receive the same number (a `toasts.length` would).
  const lastId = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (toast: { tone: ToastTone; message: string }) => {
      lastId.current += 1;
      const id = lastId.current;
      // Capped at the tail, so the newest survive — see `TOAST_LIMIT`.
      setToasts((current) => [...current, { ...toast, id }].slice(-TOAST_LIMIT));
      // **Scheduled here, from the handler that pushed it.** See the module
      // docstring: this is what buys the whole primitive its no-effect
      // discipline. `dismiss` is idempotent, so a ✕ before the timer fires and
      // the timer arriving after an unmount are both harmless.
      setTimeout(() => dismiss(id), TOAST_DURATION_MS);
    },
    [dismiss],
  );

  const value = useMemo<ToastApi>(() => ({ toasts, push, dismiss }), [toasts, push, dismiss]);

  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>;
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

/**
 * The one stack on screen, mounted once by `App.tsx`.
 *
 * Fixed to the bottom-right corner above everything, including the dialogs
 * (`DialogContent` is `z-50`), because the send that raises a toast also closes
 * the modal — and on the frame where the two overlap the confirmation must not
 * be behind the thing it is confirming.
 */
export function ToastHost() {
  const { toasts, dismiss } = useToast();

  return (
    <div
      // Always mounted, and the toasts go *inside* it — see the docstring.
      role="status"
      aria-live="polite"
      data-testid="toast-host"
      className="pointer-events-none fixed right-[14px] bottom-[14px] z-[60] flex w-[300px] flex-col gap-[6px]"
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          data-testid="toast"
          data-tone={toast.tone}
          className={`pointer-events-auto flex items-start gap-[8px] rounded border p-[8px_10px] text-[11.5px] font-semibold shadow-lg ${
            toast.tone === "ok"
              ? "border-ok/40 bg-ok-soft text-ok"
              : "border-error/40 bg-error-soft text-error"
          }`}
        >
          <span data-testid="toast-message" className="flex-1">
            {toast.message}
          </span>
          <button
            type="button"
            data-testid="toast-dismiss"
            aria-label="Dismiss notification"
            onClick={() => dismiss(toast.id)}
            className="text-[12px] leading-none opacity-70 hover:opacity-100"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
