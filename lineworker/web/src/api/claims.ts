/**
 * Server state for the claim queue (FR-H-1, FR-Q-1).
 *
 * AD-1 leaves these hooks nothing to do but fetch. The grouping, the eight
 * filters, the priority order and the 🔺 marker are all decided by
 * `services/worklist` over the caller's scope, so there is no client-side
 * sort, no predicate and no fallback here — a queue that ranked itself in
 * the browser is the prototype behaviour this story replaces.
 *
 * Note what changing the filter does: it changes the **query key**, so
 * TanStack refetches. It does not narrow a cached list. AD-1 again — the
 * client does not hold the persona's whole caseload and must not pretend to.
 */
import {
  useInfiniteQuery,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "./client";
import { problemExtension } from "./errors";
import { queryKeys } from "./queryKeys";
import type { components } from "./schema";

export type ClaimCard = components["schemas"]["ClaimCardResponse"];
export type StageGroup = components["schemas"]["StageGroupResponse"];
export type StageGroups = components["schemas"]["StageGroupsResponse"];
export type ClaimQueue = components["schemas"]["ClaimQueueResponse"];
export type QueueFilter = components["schemas"]["QueueFilter"];
export type Stage = components["schemas"]["Stage"];
export type RiskBand = components["schemas"]["RiskBand"];

/** The four groups in lifecycle order — the order the pane renders them in. */
export const STAGE_ORDER: readonly Stage[] = [
  "intake",
  "investigation",
  "treatment",
  "settled",
];

export function useClaimQueue(filter: QueueFilter) {
  return useQuery({
    queryKey: queryKeys.claims.queue(filter),
    queryFn: async (): Promise<ClaimQueue> => {
      const { data } = await api.GET("/claims/queue", {
        params: { query: { filter } },
      });
      return data!;
    },
    // Shorter than the top bar's 30s: the queue is the surface a handler
    // works from all day, and Epic 2's edits invalidate this key directly
    // when they change something a card shows.
    staleTime: 15_000,
  });
}

/**
 * The pages beyond a stage group's first — the "Show more" affordance.
 *
 * An infinite query rather than a second plain one because the pages
 * *accumulate*: a group expanded twice shows both pages, and the cursor for
 * the third comes off the second. Seeded from the first page's `nextCursor`
 * (which the pane already holds) so the group is never re-fetched just to
 * reach page two.
 *
 * `enabled` is the caller's expansion state. Without it every mounted group
 * with a `nextCursor` would fetch its second page on load, which is exactly
 * the eagerness pagination exists to avoid. A **disabled** call still
 * subscribes to the entry and reads whatever is in it, which is what lets a
 * second reader of the same group see the pages the queue pane fetched
 * without fetching anything itself.
 */
export function useStageGroupPages(
  filter: QueueFilter,
  stage: Stage,
  firstCursor: string | null,
  enabled: boolean,
) {
  return useInfiniteQuery({
    queryKey: queryKeys.claims.queuePages(filter, stage, firstCursor),
    initialPageParam: firstCursor,
    queryFn: async ({ pageParam }): Promise<StageGroup> => {
      const { data } = await api.GET("/claims/queue", {
        params: { query: { filter, stage, cursor: pageParam ?? undefined } },
      });
      // Only this group's page is read. The response carries all four
      // groups (every one of them truthful — the server refuses to blank
      // the others), but the caller asked about one.
      return data!.groups[stage];
    },
    getNextPageParam: (last: StageGroup) => last.nextCursor ?? undefined,
    enabled: enabled && firstCursor !== null,
    staleTime: 15_000,
  });
}

/** The case file for one claim (Story 2.2). */
export type ClaimDetail = components["schemas"]["ClaimDetailResponse"];
export type CaseHeaderData = components["schemas"]["CaseHeaderResponse"];
export type StepperStep = components["schemas"]["StepperStepResponse"];
export type StageOverview = ClaimDetail["overview"];
export type IntakeOverviewData = components["schemas"]["IntakeOverviewResponse"];
export type InvestigationOverviewData =
  components["schemas"]["InvestigationOverviewResponse"];
export type TreatmentOverviewData = components["schemas"]["TreatmentOverviewResponse"];
export type SettledOverviewData = components["schemas"]["SettledOverviewResponse"];
export type TimelineEntry = components["schemas"]["TimelineEntryResponse"];
export type ChecklistRow = components["schemas"]["ChecklistRowResponse"];
export type CostSplit = components["schemas"]["CostSplitResponse"];
export type TreatmentPhase = components["schemas"]["TreatmentPhase"];
export type CoordinationStatus = components["schemas"]["CoordinationStatus"];
export type DocType = components["schemas"]["DocType"];
export type CommStatus = components["schemas"]["CommStatus"];
export type ReturnStatus = components["schemas"]["ReturnStatus"];
export type Disability = components["schemas"]["Disability"];
export type RecoveryWindow = components["schemas"]["RecoveryWindow"];

/**
 * One claim's case file.
 *
 * `enabled` and the `?? ""` key are defensive, not load-bearing: today the
 * only caller is `ClaimDetailPane`'s `CaseFile`, which is typed
 * `claimId: string` and is mounted only after the pane has branched on
 * `selectedClaimId === null`, so neither guard is reachable. They stay
 * because the hook is exported and a second caller should not have to
 * rediscover that `/claims/null` is a request this must never make — but the
 * previous version of this comment claimed the workspace already called it
 * that way, which it does not (code review, 2026-08-12).
 *
 * `retry: false` for a 404. The shared client already refuses to retry
 * 4xx (`queryClient.ts`), so this is inherited rather than restated — noted
 * because "claim not in your caseload" is the one error this pane must
 * render immediately rather than after two backoffs.
 */
export function useClaimDetail(claimId: string | null) {
  return useQuery({
    queryKey: queryKeys.claims.detail(claimId ?? ""),
    queryFn: async (): Promise<ClaimDetail> => {
      const { data } = await api.GET("/claims/{claim_business_id}", {
        params: { path: { claim_business_id: claimId! } },
      });
      return data!;
    },
    enabled: claimId !== null,
    // The same 15s the queue uses. The two are read side by side and Epic
    // 2's edits invalidate both, so a case file that went stale on a
    // different clock from the card naming it would be a visible
    // disagreement.
    staleTime: 15_000,
  });
}

/** The audited inline edit (Story 2.3). */
export type ClaimFieldPatch = components["schemas"]["ClaimFieldPatch"];
export type EditOptions = components["schemas"]["EditOptionsResponse"];
export type BodyPartOption = components["schemas"]["BodyPartOptionResponse"];

/**
 * The six fields the injury card edits, by their wire names.
 *
 * Derived from the generated patch type rather than written out, so a field
 * the server stops accepting is a TypeScript error here instead of a 422 a
 * handler discovers.
 */
export type EditableField = Exclude<keyof ClaimFieldPatch, "expectedVersion">;

/**
 * Where each edited scalar is echoed back, and nowhere else.
 *
 * Two explicit lists rather than a spread over whatever key was edited: the
 * header and the investigation card carry *overlapping but different*
 * subsets, and `{...header, [field]: value}` on a field the header does not
 * have would invent a property that the next server response then deletes —
 * a cache that briefly holds a shape the API never sends.
 */
const OPTIMISTIC_HEADER_FIELDS = ["injuryType", "cause", "bodyKey", "icd"] as const;
const OPTIMISTIC_INVESTIGATION_FIELDS = [
  "injuryType",
  "cause",
  "icd",
  "disability",
  "recovery",
] as const;
/**
 * The treatment variant's one editable field (code review, 2026-08-12).
 *
 * It was missing, and the omission was visible: a `<select>` bound to
 * `overview.recovery` with nothing echoed back re-renders the *old* option
 * the instant the handler picks a new one, so the choice appears to be
 * rejected and then jumps when the response lands. The phase banner beside
 * it still waits for the server — `expectedDays` and the phase are
 * derivations, and those are exactly what AD-9 says must not be guessed.
 */
const OPTIMISTIC_TREATMENT_FIELDS = ["recovery"] as const;

/**
 * The injury block's primary marker carries `bodyKey` too (Story 2.6's review
 * pass, on the 2.4 surface).
 *
 * `claim.body_key` reaches the payload in **two** places — `header.bodyKey` and
 * `injury.markers[0].bodyKey` — and only the first was echoed. On the Injury
 * Diagram tab both are on screen at once, so changing the body part moved the
 * header instantly while the pulsing marker stayed on the old hotspot for the
 * round trip and then jumped: two controls displaying one column, visibly
 * disagreeing. That is the same defect Story 2.4's own review pass fixed for
 * the treatment card's recovery select, one tab over.
 *
 * **The primary marker only.** A secondary injury's `bodyKey` is its own
 * `additional_injury` row and this command does not touch it. And nothing else
 * on the marker moves: `band` and `severityScore` are the server's answers, and
 * AD-9 is explicit that a derivation must not be guessed in the browser.
 */
function withPrimaryMarkerBodyKey(injury: ClaimDetail["injury"], value: string) {
  const [primary, ...rest] = injury.markers;
  if (!primary?.primary) return injury;
  return { ...injury, markers: [{ ...primary, bodyKey: value }, ...rest] };
}

/**
 * Write the edited scalar into a cached case file — and nothing else.
 *
 * This is the whole of what AD-9 permits optimistically: the value the
 * handler typed, in the places the server echoes it back. `bodyKey` is a
 * user-entered scalar and moves; `bodyPart`, its label, does **not** — the
 * mapping from key to label is the server's (`services/claims/reference.py`)
 * and guessing it here would be a second copy of a vocabulary. Nor does
 * `version`, `risk`, `severityScore` or the timeline: every one of those is
 * the server's answer and waits for it.
 *
 * `icdDesc` is editable through the command but appears on no surface, so it
 * changes nothing here — the mutation still round-trips and the response
 * still replaces the cache.
 *
 * Exported so a test can assert exactly that, without rendering anything.
 */
export function applyOptimisticEdit(
  detail: ClaimDetail,
  field: EditableField,
  value: string,
): ClaimDetail {
  const header = (OPTIMISTIC_HEADER_FIELDS as readonly string[]).includes(field)
    ? { ...detail.header, [field]: value }
    : detail.header;
  const echoed =
    detail.overview.stageVariant === "investigation"
      ? OPTIMISTIC_INVESTIGATION_FIELDS
      : detail.overview.stageVariant === "treatment"
        ? OPTIMISTIC_TREATMENT_FIELDS
        : [];
  const overview = (echoed as readonly string[]).includes(field)
    ? { ...detail.overview, [field]: value }
    : detail.overview;
  const injury =
    field === "bodyKey" ? withPrimaryMarkerBodyKey(detail.injury, value) : detail.injury;
  return { ...detail, header, overview, injury };
}

/**
 * Edit one field of a claim, optimistically, under the server's version.
 *
 * The three outcomes are the story's three acceptance criteria, and each is
 * handled here rather than in the component:
 *
 * - **Success.** The response *is* the fresh case file, so it is written
 *   straight into the cache — no flash of the pre-edit value while a refetch
 *   is in flight. The detail and queue keys are then invalidated, because
 *   the queue card shows the same claim's injury type and must not be left
 *   holding the old one (AC 3).
 * - **Conflict (409).** Roll back to the snapshot, then render the fresh
 *   entity the problem document carries. No retry, no merge (AD-9): the
 *   edit was written against values somebody has since changed, and
 *   re-sending it would overwrite their work silently.
 * - **Anything else.** Roll back and let the caller show the message at the
 *   field (NFR-3: inline, never a blocking dialog).
 */
/**
 * The edited fields of one commit — one field, or the ICD pair.
 *
 * Derived from the generated patch rather than `Record<EditableField, string>`
 * so the enum-valued fields keep their member unions: `disability: "yes"`
 * fails to compile here rather than 422ing at a handler.
 */
export type FieldEdits = Omit<ClaimFieldPatch, "expectedVersion">;

/**
 * A 409's `claim` member, if it really is a case file.
 *
 * The value crossed a network and `problemExtension` is an unchecked cast,
 * so a truncated or non-conforming body would otherwise be written straight
 * into the detail cache — after which `detail.data.header` reads `undefined`,
 * `CaseHeader` throws, and (there being no error boundary) the pane blanks.
 * Three structural checks are enough to tell a case file from a fragment;
 * anything less is trusted no further than "the server said something".
 */
function freshClaimFrom(error: unknown): ClaimDetail | undefined {
  const claim = problemExtension<ClaimDetail>(error, "claim");
  return claim && claim.claimId && claim.header && claim.overview ? claim : undefined;
}

export function useEditClaimFields(claimId: string) {
  const client = useQueryClient();
  const key = queryKeys.claims.detail(claimId);

  return useMutation({
    mutationKey: queryKeys.claims.writes(claimId),
    mutationFn: async (variables: {
      edits: FieldEdits;
      expectedVersion: number;
    }): Promise<ClaimDetail> => {
      const { data } = await api.PATCH("/claims/{claim_business_id}", {
        params: { path: { claim_business_id: claimId } },
        body: { expectedVersion: variables.expectedVersion, ...variables.edits },
      });
      return data!;
    },
    onMutate: async (variables) => {
      // A refetch landing mid-edit would overwrite the optimistic value with
      // the pre-edit one and look like the input rejecting what was typed.
      await client.cancelQueries({ queryKey: key });
      const snapshot = client.getQueryData<ClaimDetail>(key);
      if (snapshot) {
        client.setQueryData(
          key,
          Object.entries(variables.edits).reduce(
            // `null` is not a value this UI sends — the command refuses it —
            // but the generated patch type admits it, so it is skipped rather
            // than cast away.
            (detail, [field, value]) =>
              typeof value === "string"
                ? applyOptimisticEdit(detail, field as EditableField, value)
                : detail,
            snapshot,
          ),
        );
      }
      return { snapshot };
    },
    onError: (error, _variables, context) => {
      // **Rolled back only if nothing else has landed since.** The snapshot
      // was taken before this mutation; if a concurrent one succeeded in the
      // meantime, restoring it would throw away a *committed* edit and leave
      // the cache behind the database, after which every later edit 409s
      // (code review). Comparing versions is enough: the server increments
      // on every write, so an unchanged version means nothing else won.
      const current = client.getQueryData<ClaimDetail>(key);
      if (context?.snapshot && current?.version === context.snapshot.version) {
        client.setQueryData(key, context.snapshot);
      }
      const fresh = freshClaimFrom(error);
      if (fresh) client.setQueryData(key, fresh);
    },
    onSuccess: (fresh) => {
      // The response *is* the fresh case file, so it is installed directly.
      // The detail key is then marked stale **without** an immediate refetch:
      // invalidating it outright fired a second GET per edit (measured), and
      // re-opened the read-after-write window this design closed — the
      // command commits and re-reads in a new transaction, so a follow-up GET
      // resolving against older data would show the saved value reverting.
      client.setQueryData(key, fresh);
      void client.invalidateQueries({ queryKey: key, refetchType: "none" });
      // The queue is a different resource and genuinely must re-fetch: its
      // cards show this claim's injury type, and it has no fresh copy.
      void client.invalidateQueries({ queryKey: queryKeys.claims.queues });
    },
    onSettled: () => {
      // Whatever happened, the cache is re-synchronised on the next read.
      // The `onError` rollback above is conservative by design, so this is
      // what guarantees a failed edit cannot leave a stale entity behind
      // indefinitely (code review).
      void client.invalidateQueries({ queryKey: key, refetchType: "none" });
    },
  });
}

/** The injury diagram (Story 2.4). */
export type InjuryDiagram = components["schemas"]["InjuryDiagramResponse"];
export type InjuryMarker = components["schemas"]["InjuryMarkerResponse"];
export type Prognosis = components["schemas"]["PrognosisResponse"];
export type TreatmentPlanStep = components["schemas"]["TreatmentPlanStepResponse"];
export type NewInjury = components["schemas"]["NewInjury"];

/**
 * What every Story 2.4 mutation does once the server has answered.
 *
 * Three things are invalidated and the third is the one worth writing down.
 *
 * - **The case file** is installed directly from the response and then
 *   marked stale without a refetch, exactly as `useEditClaimFields` does:
 *   the body *is* the fresh entity, and a follow-up GET would re-open the
 *   read-after-write window that returning it closes.
 * - **The queue** genuinely must re-fetch. Editing the severity score moves
 *   the claim's risk band, which is the card's dot *and* an input to its
 *   priority score — so the card can change position, not just colour.
 * - **The top-bar stat tiles**, which no earlier mutation touched. `highRisk`
 *   counts the claims in the high band across the caller's whole book, so a
 *   score crossing the boundary changes a number two panes away (AC 3's
 *   "stat tiles" clause). Nothing in the SPA could compute that; it has to
 *   be re-asked.
 *
 * The SLA key is deliberately **not** invalidated: those four figures are
 * averages of recorded durations, and nothing here writes one.
 */
function afterInjuryWrite(
  client: ReturnType<typeof useQueryClient>,
  key: readonly unknown[],
  fresh: ClaimDetail,
): void {
  client.setQueryData(key, fresh);
  void client.invalidateQueries({ queryKey: key, refetchType: "none" });
  void client.invalidateQueries({ queryKey: queryKeys.claims.queues });
  void client.invalidateQueries({ queryKey: queryKeys.stats.topbar });
}

/**
 * Set the claim's severity score (AC 3).
 *
 * **No optimistic update at all**, unlike the inline text edits. AD-9 permits
 * one for "the user-entered scalar", and the score *is* one — but every
 * visible consequence of changing it is derived: the band that colours the
 * number, the gauge, the primary marker, the bar's own colour. Writing the
 * digits in optimistically while their colour waited for the server would
 * render a state that exists nowhere — a 78 in green — which is worse than
 * the ~100ms of the input holding its previous value. The severity input is
 * disabled while the commit is in flight, so there is no ambiguity about
 * what is being sent.
 */
export function useEditSeverity(claimId: string) {
  const client = useQueryClient();
  const key = queryKeys.claims.detail(claimId);

  return useMutation({
    mutationKey: queryKeys.claims.writes(claimId),
    mutationFn: async (variables: {
      severityScore: number;
      expectedVersion: number;
    }): Promise<ClaimDetail> => {
      const { data } = await api.PATCH("/claims/{claim_business_id}/severity", {
        params: { path: { claim_business_id: claimId } },
        body: {
          expectedVersion: variables.expectedVersion,
          severityScore: variables.severityScore,
        },
      });
      return data!;
    },
    onError: (error) => {
      // No snapshot to roll back to, so a 409's fresh entity is the whole of
      // the recovery: install it and let the card render the value that won.
      const fresh = freshClaimFrom(error);
      if (fresh) client.setQueryData(key, fresh);
    },
    onSuccess: (fresh) => afterInjuryWrite(client, key, fresh),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: key, refetchType: "none" });
    },
  });
}

