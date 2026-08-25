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
import { ApiError, problemExtension } from "./errors";
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
 *
 * **`groups` names the one group this hook reads** (Story 9.8). It used to send
 * only `stage` — which tells the server which group the *cursor* addresses and
 * narrows nothing — so every "Show more" had the server rank, mark and
 * serialise all four groups, and this function then kept one and threw three
 * away. At the seeded page size that is up to two hundred fully-derived cards
 * to deliver fifty. `useClaimQueue` above deliberately sends neither parameter:
 * the initial load wants all four, and a response with three groups silently
 * blank would be indistinguishable from a caseload with nothing in them.
 *
 * Both parameters are sent, naming the same stage, because they answer
 * different questions: `stage` is compared against the cursor, `groups` decides
 * what comes back.
 */
/**
 * One "Show more" page: the rows, and the cursor for the page after it.
 *
 * Deliberately narrower than `StageGroup`. `total` is the size of the whole
 * filtered group and belongs to the base query — it is what the chip beside the
 * stage header reads — so leaving it out of this type means the end-of-walk
 * page below has no count to invent.
 */
type StageGroupPage = Pick<StageGroup, "items" | "nextCursor">;

/** No rows, no next cursor: the walk this group was on has nothing further. */
const END_OF_WALK: StageGroupPage = { items: [], nextCursor: null };

export function useStageGroupPages(
  filter: QueueFilter,
  stage: Stage,
  firstCursor: string | null,
  enabled: boolean,
) {
  return useInfiniteQuery({
    queryKey: queryKeys.claims.queuePages(filter, stage, firstCursor),
    initialPageParam: firstCursor,
    queryFn: async ({ pageParam }): Promise<StageGroupPage> => {
      const { data } = await api.GET("/claims/queue", {
        params: {
          query: { filter, stage, groups: [stage], cursor: pageParam ?? undefined },
        },
      });
      // The one group this request asked for. The others are **absent** rather
      // than empty — the server distinguishes "you did not ask" from "there is
      // nothing in this stage" — so the group named in `groups` is the group
      // that comes back, which the endpoint guarantees.
      //
      // Guaranteed, and still not asserted with a `!`. Every field of `groups`
      // is optional in the generated types (that is what makes a narrowed
      // response expressible at all), so a payload that ever came back without
      // this stage would put `undefined` into the page list and reach
      // `getNextPageParam` as a TypeError thrown *inside* the query — an error
      // state the pane renders as a failed fetch with no reason attached.
      // Reading an absent group as an ended walk is the honest fallback: no
      // more rows came back, so there are no more rows to show, and the "Show
      // more" button retires instead of the group breaking.
      return data!.groups[stage] ?? END_OF_WALK;
    },
    getNextPageParam: (last: StageGroupPage) => last.nextCursor ?? undefined,
    enabled: enabled && firstCursor !== null,
    staleTime: 15_000,
  });
}

/** The case file for one claim (Story 2.2). */
export type ClaimDetail = components["schemas"]["ClaimDetailResponse"];
export type CaseHeaderData = components["schemas"]["CaseHeaderResponse"];
export type StepperStep = components["schemas"]["StepperStepResponse"];
export type StageOverview = ClaimDetail["overview"];
export type IntakeOverviewData =
  components["schemas"]["IntakeOverviewResponse"];
export type InvestigationOverviewData =
  components["schemas"]["InvestigationOverviewResponse"];
export type TreatmentOverviewData =
  components["schemas"]["TreatmentOverviewResponse"];
export type SettledOverviewData =
  components["schemas"]["SettledOverviewResponse"];
export type TimelineEntry = components["schemas"]["TimelineEntryResponse"];
export type ChecklistRow = components["schemas"]["ChecklistRowResponse"];
export type CostSplit = components["schemas"]["CostSplitResponse"];
export type TreatmentPhase = components["schemas"]["TreatmentPhase"];
export type CoordinationStatus = components["schemas"]["CoordinationStatus"];
export type DocType = components["schemas"]["DocType"];
export type CommStatus = components["schemas"]["CommStatus"];
export type ReturnStatus = components["schemas"]["ReturnStatus"];
export type Disability = components["schemas"]["Disability"];
export type ClaimStatus = components["schemas"]["ClaimStatus"];
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
const OPTIMISTIC_HEADER_FIELDS = [
  "injuryType",
  "cause",
  "bodyKey",
  "icd",
] as const;
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
function withPrimaryMarkerBodyKey(
  injury: ClaimDetail["injury"],
  value: string,
) {
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
    field === "bodyKey"
      ? withPrimaryMarkerBodyKey(detail.injury, value)
      : detail.injury;
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
  return claim && claim.claimId && claim.header && claim.overview
    ? claim
    : undefined;
}

