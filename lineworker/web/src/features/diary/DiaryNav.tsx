/**
 * The diary pane's navigation state, and the cross-pane deep link into it
 * (Story 4.1, AC 5 / Story 3.5's `meetings` target).
 *
 * Story 3.5's action checklist lives inside `ClaimDetailPane`, and the surface
 * its `meetings` row points at is the copilot `<aside>` two components over.
 * There is no common ancestor except `WorkspaceShell`, and threading a
 * callback down through the detail pane, the tab bar, the overview variant and
 * the card would put a prop about the *right* pane on five components that
 * render the centre one.
 *
 * So it is React context — local UI state, which is exactly what the epic's
 * state discipline says context is for (server state goes through TanStack
 * Query and nothing else).
 *
 * **The context holds the state itself, not an intent to change it**, and that
 * is the design decision worth stating. The obvious shape is an event —
 * "somebody asked for the scheduler" — which each consumer then reacts to in a
 * `useEffect` that calls `setState`. That is precisely the cascading-render
 * pattern `react-hooks/set-state-in-effect` refuses, and rightly: it makes one
 * click render the pane three times and leaves three components each holding a
 * private copy of where the diary is. Owning `subTab` and `schedulerOpen` here
 * means the deep link is two setter calls inside one event handler, and there
 * is no effect anywhere in the feature.
 *
 * **`schedulerSession` is a counter, and it is what makes the modal reusable.**
 * The scheduler seeds its draft — today's date, 10:00, Employee checked — when
 * it mounts. Keying it on this number makes every open a fresh mount, so a
 * handler who cancels halfway through and re-opens gets an empty form rather
 * than yesterday's half-typed agenda. The prototype clears only `notes` and
 * `location` after a save (line 1878) and pre-fills the next meeting from the
 * last one; this is that bug not being ported.
 *
 * **The default value is a working no-op rather than a throw.**
 * `ClaimDetailPane` and `ActionsCard` are rendered directly by their own
 * vitest files, outside any shell; a provider-or-throw would make those tests
 * assert their way around a dependency they have no interest in. That is the
 * whole reason, and it is a statement about tests rather than about layout:
 * inside the shell the provider is always mounted, at every viewport. Below
 * the `xl` breakpoint the copilot aside is `hidden` — `display: none`, still
 * mounted — so `MeetingsSubTab` is alive behind it and the scheduler it
 * renders portals to `document.body`. The deep link therefore opens a real,
 * usable modal over a diary list nobody can see; it is not a no-op there, and
 * an earlier draft of this comment claimed it was.
 */
import { createContext, useCallback, useContext, useMemo, useState } from "react";

export type DiarySubTab = "notes" | "meetings" | "emails";

export interface DiaryNav {
  subTab: DiarySubTab;
  selectSubTab: (tab: DiarySubTab) => void;
  schedulerOpen: boolean;
  /** Increments on every open; the scheduler is keyed on it (see above). */
  schedulerSession: number;
  openScheduler: () => void;
  closeScheduler: () => void;
  /** The centre pane's deep link: Diary → Meetings → scheduler, in one call. */
  requestMeetings: () => void;
}

/** Meetings is the sub-tab this story builds, so it is the one that opens. */
const INITIAL_SUB_TAB: DiarySubTab = "meetings";

const NO_DIARY_PANE: DiaryNav = {
  subTab: INITIAL_SUB_TAB,
  selectSubTab: () => {},
  schedulerOpen: false,
  schedulerSession: 0,
  openScheduler: () => {},
  closeScheduler: () => {},
  requestMeetings: () => {},
};

const DiaryNavContext = createContext<DiaryNav>(NO_DIARY_PANE);

export function DiaryNavProvider({ children }: { children: React.ReactNode }) {
  const [subTab, setSubTab] = useState<DiarySubTab>(INITIAL_SUB_TAB);
  const [schedulerOpen, setSchedulerOpen] = useState(false);
  const [schedulerSession, setSchedulerSession] = useState(0);

  const openScheduler = useCallback(() => {
    setSchedulerSession((session) => session + 1);
    setSchedulerOpen(true);
  }, []);
  const closeScheduler = useCallback(() => setSchedulerOpen(false), []);
  const requestMeetings = useCallback(() => {
    // The sub-tab first: the scheduler is rendered by `MeetingsSubTab`, so
    // opening it while Notes is showing would set a flag nothing is mounted to
    // read. Both calls are in one event handler, so React batches them into a
    // single render.
    setSubTab("meetings");
    openScheduler();
  }, [openScheduler]);

  // Memoised so the panes below do not re-render on every shell render — the
  // value is otherwise a fresh object each time, and this provider sits above
  // the whole workspace.
  const value = useMemo<DiaryNav>(
    () => ({
      subTab,
      selectSubTab: setSubTab,
      schedulerOpen,
      schedulerSession,
      openScheduler,
      closeScheduler,
      requestMeetings,
    }),
    [subTab, schedulerOpen, schedulerSession, openScheduler, closeScheduler, requestMeetings],
  );

  return <DiaryNavContext.Provider value={value}>{children}</DiaryNavContext.Provider>;
}

export function useDiaryNav(): DiaryNav {
  return useContext(DiaryNavContext);
}