/**
 * Record a secondary injury (AC 2).
 *
 * Nothing optimistic here either, and for a stronger reason than above: the
 * new marker needs an `id`, a `version` and a severity band, none of which
 * the browser can invent. The prototype fabricates an id from `Date.now()`;
 * a row's identity is the database's.
 */
export function useAddInjury(claimId: string) {
  const client = useQueryClient();
  const key = queryKeys.claims.detail(claimId);

  return useMutation({
    mutationKey: queryKeys.claims.writes(claimId),
    mutationFn: async (variables: {
      injury: Omit<NewInjury, "expectedVersion">;
      expectedVersion: number;
    }): Promise<ClaimDetail> => {
      const { data } = await api.POST("/claims/{claim_business_id}/injuries", {
        params: { path: { claim_business_id: claimId } },
        body: { expectedVersion: variables.expectedVersion, ...variables.injury },
      });
      return data!;
    },
    onError: (error) => {
      const fresh = freshClaimFrom(error);
      if (fresh) client.setQueryData(key, fresh);
    },
    onSuccess: (fresh) => afterInjuryWrite(client, key, fresh),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: key, refetchType: "none" });
    },
  });
}

/**
 * Remove a secondary injury (AC 2).
 *
 * `expectedVersion` is the **injury row's**, not the claim's — the delete
 * compare-and-swaps on the row it destroys, so a ✕ is not refused because
 * somebody corrected an unrelated field on the same claim. The marker list
 * carries each row's version for exactly this.
 */
