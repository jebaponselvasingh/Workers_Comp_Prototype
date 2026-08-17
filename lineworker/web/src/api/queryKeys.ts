/**
 * Every TanStack Query key in the app, declared here and nowhere else (AD-9).
 *
 * The reason this module exists rather than inline key arrays: mutations
 * have to invalidate exactly the keys that hold the affected entity, and
 * that is only checkable if the keys are enumerable in one place. Keys are
 * entity + business id — `claim("WC-1042")`, not `["claim", index]`.
 *
 * Story 1.3 starts it with the two identity keys; later stories add
 * siblings here as they add screens.
 *
 * Note what the stats key does *not* carry: no persona, no employer, no
 * scope. The server answers `/stats/topbar` for whoever holds the session
 * cookie (AD-7), so putting an identity in the key would imply the client
 * chooses whose numbers it sees. Switching personas clears the whole cache
 * (`useLogout`), which is what keeps one persona's tiles out of the next
 * one's shell.
 */
export const queryKeys = {
  me: ["me"] as const,
  personas: ["personas"] as const,
  stats: {
    topbar: ["stats", "topbar"] as const,
    sla: ["stats", "sla"] as const,
  },
  // Reference data (Story 1.6): one key with nothing under it, because
  // there is one glossary. No search term in the key either — the panel
  // filters the cached list in the browser (the sanctioned AD-7 exception
  // for scope-free, PHI-free reference data), so a query per keystroke
  // would be a cache entry per keystroke for the same 25 rows.
  glossary: ["glossary"] as const,
  claims: {
    /**
     * The queue, keyed by filter (Story 2.1).
     *
     * The filter *is* in the key, unlike the glossary's search term, and
     * for the opposite reason: a filtered queue is a different server
     * answer, computed under the caller's scope over data the client does
     * not hold in full (AD-1 forbids re-filtering a cached superset). Two
     * filters are two resources, so they are two cache entries — and
     * switching back to one already fetched is instant, which is the
     * behaviour a handler flipping between "All" and "Litigation" expects.
     *
     * Still no scope in the key, for the reason the stats keys record: the
     * server answers for whoever holds the cookie, and putting an identity
     * here would imply the client chooses whose caseload it sees.
     */
    queue: (filter: string) => ["claims", "queue", filter] as const,
    /**
     * Every queue entry, whatever its filter or page — the prefix a
     * mutation invalidates (Story 2.3).
     *
     * Declared rather than spelled inline at the mutation, because it is
     * exactly the kind of key that goes wrong silently: invalidating
     * `queue(filter)` would refresh the filter the handler happens to be
     * looking at and leave every other cached filter — and every "Show
     * more" page under it — showing the injury type they just corrected.
     * TanStack matches keys by prefix, so this one covers both.
     */
    queues: ["claims", "queue"] as const,
    /**
     * The pages a stage group has been expanded through ("Show more").
     *
     * Keyed by filter, stage, and **the cursor the accumulation starts
     * from** — but not by the cursors after it. Those are the infinite
     * query's own page params and live inside the entry; putting them in
     * the key would make every page its own cache entry and lose the
     * accumulated list on the way back.
     *
     * The *first* cursor is different in kind: it is not a page param the
     * query produced, it is an input handed in from the base queue query,
     * and it encodes the offset the accumulation is anchored at. When the
     * base query refetches and the group has changed underneath it, the
     * first cursor changes — and pages accumulated from the old one
     * describe a list that no longer starts where they think it does. In
     * the key, that is a different entry and the group reloads; out of it,
     * page 1 and page 2 came from two different orderings and nothing
     * anywhere would say so.
     */
    queuePages: (filter: string, stage: string, firstCursor: string | null) =>
      ["claims", "queue", filter, "pages", stage, firstCursor] as const,
    /**
     * One claim's case file (Story 2.2), keyed by its business id.
     *
     * Entity + business id, which is the rule this module opens with: Story
     * 2.3's inline edits invalidate exactly this key plus the queue's, so
     * the header, the gauge and the card that names the same claim move
     * together. Keying on an array index or on the selected-claim state
     * would leave a mutation with nothing precise to invalidate.
     *
     * **Not keyed by filter**, unlike the queue. A case file is the same
     * case file whichever filter the handler happened to arrive through —
     * putting the filter in the key would fetch the identical payload again
     * every time they flipped the queue's dropdown.
     */
    detail: (claimId: string) => ["claims", "detail", claimId] as const,
    /**
     * One document's viewer sheet (Story 2.5), keyed under its claim.
     *
     * Nested beneath the claim's own segment rather than keyed on the
     * document id alone, because that is what makes it invalidatable *with*
     * the claim: the sheet is assembled from claim columns (the injury type
     * and ICD-10 on a FROI are 2.3's editable fields), so an edit that
     * changes the case file changes every sheet cut from it. A flat
     * `["document", id]` key would leave a previously-opened viewer showing
     * the pre-edit values with nothing able to reach it.
     *
     * The **surrogate** document id, not a business one: documents have no
     * business identifier, and the row is addressed by the same `id` the
     * payload publishes.
     */
    documentSheet: (claimId: string, documentId: number) =>
      ["claims", "detail", claimId, "document", documentId] as const,
    /**
     * One claim's Bills & Payments read model (Story 3.3).
     *
     * Nested under the claim's own segment, `documentSheet`'s arrangement and
     * for the same reason: this payload is cut from the claim's rows, so an
     * edit that invalidates the case file has to be able to reach it. A flat
     * `["financials", id]` key would leave the Bills tab showing figures from
     * before a comp-rate override while the card two tabs over showed the new
     * weekly benefit.
     *
     * **A separate entry from `detail` rather than a field on it.** The tab is
     * a schedule, two line-item lists and their totals — far more than the
     * Overview card needs, on a payload the console fetches for every claim a
     * handler clicks. Splitting it means the cost is paid by the tab that
     * shows it. The two cannot disagree despite being two entries, because the
     * server computes both from one assembler over one set of rows; see
     * `useClaimFinancials`.
     */
    financials: (claimId: string) => ["claims", "detail", claimId, "financials"] as const,
    /**
     * One claim's auto-generated action checklist (Story 3.5).
     *
     * Nested under the claim's own segment, `financials`' arrangement and for
     * the same reason: the list is generated from the claim's columns, its
     * documents and its bills, so anything that invalidates the case file has
     * to be able to reach it. A flat `["actions", id]` key would leave the card
     * offering "Approve the claim assessment" on a claim somebody had just
     * approved through a different surface.
     *
     * **A separate entry from `detail` rather than a field on it**, which is
     * `financials`' call again: the checklist costs three child reads and two
     * rule-document loads, and the case file is the console's most-fetched
     * payload — so the two are refetched on their own schedules, and a
     * completion re-reads the list without re-reading the whole case file.
     * It is **not** a lazy-loading split, and an earlier version of this
     * comment claimed it was (code review, 2026-08-17): `ActionsCard` mounts
     * with every Overview variant and `useClaimActions` is gated only on the
     * claim id, so the request goes out on every case-file open. Anyone
     * optimising later should read that as an opportunity, not as a promise
     * the code already keeps.
     *
     * The three completion mutations invalidate this key **exactly** — see
     * `afterChecklistWrite`, and `useApprovePayment`'s comment for the defect
     * that taught us prefix matching is not what a mutation wants here.
     */
    actions: (claimId: string) => ["claims", "detail", claimId, "actions"] as const,
    /**
     * The **mutation** key every write against one claim carries (Story 2.4).
     *
     * Not a query key: nothing is cached under it. It exists so that
     * `useIsMutating` can answer "is any command against this claim in
     * flight?" across the four hooks that write one — the field patch, the
     * severity score, and the add and remove of a secondary injury.
     *
     * That question has to be answerable in one place because of how
     * `expectedVersion` works: every one of those hooks reads it from the
     * same cached case file and none of them advances it optimistically, so
     * a second commit launched before the first settles carries a version
     * the first has already consumed. The server answers 409 and the handler
     * is told somebody else changed the claim — about their own edit.
     * Story 2.3 fixed that *within* one hook by disabling its inputs while it
     * was busy; Story 2.4 put three more hooks on the same surface, and four
     * independent `isPending` flags is the same bug with more moving parts.
     */
    writes: (claimId: string) => ["claims", "write", claimId] as const,
  },
  /**
   * The handler's diary (Story 4.1) — **a top-level group, not a child of
   * `claims`**, and that placement is the whole reason this comment is long.
   *
   * `GET /claims-diary/meetings` is scoped to the *caller*, not to a claim: a
   * meeting belongs to the handler who holds it and merely references a case
   * file. So the list is not a child read-model of `claims.detail` the way
   * `financials` and `actions` are, and nesting it under a claim's segment
   * would make an unrelated case-file edit invalidate the whole diary.
   *
   * **The mutation key is this group's own, and using `claims.writes` here
   * would be a bug with a visible symptom.** That key drives
   * `useClaimWriteInFlight`, which disables every editable control on the case
   * file while a command is in flight — because those controls all read
   * `expectedVersion` out of one cached case file. A meeting write touches no
   * column of `claim` and bumps no claim version, so carrying that key would
   * grey out the severity score and the comp-rate input while somebody ticked
   * a meeting done, for no reason anyone could see.
   */
  meetings: {
    /**
     * The caller's meetings, one page at a time.
     *
     * No cursor in the key, deliberately: the cursor is a *page param* the
     * infinite query keeps inside its entry, and putting it in the key would
     * make every page its own cache entry and lose the accumulated list on the
     * way back — `queuePages`' lesson, in the case where there is no separate
     * first-page query to anchor against.
     *
     * No persona in it either, for the reason the stats keys record: the server
     * answers for whoever holds the cookie (AD-7), and switching personas
     * clears the whole cache.
     */
    list: ["meetings", "list"] as const,
    /**
     * One day of the caller's meetings — the Notes sub-tab's summary (4.2).
     *
     * **A sibling under `list`'s own prefix, and the nesting is the whole
     * point.** The summary and the Meetings sub-tab read the same rows through
     * the same endpoint, so ✓ Done from one has to refresh the other; making
     * this `["meetings", "list", day]` means `["meetings", "list"]` is a prefix
     * of it and one invalidation covers both. A flat `["meetings", "day", d]`
     * would have needed two, and the one somebody forgot is the stale list.
     *
     * The `day` *is* in the key, unlike the glossary's search term and like the
     * queue's filter: a different day is a different server answer computed
     * under the caller's scope (AD-1 forbids re-filtering a cached superset),
     * and the browser must not be the thing that decides which rows fall on it.
     *
     * It is the **viewer's local** day, from `lib/clock.ts::todayIso`. The key
     * therefore changes on the **first render after** local midnight — not at
     * midnight itself. `NotesSubTab` reads `new Date()` during render, so a pane
     * left open across midnight keeps yesterday's key and yesterday's summary
     * until something re-renders it, which may be hours. That is a real (if
     * quiet) staleness and it is recorded here rather than papered over: an
     * earlier version of this comment said the key "rolls over at local
     * midnight", which described a timer nothing schedules. A midnight timer is
     * deliberately not built — see `deferred-work.md`.
     */
    day: (day: string) => ["meetings", "list", day] as const,
    /**
     * The **mutation** key every meeting command carries. Nothing is cached
     * under it; `useIsMutating` counts it so the scheduler and each card can
     * disable their controls while any write is in flight — the same guarantee
     * `claims.writes` gives the case file, over a different aggregate.
     */
    writes: ["meetings", "write"] as const,
  },
  /**
   * The handler's diary notes (Story 4.2) — **a top-level group beside
   * `meetings`, not a child of `claims`**, for the reason `meetings` records.
   *
   * `GET /claims-diary/notes` is scoped to the *caller*: a note belongs to the
   * handler who wrote it and merely tags a case file, and the list is not
   * filtered by the selected claim at all. Nesting it under a claim's segment
   * would make an unrelated case-file edit invalidate the whole diary — and
   * would imply a per-claim read model that does not exist.
   *
   * **`writes` is this group's own, and using `claims.writes` here would be a
   * bug with a visible symptom.** That key drives `useClaimWriteInFlight`,
   * which disables every editable control on the case file while a command is
   * in flight, because those controls all read `expectedVersion` out of one
   * cached case file. Saving a note touches no column of `claim` and bumps no
   * version — there is not even a version on `diary_note` — so carrying that
   * key would grey out the severity score and the comp-rate input while
   * somebody typed a diary entry, for no reason anyone could see.
   *
   * It is not `meetings.writes` either: `useMeetingWriteInFlight` disables the
   * ✓ Done buttons in the today's-meetings summary, and a note being saved is
   * not a reason a handler cannot tick a meeting off.
   */
  diaryNotes: {
    /** The caller's notes, newest first, one page at a time. No cursor in the
     * key — `meetings.list`'s rule and its reason. */
    list: ["diaryNotes", "list"] as const,
    /** The **mutation** key the add-note command carries. Nothing is cached
     * under it; `useIsMutating` counts it so the input and its Save disable
     * themselves while the write is in flight. */
    writes: ["diaryNotes", "write"] as const,
  },
} as const;
