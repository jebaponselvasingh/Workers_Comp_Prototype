/**
 * The claim documents list (Story 2.5, AC 3 and AC 5) — the prototype's
 * `.doclist`.
 *
 * One row per document: a type chip, the name, the filing date, and a "View →"
 * affordance that opens the read-only viewer.
 *
 * **Rows are buttons, not divs with click handlers.** The prototype attaches
 * `data-di` to a `div` and delegates, which means the list is unreachable by
 * keyboard and invisible to a screen reader — a document viewer nobody can
 * open without a mouse. A `<button>` gets focus, Enter and Space for free, and
 * its accessible name is the document's own.
 *
 * **The empty state is a branch of its own (NFR-3).** Every seeded claim
 * carries between three and eight documents, so this state is unreachable
 * against the dev seed — which is exactly why it is written and tested rather
 * than left to a `.map` over an empty array. A card that rendered an empty
 * list would read as "this claim's file is empty" only to somebody who already
 * knew the list was the whole story; the sentence says it.
 */
import type { DocumentRow } from "@/api/claims";

import { formatDate } from "../Cards";
import { DOC_TYPE_CHIP } from "./pathMeta";

export function DocumentList({
  documents,
  onOpen,
}: {
  documents: DocumentRow[];
  onOpen: (documentId: number) => void;
}) {
  return (
    <section
      data-testid="document-list"
      className="rounded-lg border border-border bg-surface p-3"
    >
      <h3 className="mb-2 font-display text-[10.5px] font-bold tracking-[0.4px] text-muted-text uppercase">
        Claim documents{" "}
        <span
          data-testid="document-count"
          className="font-sans text-[9px] font-normal tracking-normal normal-case text-faint"
        >
          ({documents.length} files)
        </span>
      </h3>

      {documents.length === 0 ? (
        <p data-testid="document-list-empty" className="text-[11.5px] text-faint">
          No documents on file for this claim.
        </p>
      ) : (
        <ul className="flex flex-col">
          {documents.map((document) => (
            <li key={document.id} className="border-b border-hairline last:border-b-0">
              <button
                type="button"
                data-testid="document-row"
                data-doc-type={document.docType}
                data-document-id={document.id}
                onClick={() => onOpen(document.id)}
                className="flex w-full items-center gap-[10px] py-[7px] text-left hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <span className="inline-flex w-[42px] shrink-0 items-center justify-center rounded-[3px] bg-surface-2 px-1 py-[3px] font-mono text-[9px] font-bold text-muted-text">
                  {DOC_TYPE_CHIP[document.docType]}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11.5px] text-text">
                    {document.name}
                  </span>
                  <span className="block text-[10px] text-faint">
                    Filed {formatDate(document.filedDate)}
                  </span>
                </span>
                <span aria-hidden className="shrink-0 text-[11px] font-bold text-steel">
                  View →
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