export function useRemoveInjury(claimId: string) {
  const client = useQueryClient();
  const key = queryKeys.claims.detail(claimId);

  return useMutation({
    mutationKey: queryKeys.claims.writes(claimId),
    mutationFn: async (variables: {
      injuryId: number;
      expectedVersion: number;
    }): Promise<ClaimDetail> => {
      const { data } = await api.DELETE(
        "/claims/{claim_business_id}/injuries/{injury_id}",
        {
          params: {
            path: { claim_business_id: claimId, injury_id: variables.injuryId },
            query: { expectedVersion: variables.expectedVersion },
          },
        },
      );
      return data!;
    },
    onError: (error) => {
      const fresh = freshClaimFrom(error);
      if (fresh) client.setQueryData(key, fresh);
    },
    onSuccess: (fresh) => afterInjuryWrite(client, key, fresh),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: key, refetchType: "none" });
    },
  });
}

/** The statutory benefit calculation (Story 3.1). */
export type Benefit = components["schemas"]["BenefitResponse"];
export type IndemnityType = components["schemas"]["IndemnityType"];
export type CompRatePatch = components["schemas"]["CompRatePatch"];

/**
 * Set or clear the comp-rate override (AC 4).
 *
 * `compRateBp: null` is the ↺ reset — one mutation for both, because they are
 * one column and one command. The value is **basis points**; `lib/rate.ts` is
 * the only place a percentage is turned into one.
 *
 * **Optimistic, unlike the severity score.** AD-9 permits an optimistic update
 * for the user-entered scalar, and here the scalar's one *visible* consequence
 * — the digits in the input — is the scalar itself: the comp rate is not
 * banded, coloured or thresholded by anything. The weekly figure, the
 * indemnity type and the rationale paragraph beside it are all the server's
 * answers and deliberately do **not** move until it replies, which is why the
 * echo is written into `compRateBp` and `isOverridden` and nothing else. Story
 * 2.4's severity card takes the opposite call for the opposite reason: every
 * visible consequence of *that* number is derived, so a 78 would render in
 * green for a round trip.
 *
 * `isOverridden` moves with it because the ↺ control is bound to it, and a
 * reset whose button stayed on screen for the round trip invites a second
 * click that 409s against the version the first is consuming.
 */