/**
 * Mark everything a claim edit changes as stale — and nothing it does not.
 *
 * Three exact keys and one prefix, each `refetchType: "none"`. Together that is
 * exactly what one prefix invalidation of `claims.detail` used to do, **minus
 * the insights cache**, and separating them is the whole point (review of Story
 * 6.2, M5).
 *
 * **The fourth entry is a prefix and has to be.** `documentSheet` carries a
 * document id, so there is no exact key to name — and the first version of this
 * helper listed only the three that could be named, which silently dropped
 * every open document viewer from the invalidation the nesting exists to
 * deliver (follow-up review of Story 6.2, B1). "Everything the old prefix
 * reached except insights" is the contract; it is only true if the sheets are
 * in the list.
 *
 * TanStack matches query keys by prefix, and `actions`, `financials`,
 * `document` and `insights` are all nested under the claim's own segment — but
 * for two opposite reasons. The first three are cut from the claim's rows,
 * so an edit that changes the claim really does change them and a prefix
 * invalidation reaching them is correct. `insights` is nested so it can be
 * *addressed* beside the case file without being evicted by it: a narrative is
 * a cache with its own generation timestamp (AD-10), and editing a claim does
 * not make yesterday's narrative wrong, it makes it dated — which the card says
 * for itself. `queryKeys.ts` states that "nothing else invalidates it at all",
 * and until this helper existed every claim edit invalidated it, defeating the
 * deliberate five-minute `staleTime` and re-fetching four narratives on every
 * keystroke's commit.
 *
 * Written once here rather than expanded at each of the eight call sites,
 * because eight copies of a three-line invalidation is eight places for the
 * next nested key to be forgotten.
 *
 * `refetchType: "none"` throughout, which is `useEditClaimFields`' rule: the
 * mutation response *is* the fresh case file and is installed directly, so an
 * immediate refetch would re-open the read-after-write window that returning it
 * closes.
 */
function markCaseFileStale(
  client: ReturnType<typeof useQueryClient>,
  claimId: string,
): void {
  for (const key of [
    queryKeys.claims.detail(claimId),
    queryKeys.claims.actions(claimId),
    queryKeys.claims.financials(claimId),
  ]) {
    void client.invalidateQueries({
      queryKey: key,
      exact: true,
      refetchType: "none",
    });
  }
  // …and every open document sheet, which is the one nested key that *cannot*
  // be named exactly: it carries a document id, and the helper does not know
  // which documents a reader happens to have open. Invalidated by prefix, which
  // is safe because nothing else lives under `…/document` — the key insights
  // deliberately does not share (follow-up review of Story 6.2, B1).
  void client.invalidateQueries({
    queryKey: queryKeys.claims.documentSheets(claimId),
    refetchType: "none",
  });
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
        body: {
          expectedVersion: variables.expectedVersion,
          ...variables.edits,
        },
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
      markCaseFileStale(client, claimId);
      // The queue is a different resource and genuinely must re-fetch: its
      // cards show this claim's injury type, and it has no fresh copy.
      void client.invalidateQueries({ queryKey: queryKeys.claims.queues });
    },
    onSettled: () => {
      // Whatever happened, the cache is re-synchronised on the next read.
      // The `onError` rollback above is conservative by design, so this is
      // what guarantees a failed edit cannot leave a stale entity behind
      // indefinitely (code review).
      markCaseFileStale(client, claimId);
    },
  });
}

/** The injury diagram (Story 2.4). */
export type InjuryDiagram = components["schemas"]["InjuryDiagramResponse"];
export type InjuryMarker = components["schemas"]["InjuryMarkerResponse"];
export type Prognosis = components["schemas"]["PrognosisResponse"];
export type TreatmentPlanStep =
  components["schemas"]["TreatmentPlanStepResponse"];
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
  claimId: string,
  fresh: ClaimDetail,
): void {
  client.setQueryData(queryKeys.claims.detail(claimId), fresh);
  markCaseFileStale(client, claimId);
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
    onSuccess: (fresh) => afterInjuryWrite(client, claimId, fresh),
    onSettled: () => {
      markCaseFileStale(client, claimId);
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
        body: {
          expectedVersion: variables.expectedVersion,
          ...variables.injury,
        },
      });
      return data!;
    },
    onError: (error) => {
      const fresh = freshClaimFrom(error);
      if (fresh) client.setQueryData(key, fresh);
    },
    onSuccess: (fresh) => afterInjuryWrite(client, claimId, fresh),
    onSettled: () => {
      markCaseFileStale(client, claimId);
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
    onSuccess: (fresh) => afterInjuryWrite(client, claimId, fresh),
    onSettled: () => {
      markCaseFileStale(client, claimId);
    },
  });
}

