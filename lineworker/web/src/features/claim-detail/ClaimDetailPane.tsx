/**
 * The case-file pane — the centre column of the three-pane workspace
 * (UX-DR3, UX-DR4, UX-DR5).
 *
 * Reads the selected claim from the URL (`useSelectedClaimId`) rather than
 * from a prop, which is what Story 2.1 built that hook for: the queue writes
 * the selection and this pane reads it, so neither owns the other.
 *
 * **The server is now the authority on "is this claim in my caseload".**
 * Story 2.1's placeholder had to answer that from the queue payload it
 * happened to hold, which produced three states — present, absent,
 * unconfirmed — because a filtered or partially-paged queue cannot rule a
 * claim out. `GET /claims/{id}` can: it answers 404 for a claim that is not
 * in the caller's scope and 200 for one that is, whatever the queue is
 * currently showing. So the presence machinery is gone, along with
 * `useLoadedClaimIds`, which existed only to feed it. Keeping both would be
 * two answers to one question, which is the disagreement AD-10 exists to
 * prevent — and the *worse* of the two would be the one that guessed.
 *
 * A consequence worth stating: a failed **queue** no longer affects this
 * pane at all. Story 2.1 had to explain that "we cannot check"; now the case
 * file loads on its own.
 *
 * Four rendered states (NFR-3): nothing selected, loading, error — with the
 * 404 spelled out separately, because "not in your caseload" is a fact and
 * "could not be loaded" is a failure — and the case file itself.
 */
import type { ActionTarget } from "@/api/claims";
import { useClaimDetail } from "@/api/claims";
import { isNotFound } from "@/api/errors";
import { useDiaryNav } from "@/features/diary/DiaryNav";
import { useSelectedClaimId } from "@/features/queue/useSelectedClaim";

import { CaseHeader } from "./CaseHeader";
import { DetailTabs, useDetailTab } from "./DetailTabs";
import { StageStepper } from "./StageStepper";
import { BillsTab } from "./bills/BillsTab";
import { DocumentsTab } from "./documents/DocumentsTab";
import { InjuryTab } from "./injury/InjuryTab";
import { PhotosTab } from "./photos/PhotosTab";
import { IntakeOverview } from "./overview/IntakeOverview";
import { InvestigationOverview } from "./overview/InvestigationOverview";
import { SettledOverview } from "./overview/SettledOverview";
import { TreatmentOverview } from "./overview/TreatmentOverview";

function PaneShell({ children }: { children: React.ReactNode }) {
  return (
    <section
      aria-label="Claim detail"
      data-testid="detail-pane"
      className="min-w-0 flex-1 overflow-y-auto p-4"
    >
      {children}
    </section>
  );
}

function DetailSkeleton() {
  return (
    <div data-testid="detail-skeleton" aria-hidden className="flex flex-col gap-[10px]">
      <span className="block h-6 w-1/3 animate-pulse rounded bg-surface-2" />
      <span className="block h-4 w-1/2 animate-pulse rounded bg-surface-2" />
      <span className="block h-10 w-full animate-pulse rounded bg-surface-2" />
      <span className="block h-40 w-full animate-pulse rounded bg-surface-2" />
    </div>
  );
}

/**
 * The case file for the claim named in `?claim=`.
 *
 * Keyed by claim id at the call site below, so the tab selection resets when
 * the handler moves to a different claim: they are looking at a different
 * case file, and landing on that file's Documents tab implies a continuity
 * that is not there.
 */