export function useEditCompRate(claimId: string) {
  const client = useQueryClient();
  const key = queryKeys.claims.detail(claimId);

  return useMutation({
    mutationKey: queryKeys.claims.writes(claimId),
    mutationFn: async (variables: {
      compRateBp: number | null;
      expectedVersion: number;
    }): Promise<ClaimDetail> => {
      const { data } = await api.PATCH("/claims/{claim_business_id}/comp-rate", {
        params: { path: { claim_business_id: claimId } },
        body: {
          expectedVersion: variables.expectedVersion,
          compRateBp: variables.compRateBp,
        },
      });
      return data!;
    },
    onMutate: async (variables) => {
      await client.cancelQueries({ queryKey: key });
      const snapshot = client.getQueryData<ClaimDetail>(key);
      if (snapshot) {
        client.setQueryData(key, applyOptimisticCompRate(snapshot, variables.compRateBp));
      }
      return { snapshot };
    },
    onError: (error, _variables, context) => {
      // Version-guarded, exactly as `useEditClaimFields`' rollback is: if a
      // concurrent mutation committed in the meantime, restoring the snapshot
      // would throw away a *saved* edit and leave every later write 409ing.
      const current = client.getQueryData<ClaimDetail>(key);
      if (context?.snapshot && current?.version === context.snapshot.version) {
        client.setQueryData(key, context.snapshot);
      }
      const fresh = freshClaimFrom(error);
      if (fresh) client.setQueryData(key, fresh);
    },
    onSuccess: (fresh) => {
      // The response *is* the fresh case file. The queue is deliberately not
      // invalidated: no queue card shows a comp rate, and nothing in the
      // priority score reads one — unlike the severity score, which moves a
      // card's band and its position.
      client.setQueryData(key, fresh);
      void client.invalidateQueries({ queryKey: key, refetchType: "none" });
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: key, refetchType: "none" });
    },
  });
}