/** The statutory benefit calculation (Story 3.1). */
export type Benefit = components["schemas"]["BenefitResponse"];
export type IndemnityType = components["schemas"]["IndemnityType"];
export type CompRatePatch = components["schemas"]["CompRatePatch"];

/**
 * The reserve adequacy verdict (Story 3.2).
 *
 * No hook of its own, and no query key of its own — deliberately. The verdict
 * rides on the case file, so it arrives with `useClaimDetail` under
 * `queryKeys.claims.detail`, and Story 3.3's Bills financial summary reads the
 * identical cached object rather than fetching a second opinion. That is what
 * makes "the same verdict in both surfaces" structural instead of a promise
 * two components make separately.
 */
export type ReserveCheck = components["schemas"]["ReserveCheckResponse"];
export type ReserveVerdict = components["schemas"]["ReserveVerdict"];

/**
 * The Bills & Payments read model (Story 3.3).
 *
 * **A key of its own, unlike the reserve check — and the two do not disagree.**
 * The verdict rides on the case file so the treatment Overview card can render
 * it without a second request; this payload is the whole tab, which is far
 * more than that card needs and is fetched only when the tab is opened. What
 * makes them agree is not a shared cache entry but a shared *computation*:
 * both come from one assembler over one set of rows on the server
 * (`services/financials/summary.py`), so whichever was fetched first, the
 * figures are the same answer rather than two answers that happen to match.
 * `reserveCheck` is therefore published on both payloads, and
 * `BillsTab.test.tsx` pins that the tab renders this one.
 *
 * **The schedule is not paginated and will not need to be**: the generator
 * clamps a claim to at most twenty weeks, and the line items are a handful.
 */
export type ClaimFinancials = components["schemas"]["ClaimFinancialsResponse"];
export type FinancialSummary =
  components["schemas"]["FinancialSummaryResponse"];
export type ScheduleWeek = components["schemas"]["ScheduleWeekResponse"];
export type ScheduleWeekStatus = components["schemas"]["ScheduleWeekStatus"];
export type BillLine = components["schemas"]["BillResponse"];
export type ExpenseLine = components["schemas"]["ExpenseResponse"];
export type LineItemStatus = components["schemas"]["LineItemStatus"];
export type BillCategory = components["schemas"]["BillCategory"];
export type ExpenseCategory = components["schemas"]["ExpenseCategory"];

export function useClaimFinancials(claimId: string | null) {
  return useQuery({
    queryKey: queryKeys.claims.financials(claimId ?? ""),
    queryFn: async (): Promise<ClaimFinancials> => {
      const { data } = await api.GET("/claims/{claim_business_id}/financials", {
        params: { path: { claim_business_id: claimId! } },
      });
      return data!;
    },
    enabled: claimId !== null,
    // The case file's 15s, for the reason the document sheet gives: this
    // payload and the case file are cut from the same rows, so two staleness
    // clocks would let the Overview card and the Bills tab drift apart on
    // screen even though the server cannot compute them differently.
    staleTime: 15_000,
  });
}

/** Approving a payment into the next batch (Story 3.4). */
export type PaymentApproval = components["schemas"]["PaymentApproval"];
export type ApprovalKind = PaymentApproval["kind"];

/**
 * A 409's `financials` member, if it really is a Bills payload.
 *
 * `freshClaimFrom`'s check over this story's entity, and for the same reason:
 * `problemExtension` is an unchecked cast over something that crossed a
 * network, and a truncated body written into the financials cache would blank
 * the tab. Three structural probes are enough to tell the payload from a
 * fragment.
 */
function freshFinancialsFrom(error: unknown): ClaimFinancials | undefined {
  const fresh = problemExtension<ClaimFinancials>(error, "financials");
  return fresh && fresh.summary && fresh.schedule && fresh.bills
    ? fresh
    : undefined;
}

/** The row's fresh status on a 409, so the sheet can say *why* it was refused. */
export function conflictPaymentStatus(error: unknown): string | undefined {
  return problemExtension<string>(error, "paymentStatus");
}

