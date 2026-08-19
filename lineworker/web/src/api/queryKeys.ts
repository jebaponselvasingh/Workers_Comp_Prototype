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
  /**
   * The supervisor/analyst portfolio dashboard (Story 5.1).
   *
   * One key with nothing under it, because there is one summary: the ten KPI
   * figures, the dataset chip's counts and the two thresholds behind the
   * captions all arrive in a single response.
   *
   * **No persona and no scope segment**, for the reason the stats keys record
   * at length: the server answers `/dashboard/summary` for whoever holds the
   * session cookie (AD-7), so putting an identity here would imply the client
   * chooses whose portfolio it sees. Switching personas clears the whole cache
   * (`useLogout`), which is what keeps David Bline's hundred claims out of
   * Jennifer Park's dashboard.
   *
   * A `dashboard` group rather than a `stats.dashboard` sibling: the top bar's
   * two keys are *caseload* aggregates shared by every role, and this is a
   * portfolio aggregate for one route. Epic 5's later stories (handler
   * benchmarking, the charts, the top-30 worklist) are separate server answers
   * with separate costs, so they become siblings here rather than fields on
   * this one.
   */
  dashboard: {
    summary: ["dashboard", "summary"] as const,
    /**
     * The handler performance table (Story 5.2) — the sibling the group's
     * docstring above pre-authorised.
     *
     * Its own key rather than a field on `summary` for the reason recorded
     * there: it is a separate server answer with a separate cost (a scoped read
     * grouped by handler, against ten counted columns), and it fails and
     * refetches on its own. That separation is load-bearing on the page as well
     * as in the cache — `DashboardPage` renders the two behind independent
     * states so a benchmark failure leaves the KPI cards standing.
     *
     * No persona and no role segment, although this endpoint *does* gate on
     * role: a 403 is the absence of an answer rather than a different answer,
     * and a handler never reaches this route. Putting a role here would imply
     * the client picks which version of the table it sees.
     */
    handlerBenchmarks: ["dashboard", "handler-benchmarks"] as const,
    /**
     * The seven analytics surfaces (Story 5.3) — the third sibling the group's
     * docstring above pre-authorised.
     *
     * One key for all seven, matching the one endpoint behind them: the six
     * distributions and the SLA strip are folded from one scoped read in one
     * pass, and splitting them into seven cache entries would mean seven
     * requests over the same hundred rows, seven loading states, and a
     * dashboard that could render three charts describing slightly different
     * sets if a claim changed in between.
     *
     * Its own key rather than a field on `summary` for that key's recorded
     * reason: it is a separate server answer with a separate cost, and it
     * fails and refetches on its own. The page renders the three sections
     * behind independent states so a chart outage leaves the KPI cards and the
     * handler table standing.
     */
    charts: ["dashboard", "charts"] as const,
    /**
     * The top-30 priority worklist (Story 5.4) — the fourth and last sibling
     * the group's docstring above pre-authorised.
     *
     * Its own key rather than a field on any of the three, for their recorded
     * reason: it is a separate server answer with a separate cost (a scoped
     * read of the whole book, ranked, plus four bulk child reads and an action
     * generated per row), and it fails and refetches on its own. The page
     * renders four sections behind four independent states, so a worklist
     * outage leaves the KPI cards, the handler table and the charts standing.
     *
     * No persona and no role segment, although this payload names a handler in
     * every row: the server answers for whoever holds the session cookie
     * (AD-7), and putting an identity here would imply the client picks whose
     * worklist it sees.
     */
    priorityClaims: ["dashboard", "priority-claims"] as const,
    /**
     * The pages the worklist has been walked through ("Show more").
     *
     * `claims.queuePages`' shape and its whole argument, on a list that is not
     * grouped: keyed by **the cursor the accumulation starts from**, but not by
     * the cursors after it. Those are the infinite query's own page params and
     * live inside the entry; putting them in the key would make every page its
     * own cache entry and lose the accumulated rows on the way back.
     *
     * The *first* cursor is different in kind: it is not a page param the query
     * produced, it is an input handed in from the base query, and it encodes
     * both the offset the accumulation is anchored at and the three rule
     * versions that ranked it. When the base query refetches and the worklist
     * has changed underneath it — a claim left treatment, a rule document was
     * superseded — the first cursor changes, and pages accumulated from the old
     * one describe a list that no longer starts where they think it does. In
     * the key, that is a different entry and the table reloads; out of it, page
     * 1 and page 2 came from two different rankings and nothing anywhere would
     * say so.
     */
    priorityClaimPages: (firstCursor: string | null) =>
      ["dashboard", "priority-claims", "pages", firstCursor] as const,
    /**
     * A drill-through list, keyed by **the filter set that produced it**
     * (Story 5.5).
     *
     * The filter is in the key for `claims.queue`'s reason, one surface up: a
     * filtered list is a different server answer, computed under the caller's
     * scope over data the client does not hold in full, so two filter sets are
     * two resources and therefore two cache entries. Going back to a filter
     * already fetched is then instant, which is what a supervisor clearing a
     * chip and re-applying it expects.
     *
     * It takes the *serialised* filter set rather than the object, because a
     * TanStack key is compared structurally and a fresh object literal per
     * render would be a fresh key per render. `toFilterKey` in
     * `features/dashboard/drill/filters.ts` is the one place that serialisation
     * happens, so the key, the URL and the request cannot disagree about which
     * facets are set.
     *
     * No persona and no scope segment, for the group's recorded reason: the
     * server answers `/dashboard/claims` for whoever holds the session cookie
     * (AD-7), and `filter[employerId]` narrows that answer rather than choosing
     * whose it is.
     */
    drillClaims: (filterKey: string) => ["dashboard", "claims", filterKey] as const,
    /**
     * The pages a drill-through list has been walked through ("Show more").
     *
     * `priorityClaimPages`' shape and its whole argument, with the filter set
     * added ahead of the cursor: keyed by the accumulation's **first** cursor
     * but not by the ones after it, which are the infinite query's own page
     * params and live inside the entry.
     *
     * Both parts are load-bearing. The first cursor encodes the offset the
     * accumulation is anchored at *and* the two rule versions that ranked it,
     * so a base query that refetched onto a new one must not hand its pages to
     * the old accumulation. The filter set is beside it because the server
     * refuses a cursor replayed under different filters — a cache entry shared
     * across two filter sets would be pages the server would 400 if they were
     * ever requested again.
     */
    drillClaimPages: (filterKey: string, firstCursor: string | null) =>
      ["dashboard", "claims", filterKey, "pages", firstCursor] as const,
  },
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
  /**
   * The handler's stakeholder emails (Story 4.3) — **a top-level group beside
   * `meetings` and `diaryNotes`, never under `claims`**, for the reason those
   * two record.
   *
   * `GET /claims-diary/emails` is scoped to the *sender*: two handlers whose
   * books overlap read their own correspondence and not each other's, and the
   * list is not filtered by the selected claim at all. Nesting it under a
   * claim's segment would make an unrelated case-file edit invalidate the whole
   * sent log — and would imply a per-claim read model the server does not have.
   *
   * **`writes` is this group's own**, and reusing `claims.writes` would grey out
   * every editable control on the case file while somebody logged an email that
   * bumps no claim version. It is not `meetings.writes` either: sending an email
   * is not a reason a handler cannot tick a meeting done.
   */
  emails: {
    /**
     * The six quick templates — reference data, one key with nothing under it.
     *
     * No claim in the key, deliberately: the row of buttons is the same six for
     * every caller and every claim. The *merged* text is a different resource
     * and lives under `draft` below.
     */
    templates: ["emails", "templates"] as const,
    /** The caller's sent log, newest first, one page at a time. No cursor in
     * the key — `meetings.list`'s rule and its reason. */
    list: ["emails", "list"] as const,
    /**
     * One server-merged composition, by what it was merged from.
     *
     * Two kinds share this shape because the server serves them as one payload:
     * `draft("template", `${claimId}:${templateKey}`)` for a quick template, and
     * `draft("meeting", meetingId)` for a meeting's confirmation letter. The
     * claim is *inside* the template's id rather than a fourth segment because
     * a merge is only a resource at all in combination with a claim — a key that
     * omitted it would serve WC-20017's letter into WC-20044's composer.
     *
     * Nothing invalidates these, and the reason is *not* the one this comment
     * used to give. "A merge is a pure function of rows that this story never
     * writes" is false: the letter is cut from `stage`, `status`, `injury_type`,
     * `cause`, `icd` and `body_part` — Story 2.3's inline-editable fields, in a
     * pane a handler can reach without closing the composer — and from
     * `days_open`, which moves overnight. What makes an invalidation
     * unnecessary is that `useMergedTemplate` holds no staleness at all: every
     * open of the composer is a fresh mount, so choosing a template re-merges
     * against whatever the rows say *then*, and the cached copy is only ever
     * the placeholder that keeps the form from blanking mid-request.
     */
    draft: (kind: "template" | "meeting", id: string | number) =>
      ["emails", "draft", kind, id] as const,
    /** The **mutation** key the send command carries. Nothing is cached under
     * it; `useIsMutating` counts it so the composer's controls disable
     * themselves while the write is in flight — an append-only table has no
     * version to refuse a double submit with. */
    writes: ["emails", "write"] as const,
  },
} as const;
