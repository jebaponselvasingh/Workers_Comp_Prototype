/**
 * The Documents & ID tab (Story 2.5, UX-DR5) — the prototype's `docsHTML`.
 *
 * Three stacked cards in the prototype's order — required forms, employee ID,
 * claim documents — plus the read-only viewer the third one opens. Story 2.2
 * shipped this tab as an explicit empty state naming this story; that seam is
 * gone from `DetailTabs` and this is what replaced it.
 *
 * **A single column, not `CardGrid`.** The forms card holds nine lines of
 * statutory prose and the ID card is a fixed-width badge; side by side they
 * either wrap badly or squeeze the descriptions into a column too narrow to
 * read. The prototype stacks them for the same reason.
 *
 * **Which document is open is local UI state** (AD-9), like the tab selection
 * above it: it is not a server resource, and it is not shareable the way
 * `?claim=` is — a link to a modal over a claim somebody else cannot see is a
 * link to a 404. It resets with the tab, because `DetailTabs` renders this
 * panel only while its tab is selected.
 */
import { useState } from "react";

import type { ClaimDetail } from "@/api/claims";

import { DocumentList } from "./DocumentList";
import { DocumentViewerDialog } from "./DocumentViewerDialog";
import { EmployeeIdCard } from "./EmployeeIdCard";
import { RequiredFormsCard } from "./RequiredFormsCard";

export function DocumentsTab({ claim }: { claim: ClaimDetail }) {
  const [openDocumentId, setOpenDocumentId] = useState<number | null>(null);
  const { documents } = claim;

  return (
    <div data-testid="documents-tab" className="flex min-w-0 flex-col">
      <RequiredFormsCard path={documents.path} forms={documents.requiredForms} />
      <EmployeeIdCard card={documents.idCard} />
      <DocumentList documents={documents.documents} onOpen={setOpenDocumentId} />

      <DocumentViewerDialog
        claimId={claim.claimId}
        documentId={openDocumentId}
        onClose={() => setOpenDocumentId(null)}
      />
    </div>
  );
}