/**
 * Write the submitted rate into a cached case file — and nothing else.
 *
 * Exported so a test can assert exactly what waits for the server, without
 * rendering anything. `weeklyCents`, `indemnityType`, the statutory bounds and
 * the rationale paragraph are every one of them the server's answer.
 */
export function applyOptimisticCompRate(
  detail: ClaimDetail,
  compRateBp: number | null,
): ClaimDetail {
  return {
    ...detail,
    benefit: {
      ...detail.benefit,
      compRateBp: compRateBp ?? detail.benefit.defaultCompRateBp,
      isOverridden: compRateBp !== null,
    },
  };
}

/** The Documents & ID tab (Story 2.5). */
export type DocumentsBlock = components["schemas"]["DocumentsBlockResponse"];
export type RequiredForm = components["schemas"]["RequiredFormResponse"];
export type EmployeeIdCardData = components["schemas"]["EmployeeIdCardResponse"];
export type DocumentRow = components["schemas"]["DocumentRowResponse"];
export type DocumentSheet = components["schemas"]["DocumentSheetResponse"];
export type SheetRow = components["schemas"]["SheetRowResponse"];
export type ClaimPath = components["schemas"]["ClaimPath"];

/**
 * The Photos tab (Story 2.6).
 *
 * **No hook of its own, deliberately.** The block rides the case file's
 * `claimDetail` query (AD-9) — one server-state idiom, no bespoke fetch for one
 * tab — and the viewer renders three fields the grid already holds, so there is
 * nothing left to ask for when a card is clicked. That is the opposite call
 * from `useDocumentSheet` above, and the difference is the payload: a document
 * sheet is a dozen rows of the claim's data assembled per document, while a
 * photo card *is* its own viewer's content.
 */
