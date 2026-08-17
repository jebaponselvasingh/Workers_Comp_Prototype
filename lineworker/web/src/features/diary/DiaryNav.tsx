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
 * **Focus is a counter *and* an intent, and never a `useEffect`.** "Focus the
 * add-note input" (Story 4.2, AC 4) is the same shape as opening the scheduler:
 * `requestNotes()` selects the sub-tab, bumps `noteFocusSession` and raises
 * `noteFocusPending`, and `NotesSubTab` passes the number as the React `key` on
 * the input and the flag as its `autoFocus` — so a fresh mount *is* the focus,
 * with no ref and no effect. The counter makes a *second* click on the same deep
 * link work, which a flag alone would not: the key changes again, the input
 * remounts, and the caret returns even though the handler had already navigated
 * there once. The flag makes every *other* arrival leave the caret alone, which
 * the counter alone did not: `autoFocus={noteFocusSession !== 0}` stayed true
 * for the life of the provider, so each later click on 📓 Notes stole focus out
 * of the tablist. `selectSubTab` clears it; nothing else has to.
 *
 * **The half-written note lives here too**, for a reason that is about mounting
 * rather than about focus: `DiaryTab` renders only the selected sub-tab, so a
 * draft held inside `NotesSubTab` is destroyed by a click on 📅 Meetings. See
 * `NoteDraft`, which also explains why the claim it will be tagged to is
 * captured with the first keystroke instead of read at submit.
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

/**
 * A half-written note, and the claim it will be tagged to.
 *
 * **Both members live here rather than in `NotesSubTab`**, for two separate
 * reasons that happen to want the same state.
 *
 * *The text*, because `DiaryTab` mounts only the selected sub-tab (deliberately
 * — its own test asserts it), so a `useState` inside `NotesSubTab` is destroyed
 * by a click on 📅 Meetings. Three typed sentences, one glance at the calendar,
 * and the box is empty on the way back with nothing having said so. The draft
 * outlives the sub-tab because the pane is what owns it.
 *
 * *The claim*, because it is captured when the draft **starts** rather than read
 * when it is submitted. The workspace selection can move under a note being
 * typed — "Open Claim" on a today's-meeting card, a control this very pane
 * renders, does exactly that — and a note is written into an append-only table
 * with no edit and no delete. Reading the selection at submit time writes the
 * handler's words against whichever claim they last clicked, permanently, and
 * silently satisfies that claim's weekly check-in instead of the one the note is
 * about. So the tag is frozen at the first keystroke and `NotesSubTab` shows it.
 *
 * An empty draft has no captured claim: `claimId` is `null` and the form falls
 * back to the live selection, so a handler who has not started typing sees the
 * tag follow their clicks — which is the behaviour that makes the freeze legible
 * when it happens.
 */
export interface NoteDraft {
  text: string;
  /** The claim captured at the first keystroke, or `null` for an empty draft. */
  claimId: string | null;
}

export interface DiaryNav {
  subTab: DiarySubTab;
  /**
   * Select a sub-tab **as the handler**, from the strip.
   *
   * Distinct from the deep links below in one way that matters: it clears
   * `noteFocusPending`. See that field.
   */
  selectSubTab: (tab: DiarySubTab) => void;
  schedulerOpen: boolean;
  /** Increments on every open; the scheduler is keyed on it (see above). */
  schedulerSession: number;
  openScheduler: () => void;
  closeScheduler: () => void;
  /** The centre pane's deep link: Diary → Meetings → scheduler, in one call. */
  requestMeetings: () => void;
  /**
   * Increments every time the add-note input is asked for; `NotesSubTab` keys
   * the input on it (see below).
   */
  noteFocusSession: number;
  /**
   * Whether the *current* mount of the add-note input should take focus.
   *
   * **The counter alone was not enough**, and the bug it left is worth stating
   * because the obvious fix looks like this field is redundant.
   * `autoFocus={noteFocusSession !== 0}` is true for the rest of the provider's
   * life once the deep link has been followed once — so every later click on 📓
   * Notes, and every arrow-key move onto it, remounted the textarea and yanked
   * focus out of the tablist the handler was navigating. Focus belongs to an
   * *act of asking for it*, not to a counter that has ever been non-zero, so
   * `requestNotes` sets this and `selectSubTab` clears it. Effect-free in both
   * directions: the counter still forces the remount, this decides whether the
   * mount grabs the caret.
   */
  noteFocusPending: boolean;
  /**
   * Lower the intent — called by the control that **takes** the caret.
   *
   * `selectSubTab` clearing it was the whole lifetime, and it is not the whole
   * lifetime: any route out of Notes that is not the strip left the flag raised,
   * so the next arrival stole focus again. Unreachable while the strip is the
   * only two-way switch; Epic 6 makes the copilot tabs one, and the theft comes
   * back with it. `NotesSubTab` calls this from the input's `onFocus`, which is
   * the moment the intent is actually satisfied — an event, not an effect.
   */
  lowerNoteFocus: () => void;
  /** The centre pane's other deep link: Diary → Notes, input focused. */
  requestNotes: () => void;
  /** The half-written note, kept across sub-tab switches. See `NoteDraft`. */
  noteDraft: NoteDraft;
  /**
   * Type into the draft, capturing `claimId` if this is the first keystroke.
   *
   * The current selection is a *parameter* rather than something this provider
   * reads, because the provider sits above the workspace and has no business
   * knowing which claim is open — `NotesSubTab` has it as a prop and passes it
   * with every keystroke. Which of the two is kept is the rule this function
   * owns, and it is one line so that it is one line in one place.
   */
  typeNoteDraft: (text: string, claimId: string | null) => void;
  /** Empty the draft and release its captured claim — after a save. */
  clearNoteDraft: () => void;
}