function CaseFile({ claimId }: { claimId: string }) {
  const detail = useClaimDetail(claimId);
  const [activeTab, setActiveTab] = useDetailTab();
  const { requestMeetings } = useDiaryNav();

  if (detail.isPending) return <DetailSkeleton />;

  if (detail.isError) {
    // A 404 is an answer, not a failure: this claim is not in the caller's
    // book (or does not exist — the server deliberately does not say which,
    // so that a stale link cannot be used to find out whose claims exist).
    return isNotFound(detail.error) ? (
      <p data-testid="detail-unknown" className="text-sm text-muted-text">
        <span className="font-mono">{claimId}</span> is not in this caseload.
      </p>
    ) : (
      <p role="alert" data-testid="detail-error" className="text-sm text-error">
        ⚠ <span className="font-mono">{claimId}</span> could not be loaded. Try again in a
        moment.
      </p>
    );
  }

  const { header, stepper, overview } = detail.data;

  /**
   * Story 3.5's deep links, and they are the tab state this pane already owns
   * (AC 3: "real navigation … reuse Epic 2's tab state, no bespoke routing").
   *
   * The remaining seam targets never reach here: the server sends them with
   * `enabled: false` and the card renders a disabled control with the sentence
   * naming the story that will enable it. The fallthrough is a no-op rather
   * than a throw, because the seam is a *contract* between two versions of the
   * app — an SPA held open across a deploy that added a target must not blank
   * the pane over it.
   *
   * **`meetings` is the first target that is not a tab** (Story 4.1). It lives
   * in the right pane, so instead of moving this pane's tab state it raises an
   * intent on the context `WorkspaceShell` provides, and the copilot pane's
   * Diary tab picks it up.
   *
   * **Below the `xl` breakpoint the scheduler still opens**, and that is worth
   * stating because the obvious guess is the opposite. The copilot aside is
   * `hidden … xl:flex` — `display: none`, but still *mounted* — so
   * `MeetingsSubTab` is alive behind it, and the Radix dialog it renders
   * portals to `document.body` rather than into the aside. A handler on a
   * narrow window therefore gets a working modal over a workspace whose diary
   * list they cannot see, and a meeting saved from it is written correctly and
   * stays invisible until the window widens. The pane has been `xl`-only since
   * Story 2.1; changing that is not this deep link's decision to take, and the
   * e2e spec pins the behaviour rather than the assumption.
   */
  const navigate = (target: ActionTarget): void => {
    if (target === "overview" || target === "bills" || target === "documents") {
      setActiveTab(target);
      return;
    }
    if (target === "meetings") {
      requestMeetings();
    }
  };

  return (
    <>
      <CaseHeader header={header} />
      <DetailTabs
        activeTab={activeTab}
        onTabChange={setActiveTab}
        injury={<InjuryTab claim={detail.data} />}
        // The claim id rather than the case file: the Bills tab fetches its
        // own payload under its own query key, so the only thing it needs
        // from here is which claim to ask about.
        bills={<BillsTab claimId={claimId} />}
        documents={<DocumentsTab claim={detail.data} />}
        photos={<PhotosTab claim={detail.data} />}
        // The server's number, not the grid's length — see `DetailTabs`. It is
        // read here because the label is rendered whether or not the panel is
        // mounted, and the panel is mounted only while its tab is selected.
        photoCount={detail.data.photos.count}
      >
        {/* AC 2: the stepper is always the first element of Overview. */}
        <StageStepper steps={stepper} />
        {overview.stageVariant === "intake" ? (
          <IntakeOverview claim={detail.data} overview={overview} onNavigate={navigate} />
        ) : overview.stageVariant === "investigation" ? (
          // The whole case file, not just its variant: the inline edits
          // (Story 2.3) send `version` as `expectedVersion` and build their
          // selects from the server's vocabularies, and both live one level
          // up from the block being rendered.
          <InvestigationOverview
            claim={detail.data}
            overview={overview}
            onNavigate={navigate}
          />
        ) : overview.stageVariant === "treatment" ? (
          <TreatmentOverview
            claim={detail.data}
            overview={overview}
            onOpenBills={() => setActiveTab("bills")}
            onNavigate={navigate}
          />
        ) : (
          <SettledOverview claim={detail.data} overview={overview} onNavigate={navigate} />
        )}
      </DetailTabs>
    </>
  );
}

export function ClaimDetailPane() {
  const selectedClaimId = useSelectedClaimId();

  return (
    <PaneShell>
      {selectedClaimId === null ? (
        <p data-testid="detail-none" className="text-sm text-muted-text">
          Select a claim to open its case file.
        </p>
      ) : (
        <CaseFile key={selectedClaimId} claimId={selectedClaimId} />
      )}
    </PaneShell>
  );
}