export type PhotosBlock = components["schemas"]["PhotosBlockResponse"];
export type PhotoCardData = components["schemas"]["PhotoResponse"];

/**
 * One document's read-only viewer sheet (AC 4).
 *
 * A **query of its own** rather than a block on the case file, and that is the
 * one place this story departs from Story 2.2's "everything the tab shows
 * arrives with the tab". A claim carries three to eight documents and each
 * sheet is a dozen rows of the same claim's data, so folding them all into the
 * case file would multiply the payload of the console's most-fetched endpoint
 * to serve a modal most handlers never open.
 *
 * `enabled` is the dialog's open state, so nothing is requested until a row is
 * clicked. TanStack keeps the answer cached afterwards, which is what makes
 * re-opening the same document instant — and the sheet is derived from stored
 * columns, so a stale one is only stale in the way the case file behind it is.
 */
export function useDocumentSheet(
  claimId: string,
  documentId: number | null,
) {
  return useQuery({
    queryKey: queryKeys.claims.documentSheet(claimId, documentId ?? 0),
    queryFn: async (): Promise<DocumentSheet> => {
      const { data } = await api.GET(
        "/claims/{claim_business_id}/documents/{document_id}/content",
        {
          params: {
            path: { claim_business_id: claimId, document_id: documentId! },
          },
        },
      );
      return data!;
    },
    enabled: documentId !== null,
    // The same 15s the case file uses: the sheet is assembled from the very
    // columns the case file publishes, so two different staleness clocks would
    // let the viewer and the pane behind it disagree about one claim.
    staleTime: 15_000,
  });
}

/**
 * True while **any** command against this claim is in flight.
 *
 * The one flag every editable control on the case file disables itself with.
 * `expectedVersion` comes from the cached case file and no mutation here
 * advances it optimistically, so two commits that overlap send the same
 * version: the second is refused with a 409 that says somebody else changed
 * the claim, about the handler's own edit, whose keystrokes are then
 * unrecoverable. TanStack also detaches the first call's callbacks when a
 * second `mutate()` runs on the same hook, so that rollback is silent.
 *
 * Story 2.3 made this true within one hook and its `EditableRow` said so.
 * Story 2.4 added the severity score and the add/remove pair, each with its
 * own `isPending` — three more ways for two writes to overlap on one screen
 * (code review, 2026-08-12). Counting mutations by key is what makes the
 * guarantee hold across all four rather than within each.
 */
export function useClaimWriteInFlight(claimId: string): boolean {
  return useIsMutating({ mutationKey: queryKeys.claims.writes(claimId) }) > 0;
}