/**
 * Approve one payment row into the next batch (AC 1).
 *
 * **Nothing optimistic, and this is the clearest case in the console for that
 * rule.** AD-9 permits an optimistic update for a user-entered scalar; a
 * payment status is not one — it is a *server decision*, and the server may
 * refuse it on a guard the browser cannot evaluate (the row's current status,
 * which may have moved since this payload was fetched). Flipping the chip to
 * "Payment Scheduled" and then rolling it back would show a handler money
 * queued that was not.
 *
 * So the three outcomes are:
 *
 * - **Success.** The response *is* the fresh Bills payload, installed straight
 *   into the cache — the chip, the schedule's next-due date and the sheet's
 *   state all move together because they come from one object. The case file
 *   is invalidated beside it: its treatment card shows the same claim's
 *   disbursed-of-scheduled figures.
 * - **Conflict (409).** Install the fresh payload the problem document
 *   carries and let the sheet render the refusal inline. No retry, no merge —
 *   re-sending an approval against a row somebody has already paid is the one
 *   thing this must never do.
 * - **Anything else.** The sheet shows the message inline (NFR-3, UX-DR11:
 *   never a blocking dialog).
 *
 * There is no snapshot to roll back to, so `onError` has only the fresh-state
 * branch — `useEditSeverity`'s shape rather than `useEditClaimFields`'.
 */