/**
 * Notes is what the pane opens on, which changed in Story 4.2.
 *
 * 4.1 opened on Meetings because it was the only sub-tab with anything in it.
 * The Notes tab is first in the strip, it carries the greeting and today's
 * meetings, and it is what the prototype's diary pane shows on arrival — so it
 * is the landing surface now that it exists.
 */
const INITIAL_SUB_TAB: DiarySubTab = "notes";

/** An untouched draft — the value the provider starts from and returns to. */
const EMPTY_NOTE_DRAFT: NoteDraft = { text: "", claimId: null };

const NO_DIARY_PANE: DiaryNav = {
  subTab: INITIAL_SUB_TAB,
  selectSubTab: () => {},
  schedulerOpen: false,
  schedulerSession: 0,
  openScheduler: () => {},
  closeScheduler: () => {},
  requestMeetings: () => {},
  noteFocusSession: 0,
  noteFocusPending: false,
  lowerNoteFocus: () => {},
  requestNotes: () => {},
  noteDraft: EMPTY_NOTE_DRAFT,
  typeNoteDraft: () => {},
  clearNoteDraft: () => {},
};

const DiaryNavContext = createContext<DiaryNav>(NO_DIARY_PANE);

export function DiaryNavProvider({ children }: { children: React.ReactNode }) {
  const [subTab, setSubTab] = useState<DiarySubTab>(INITIAL_SUB_TAB);
  const [schedulerOpen, setSchedulerOpen] = useState(false);
  const [schedulerSession, setSchedulerSession] = useState(0);
  const [noteFocusSession, setNoteFocusSession] = useState(0);
  const [noteFocusPending, setNoteFocusPending] = useState(false);
  const [noteDraft, setNoteDraft] = useState<NoteDraft>(EMPTY_NOTE_DRAFT);

  const selectSubTab = useCallback((tab: DiarySubTab) => {
    setSubTab(tab);
    // The handler moved the selection themselves, so nobody is asking for the
    // caret — see `noteFocusPending`. Clearing it here rather than in
    // `NotesSubTab` keeps the intent's whole lifetime in one file: it is set by
    // exactly one call and cleared by exactly one.
    setNoteFocusPending(false);
  }, []);

  const typeNoteDraft = useCallback((text: string, claimId: string | null) => {
    setNoteDraft((draft) =>
      // The first keystroke captures; every later one keeps what was captured.
      // Emptying the box releases it, so the next note starts fresh against
      // whatever is selected then. See `NoteDraft`.
      draft.text === "" ? { text, claimId } : { text, claimId: draft.claimId },
    );
  }, []);

  const clearNoteDraft = useCallback(() => setNoteDraft(EMPTY_NOTE_DRAFT), []);
  const lowerNoteFocus = useCallback(() => setNoteFocusPending(false), []);

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
    //
    // **Through `selectSubTab`, not `setSubTab`.** It was the direct setter,
    // which meant this path left `noteFocusPending` raised — leave Notes by the
    // meetings deep link and come back, and the input took the caret again for
    // an intent nobody had expressed. One of the two sub-tab movers clearing the
    // flag and the other not is exactly the asymmetry that makes a state
    // machine wrong in one place only.
    selectSubTab("meetings");
    openScheduler();
  }, [openScheduler, selectSubTab]);
  const requestNotes = useCallback(() => {
    // The sub-tab first, for `requestMeetings`' reason exactly: the add-note
    // input is rendered by `NotesSubTab`, so bumping the session while
    // Meetings is showing would increment a counter nothing is mounted to
    // read — and the input would then mount *unfocused* when the handler
    // later clicked the tab themselves. Both calls are in one event handler,
    // so React batches them into a single render and the input mounts once,
    // already focused.
    setSubTab("notes");
    setNoteFocusSession((session) => session + 1);
    // The intent, which is what `autoFocus` actually reads. The counter forces
    // the remount; this says the mount may take the caret. See
    // `noteFocusPending` on the bug that separating them fixes.
    setNoteFocusPending(true);
  }, []);

  // Memoised so the panes below do not re-render on every shell render — the
  // value is otherwise a fresh object each time, and this provider sits above
  // the whole workspace.
  const value = useMemo<DiaryNav>(
    () => ({
      subTab,
      selectSubTab,
      schedulerOpen,
      schedulerSession,
      openScheduler,
      closeScheduler,
      requestMeetings,
      noteFocusSession,
      noteFocusPending,
      lowerNoteFocus,
      requestNotes,
      noteDraft,
      typeNoteDraft,
      clearNoteDraft,
    }),
    [
      subTab,
      selectSubTab,
      schedulerOpen,
      schedulerSession,
      openScheduler,
      closeScheduler,
      requestMeetings,
      noteFocusSession,
      noteFocusPending,
      lowerNoteFocus,
      requestNotes,
      noteDraft,
      typeNoteDraft,
      clearNoteDraft,
    ],
  );

  return <DiaryNavContext.Provider value={value}>{children}</DiaryNavContext.Provider>;
}

export function useDiaryNav(): DiaryNav {
  return useContext(DiaryNavContext);
}