export function useApprovePayment(claimId: string) {
  const client = useQueryClient();
  const key = queryKeys.claims.financials(claimId);

  return useMutation({
    // The claim's shared write key, so `useClaimWriteInFlight` counts an
    // approval like any other command: every control on the case file reads
    // `expectedVersion` out of a cached payload, and two overlapping writes
    // send versions the first has already consumed.
    mutationKey: queryKeys.claims.writes(claimId),
    mutationFn: async (variables: {
      kind: ApprovalKind;
      targetId: number;
      expectedVersion: number;
    }): Promise<ClaimFinancials> => {
      const { data } = await api.POST(
        "/claims/{claim_business_id}/payments/approvals",
        {
          params: { path: { claim_business_id: claimId } },
          body: {
            kind: variables.kind,
            targetId: variables.targetId,
            expectedVersion: variables.expectedVersion,
          },
        },
      );
      return data!;
    },
    onError: (error) => {
      const fresh = freshFinancialsFrom(error);
      if (!fresh) return;
      client.setQueryData(key, fresh);
      // The case file needs the same invalidation the success path gives it,
      // for the same reason and with the same `exact: true`. A 409 raised
      // because the batch disbursed this row moves precisely the figures the
      // treatment Overview card renders — `disbursedIndemnityCents` of
      // `scheduledIndemnityCents` — so refreshing only the Bills tab leaves
      // the two surfaces disagreeing about a claim nobody edited.
      void client.invalidateQueries({
        queryKey: queryKeys.claims.detail(claimId),
        exact: true,
      });
    },
    onSuccess: (fresh) => {
      client.setQueryData(key, fresh);
      // Marked stale without an immediate refetch, `useEditClaimFields`' rule:
      // the body *is* the fresh payload, and a follow-up GET would re-open the
      // read-after-write window that returning it closes. `exact: true` for the
      // reason spelled out below — and because `insights` is nested under the
      // claim too, and a refresh of the narratives is not something approving a
      // payment asks for (review of Story 6.2, M5).
      void client.invalidateQueries({
        queryKey: key,
        exact: true,
        refetchType: "none",
      });
      // The case file genuinely must re-fetch: its treatment Overview card
      // renders `disbursedIndemnityCents` of `scheduledIndemnityCents` from the
      // same rows, and it has no fresh copy of them.
      //
      // **`exact: true`, and leaving it off was a real defect** — caught by
      // `approving a week re-renders the sheet from the response`, which failed
      // showing the *pre-approval* status. TanStack matches query keys by
      // prefix, and Story 3.3 deliberately nested `financials` **under** the
      // claim's own segment (`["claims","detail",id,"financials"]`) so that an
      // edit invalidating the case file could reach it. That is right for an
      // edit and wrong here: this mutation has just installed the authoritative
      // payload under that very key, so a prefix invalidation fires a GET that
      // races its own write — re-opening exactly the read-after-write window
      // `refetchType: "none"` is used two lines up to close.
      void client.invalidateQueries({
        queryKey: queryKeys.claims.detail(claimId),
        exact: true,
      });
    },
    onSettled: () => {
      void client.invalidateQueries({
        queryKey: key,
        exact: true,
        refetchType: "none",
      });
    },
  });
}

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
      const { data } = await api.PATCH(
        "/claims/{claim_business_id}/comp-rate",
        {
          params: { path: { claim_business_id: claimId } },
          body: {
            expectedVersion: variables.expectedVersion,
            compRateBp: variables.compRateBp,
          },
        },
      );
      return data!;
    },
    onMutate: async (variables) => {
      await client.cancelQueries({ queryKey: key });
      const snapshot = client.getQueryData<ClaimDetail>(key);
      if (snapshot) {
        client.setQueryData(
          key,
          applyOptimisticCompRate(snapshot, variables.compRateBp),
        );
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
      markCaseFileStale(client, claimId);
    },
    onSettled: () => {
      markCaseFileStale(client, claimId);
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

/** The auto-generated action checklist (Story 3.5). */
export type ClaimActions = components["schemas"]["ClaimActionsResponse"];
export type ClaimAction = components["schemas"]["ActionResponse"];
export type ActionKey = components["schemas"]["ActionKey"];
export type ActionTarget = components["schemas"]["ActionTarget"];
export type ActionUrgency = components["schemas"]["ActionUrgency"];
export type ActionCommand = components["schemas"]["ActionCommand"];

/**
 * One claim's checklist — the list, in the order the server ranked it.
 *
 * **A query of its own rather than a block on the case file**, which is the
 * same call `useClaimFinancials` makes and for the same two reasons: the
 * payload costs the server three child reads and two rule-document loads, and
 * the case file is the console's most-fetched response. The two cannot
 * disagree despite being two entries, because the checklist is generated from
 * the very rows the case file is assembled from and the three completion
 * mutations invalidate both.
 *
 * Nothing here sorts, filters, counts or thresholds — `items` is rendered in
 * the order it arrives (AD-1, and `noDerivation.test.ts` holds the guard).
 */
export function useClaimActions(claimId: string | null) {
  return useQuery({
    queryKey: queryKeys.claims.actions(claimId ?? ""),
    queryFn: async (): Promise<ClaimActions> => {
      const { data } = await api.GET("/claims/{claim_business_id}/actions", {
        params: { path: { claim_business_id: claimId! } },
      });
      return data!;
    },
    enabled: claimId !== null,
    // The case file's 15s. The checklist is cut from the same rows, so two
    // staleness clocks would let the card and the header behind it drift apart
    // on screen even though the server cannot compute them differently.
    staleTime: 15_000,
  });
}

/**
 * What every Story 3.5 completion does once the server has answered.
 *
 * Four invalidations, and the second is the whole of AC 5:
 *
 * - **The case file** is installed directly from the response — the body *is*
 *   the fresh entity — and then marked stale without a refetch, exactly as
 *   `useEditClaimFields` does, because a follow-up GET would re-open the
 *   read-after-write window that returning it closes.
 * - **The checklist** genuinely must re-fetch, and that is AC 5's "the action
 *   list re-renders": the row the handler just completed is gone because the
 *   server re-evaluated the trigger, not because anything here removed it from
 *   a list. `exact: true` for `useApprovePayment`'s reason — the key is nested
 *   under the case file's, and a prefix invalidation would also re-fetch the
 *   payload this mutation has just installed.
 * - **The queue** must re-fetch: approving an assessment drops the claim's
 *   `pendingApproval` term, which moves its priority score and can move its
 *   card. Nothing in the browser can compute that — the weight is in a rule
 *   document the SPA has never seen.
 * - **The top-bar tiles** are counts over the caller's whole book, so a status
 *   change is a number two panes away with no local answer.
 */
function afterChecklistWrite(
  client: ReturnType<typeof useQueryClient>,
  claimId: string,
  fresh: ClaimDetail,
): void {
  const key = queryKeys.claims.detail(claimId);
  client.setQueryData(key, fresh);
  void client.invalidateQueries({
    queryKey: key,
    exact: true,
    refetchType: "none",
  });
  void client.invalidateQueries({
    queryKey: queryKeys.claims.actions(claimId),
    exact: true,
  });
  void client.invalidateQueries({ queryKey: queryKeys.claims.queues });
  void client.invalidateQueries({ queryKey: queryKeys.stats.topbar });
}

/**
 * Approve the claim's assessment (AC 4).
 *
 * **Nothing optimistic**, `useApprovePayment`'s rule for `useEditSeverity`'s
 * reason: the status is a server decision guarded on the claim's *current*
 * status, which the browser cannot evaluate — and every visible consequence of
 * it (the header chip, the queue card's position, the checklist row
 * disappearing) is derived. Flipping the chip and rolling it back would show a
 * handler an approval that did not happen.
 *
 * A 409 installs the fresh case file the problem document carries and lets the
 * card render the refusal inline (NFR-3, UX-DR11: never a blocking dialog).
 */
export function useApproveAssessment(claimId: string) {
  const client = useQueryClient();

  return useMutation({
    // The claim's shared write key, so `useClaimWriteInFlight` counts this like
    // any other command: every control on the case file reads `expectedVersion`
    // out of a cached payload, and two overlapping writes send a version the
    // first has already consumed.
    mutationKey: queryKeys.claims.writes(claimId),
    mutationFn: async (variables: {
      expectedVersion: number;
    }): Promise<ClaimDetail> => {
      const { data } = await api.POST(
        "/claims/{claim_business_id}/assessment/approval",
        {
          params: { path: { claim_business_id: claimId } },
          body: { expectedVersion: variables.expectedVersion },
        },
      );
      return data!;
    },
    onError: (error) => {
      const fresh = freshClaimFrom(error);
      if (!fresh) return;
      client.setQueryData(queryKeys.claims.detail(claimId), fresh);
      // **The checklist is invalidated on the refusal too**, and leaving it out
      // was the defect this pattern exists to prevent — the same one
      // `useApprovePayment.onError` was fixed for on 2026-08-17. A 409 means
      // the entity moved, and the row that offered the button was generated
      // from where it used to be. Without this, the header chip flips to the
      // new status while the row that caused the refusal is still on screen;
      // for a document row it is worse, because the row keeps a
      // `documentVersion` the server has already superseded and every
      // subsequent click refuses again until `staleTime` happens to lapse.
      void client.invalidateQueries({
        queryKey: queryKeys.claims.actions(claimId),
        exact: true,
      });
    },
    onSuccess: (fresh) => afterChecklistWrite(client, claimId, fresh),
    onSettled: () => {
      void client.invalidateQueries({
        queryKey: queryKeys.claims.detail(claimId),
        exact: true,
        refetchType: "none",
      });
    },
  });
}

/**
 * Mark a document reviewed, or confirm one already reviewed (AC 5).
 *
 * `expectedVersion` is the **document's**, published on the checklist row as
 * `documentVersion` — the write compare-and-swaps on the row it changes, so
 * accepting a medical authorization is not refused because somebody corrected
 * an unrelated field on the same claim.
 *
 * `step` is the value the server put on the row. The browser does not decide
 * whether a document is ready to confirm: that rule is
 * `services/claims/assessment.py`'s, and a client that guessed it would offer a
 * button the command refuses.
 */
export function useSetDocumentReview(claimId: string) {
  const client = useQueryClient();

  return useMutation({
    mutationKey: queryKeys.claims.writes(claimId),
    mutationFn: async (variables: {
      documentId: number;
      step: ActionCommand;
      expectedVersion: number;
    }): Promise<ClaimDetail> => {
      const { data } = await api.POST(
        "/claims/{claim_business_id}/documents/{document_id}/review",
        {
          params: {
            path: {
              claim_business_id: claimId,
              document_id: variables.documentId,
            },
          },
          body: {
            expectedVersion: variables.expectedVersion,
            step: variables.step,
          },
        },
      );
      return data!;
    },
    onError: (error) => {
      const fresh = freshClaimFrom(error);
      if (!fresh) return;
      client.setQueryData(queryKeys.claims.detail(claimId), fresh);
      // **The checklist is invalidated on the refusal too**, and leaving it out
      // was the defect this pattern exists to prevent — the same one
      // `useApprovePayment.onError` was fixed for on 2026-08-17. A 409 means
      // the entity moved, and the row that offered the button was generated
      // from where it used to be. Without this, the header chip flips to the
      // new status while the row that caused the refusal is still on screen;
      // for a document row it is worse, because the row keeps a
      // `documentVersion` the server has already superseded and every
      // subsequent click refuses again until `staleTime` happens to lapse.
      void client.invalidateQueries({
        queryKey: queryKeys.claims.actions(claimId),
        exact: true,
      });
    },
    onSuccess: (fresh) => afterChecklistWrite(client, claimId, fresh),
    onSettled: () => {
      void client.invalidateQueries({
        queryKey: queryKeys.claims.detail(claimId),
        exact: true,
        refetchType: "none",
      });
    },
  });
}

/** Record the injury on the OSHA 300 log (AC 5) — `useApproveAssessment`'s shape. */
export function useMarkOshaLogged(claimId: string) {
  const client = useQueryClient();

  return useMutation({
    mutationKey: queryKeys.claims.writes(claimId),
    mutationFn: async (variables: {
      expectedVersion: number;
    }): Promise<ClaimDetail> => {
      const { data } = await api.POST("/claims/{claim_business_id}/osha-log", {
        params: { path: { claim_business_id: claimId } },
        body: { expectedVersion: variables.expectedVersion },
      });
      return data!;
    },
    onError: (error) => {
      const fresh = freshClaimFrom(error);
      if (!fresh) return;
      client.setQueryData(queryKeys.claims.detail(claimId), fresh);
      // **The checklist is invalidated on the refusal too**, and leaving it out
      // was the defect this pattern exists to prevent — the same one
      // `useApprovePayment.onError` was fixed for on 2026-08-17. A 409 means
      // the entity moved, and the row that offered the button was generated
      // from where it used to be. Without this, the header chip flips to the
      // new status while the row that caused the refusal is still on screen;
      // for a document row it is worse, because the row keeps a
      // `documentVersion` the server has already superseded and every
      // subsequent click refuses again until `staleTime` happens to lapse.
      void client.invalidateQueries({
        queryKey: queryKeys.claims.actions(claimId),
        exact: true,
      });
    },
    onSuccess: (fresh) => afterChecklistWrite(client, claimId, fresh),
    onSettled: () => {
      void client.invalidateQueries({
        queryKey: queryKeys.claims.detail(claimId),
        exact: true,
        refetchType: "none",
      });
    },
  });
}

/** The Documents & ID tab (Story 2.5). */
export type DocumentsBlock = components["schemas"]["DocumentsBlockResponse"];
export type RequiredForm = components["schemas"]["RequiredFormResponse"];
export type EmployeeIdCardData =
  components["schemas"]["EmployeeIdCardResponse"];
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
export function useDocumentSheet(claimId: string, documentId: number | null) {
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

/**
 * The AI insight cache (Story 6.2).
 *
 * Four cached narratives per claim, keyed by kind rather than listed, so each
 * slot's `content` is the exact structure that kind stores and each card
 * component is typed all the way from the JSONB column. See
 * `ClaimInsightsResponse` on why the server sends an object.
 *
 * `FraudRiskContent` is a discriminated union on `outcome`, and the browser
 * narrows on that field and on nothing else. The alternative — comparing
 * `fraudScore` against `fraudFlagScoreMin`, both of which are on the payload —
 * would be the client re-deciding a verdict `services/derivations` already
 * reached, which is the one thing AC 3 is about.
 */
export type ClaimInsights = components["schemas"]["ClaimInsightsResponse"];
/**
 * What a refresh answers: the four cards, plus the kinds it could not write.
 *
 * A superset of `ClaimInsights` rather than a separate shape, so the four cards
 * install straight into the query cache — and `failedKinds` rides along, which
 * is what lets the tab say *which* card the model refused instead of leaving it
 * reading "not generated" beside a button that appeared to do nothing (review
 * of Story 6.2, M7).
 *
 * **The extra member does not go into the cache**, and `useRefreshInsights`
 * strips it: the insights key is typed `ClaimInsights`, and writing this shape
 * into it left the cache holding a `failedKinds` no reader declares and no
 * later `GET` would replace — a value that would survive until the entry was
 * evicted, describing a refresh long since finished (follow-up review of Story
 * 6.2, C6).
 */
export type RefreshInsights = components["schemas"]["RefreshInsightsResponse"];
export type InsightKind = components["schemas"]["InsightKind"];
export type SimilarCaseCard = components["schemas"]["SimilarCaseCard"];
export type ReserveAdequacyCard = components["schemas"]["ReserveAdequacyCard"];
export type NextBestActionsCard = components["schemas"]["NextBestActionsCard"];
export type FraudRiskCard = components["schemas"]["FraudRiskCard"];
export type SimilarCaseContent = components["schemas"]["SimilarCaseInsight"];
export type ReserveAdequacyContent =
  components["schemas"]["ReserveAdequacyInsight"];
export type NextBestActionsContent =
  components["schemas"]["NextBestActionsInsight"];
export type FraudRedFlagsContent =
  components["schemas"]["FraudRedFlagsInsight"];
export type FraudLowRiskContent = components["schemas"]["FraudLowRiskInsight"];

/**
 * One claim's four cards — a read of the cache, never a generation.
 *
 * **A query of its own rather than a block on the case file**, which is
 * `useClaimActions`' call for a stronger reason: this payload is model output,
 * it is refreshed on its own schedule by a server-side job, and it is far
 * larger than anything the Overview needs. Folding it into the console's
 * most-fetched response would put four narratives on the wire every time a
 * handler clicked a claim.
 *
 * **A long `staleTime`, unlike every other claim query.** Fifteen seconds is
 * right for a payload cut from rows a handler is editing; an insight is a cache
 * with a visible generation timestamp (AD-10), so re-fetching it every fifteen
 * seconds would spend requests to receive the identical rows until somebody
 * pressed Refresh or a job ran. Five minutes is a compromise between that and
 * a tab that never notices a scheduled run.
 */
export function useClaimInsights(claimId: string | null) {
  return useQuery({
    queryKey: queryKeys.claims.insights(claimId ?? ""),
    queryFn: async (): Promise<ClaimInsights> => {
      const { data } = await api.GET("/claims/{claim_business_id}/insights", {
        params: { path: { claim_business_id: claimId! } },
      });
      return data!;
    },
    enabled: claimId !== null,
    staleTime: 300_000,
  });
}

/**
 * Regenerate this claim's four narratives (AC 1, AC 2's refresh affordance).
 *
 * **The response body *is* the fresh payload**, so it is installed directly
 * rather than triggering a re-fetch — `afterChecklistWrite`'s move, for its
 * reason: a follow-up GET would re-open the read-after-write window that
 * returning the entity closes, and here it would also cost a second request
 * after a call that already took tens of seconds.
 *
 * **It carries no `claims.writes` mutation key**, and that is deliberate rather
 * than an omission. That key drives `useClaimWriteInFlight`, which disables
 * every editable control on the case file while a command is in flight, because
 * those controls all send an `expectedVersion` read from one cached case file.
 * A refresh writes no column of `claim` and bumps no version — `ai_insight` has
 * no version at all — so carrying it would grey out the severity score and the
 * comp-rate input for the length of a model completion, for no reason anybody
 * could see. `queryKeys.meetings` and `queryKeys.diaryNotes` record the same
 * decision for the same reason.
 *
 * Nothing is invalidated beyond this claim's own insights: a narrative is not a
 * claim fact, so no queue card, no top-bar tile and no case file changes
 * because one was regenerated.
 *
 * ## The one exception, and it is an outage (Story 6.6)
 *
 * A 503 here **does** invalidate `queryKeys.copilot.availability`, which is the
 * one key outside this claim that this mutation touches. Story 6.6's rule is
 * that the copilot's model outage is discovered by a probe and rendered as a
 * disabled control, never discovered by pressing something and failing — and
 * the probe is cached server-side and polled every fifteen seconds, so there is
 * a window in which the model has gone down and the Refresh button is still
 * enabled. A handler who presses it inside that window has just learned, at
 * first hand and more reliably than any probe could, that the model server is
 * not answering; leaving the button enabled after that would be inviting them
 * to find out again. Invalidating marks the outage immediately, and the button
 * and the copilot panel both disable off the same cache entry.
 *
 * **Matched on the status and not on a `code`**, deliberately. The stream's
 * closed `StreamErrorCode` vocabulary belongs to the SSE frames; this route
 * predates it and answers a plain `about:blank` problem document, and widening
 * that vocabulary onto an ordinary HTTP route to save a comparison here would
 * be a server change made for a client's convenience. 503 is what this route
 * says "the local model server did not answer" with, and it is the only 503 it
 * has.
 */
export function useRefreshInsights(claimId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<RefreshInsights> => {
      const { data } = await api.POST(
        "/claims/{claim_business_id}/insights/refresh",
        {
          params: { path: { claim_business_id: claimId } },
        },
      );
      return data!;
    },
    onSuccess: (fresh) => {
      // The cards only. `failedKinds` is a fact about *this run*, not about the
      // cache, and the key it would be written under is typed `ClaimInsights`
      // — so it is dropped here rather than left to sit in the entry describing
      // a refresh that finished long ago.
      const {
        similarCaseOutcomes,
        reserveAdequacyReview,
        nextBestActions,
        fraudRiskIndicators,
      } = fresh;
      const key = queryKeys.claims.insights(claimId);
      client.setQueryData<ClaimInsights>(key, {
        similarCaseOutcomes,
        reserveAdequacyReview,
        nextBestActions,
        fraudRiskIndicators,
      });
      // Marked stale without a re-fetch, `afterChecklistWrite`'s pattern:
      // `exact: true` because the key is nested under the case file's, and a
      // prefix invalidation would also re-fetch the case file — which this
      // mutation did not change.
      void client.invalidateQueries({
        queryKey: key,
        exact: true,
        refetchType: "none",
      });
    },
    onError: (error: unknown) => {
      // See the docstring: the press that failed is better evidence than the
      // probe that has not run yet, so the outage is marked now rather than up
      // to fifteen seconds from now. Refetched rather than merely marked stale
      // — unlike the insights key above — because the whole point is that the
      // control disables while the handler is still looking at it.
      if (error instanceof ApiError && error.status === 503) {
        void client.invalidateQueries({
          queryKey: queryKeys.copilot.availability,
          exact: true,
        });
      }
    },
  });
}
