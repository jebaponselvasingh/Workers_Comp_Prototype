/**
 * The WC glossary slide-in (UX-DR10, FR-GLOS-1, AC 1 & 2).
 *
 * Replaces the prototype's `#glov` overlay and `renderGloss()` pair: same
 * right-hand panel over a click-to-close backdrop, same row anatomy (term,
 * abbreviation chip, muted definition, hairline dividers), and the same
 * no-match copy echoing what was typed. What is *not* the same is where the
 * 25 terms come from — `glossary_term` reference data over
 * `GET /api/glossary`, not a literal in the page — and one named deviation
 * in the predicate: the query is trimmed before matching (see `matches()`).
 *
 * **Why the search runs in the browser.** AD-7 requires every filter that
 * decides *what a caller may see* to happen behind FastAPI. This one does
 * not: glossary terms carry no employer scope and no PHI, the endpoint
 * hands every authenticated persona the identical list, and the caller
 * already holds all 25 rows. Narrowing text the user already has is
 * rendering, not authorization — the story's sanctioned exception. It does
 * not generalise: claim data is scoped, so its filters stay on the server.
 *
 * This component owns the trigger button's *wrapper*, not the button: the
 * top bar passes its own 📖 Glossary button as a child so the bar keeps its
 * layout, while `SheetTrigger asChild` makes Radix's focus return (Task 3's
 * "focus returns to the button on close") structural rather than a side
 * effect of whatever happened to be focused.
 *
 * Deliberately absent: any authoring or admin affordance (nothing in scope
 * writes a term), and any link between a term and a claim (the ERD's dotted
 * association has no story behind it).
 */
import { useState } from "react";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

import type { GlossaryTerm } from "@/api/glossary";
import { useGlossary } from "@/api/glossary";

interface GlossaryPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The top bar's 📖 Glossary button, used as the Radix trigger. */
  children: React.ReactNode;
}

/**
 * The prototype's predicate with one named deviation: case-insensitive
 * substring across abbreviation OR term OR definition, and an empty query
 * matches everything. Definitions are searched too — that is how
 * "audiogram" finds NIHL, and it is the half of FR-GLOS-1 an
 * abbreviation-only search would quietly drop.
 *
 * **The deviation: the query is trimmed** (the caller passes `q`, already
 * `query.trim().toLowerCase()`). The prototype's `renderGloss` does not
 * trim, so there `"   "` filters to nothing and `"havs "` misses the term
 * it obviously means. Terms are pasted out of adjuster emails and claim
 * notes with a trailing space attached, and answering "no matches" to that
 * is the same lie NFR-3 exists to prevent. Kept deliberately, tested
 * deliberately (`GlossaryPanel.test.tsx`), and echoed back *untrimmed* in
 * the no-match line so the user still sees exactly what they typed.
 */
function matches(term: GlossaryTerm, query: string): boolean {
  return (
    term.abbreviation.toLowerCase().includes(query) ||
    term.term.toLowerCase().includes(query) ||
    term.definition.toLowerCase().includes(query)
  );
}

function TermRow({ term }: { term: GlossaryTerm }) {
  return (
    <div data-testid="glossary-term" className="border-b border-hairline py-[9px]">
      <div className="flex items-center gap-1.5 font-display text-[12.5px] font-bold">
        <span data-testid="glossary-term-name">{term.term}</span>
        <span
          data-testid="glossary-term-abbr"
          className="rounded-[3px] bg-steel-soft px-[5px] py-px font-mono text-[9.5px] text-steel"
        >
          {term.abbreviation}
        </span>
      </div>
      <div
        data-testid="glossary-term-definition"
        className="mt-[3px] text-[11.5px] leading-[1.5] text-muted-text"
      >
        {term.definition}
      </div>
    </div>
  );
}

function RowSkeleton() {
  return (
    <div
      data-testid="glossary-skeleton"
      aria-hidden
      className="border-b border-hairline py-[9px]"
    >
      <span className="block h-[13px] w-1/2 animate-pulse rounded bg-surface-2" />
      <span className="mt-[5px] block h-[11px] w-full animate-pulse rounded bg-surface-2" />
    </div>
  );
}

export function GlossaryPanel({ open, onOpenChange, children }: GlossaryPanelProps) {
  const [query, setQuery] = useState("");
  // Gated on `open`: this component is mounted for the whole session
  // because it wraps the top bar's button, but most sessions never open the
  // panel, and reference data nobody looked at is not worth a request.
  const glossary = useGlossary(open);

  const q = query.trim().toLowerCase();
  const terms = glossary.data?.items ?? [];
  const shown = q ? terms.filter((term) => matches(term, q)) : terms;

  // What a screen reader hears when the list changes under it. Filtering is
  // the one interaction here with no visible focus change and no announced
  // consequence: a sighted user sees 25 rows collapse to one, a screen
  // reader user hears nothing at all unless we say it. The live region is
  // this one line — deliberately *not* the results container itself, which
  // would re-announce all 25 rows on every keystroke and make the panel
  // unusable rather than merely silent.
  const announcement = glossary.isPending
    ? "Loading glossary terms."
    : glossary.isError
      ? // The error paragraph is a `role="alert"` of its own; announcing it
        // twice is worse than announcing it once.
        ""
      : terms.length === 0
        ? "No glossary terms are available."
        : q
          ? `${shown.length} of ${terms.length} terms match.`
          : `${terms.length} terms.`;

  function handleOpenChange(next: boolean): void {
    // The prototype calls `renderGloss("")` on open. Reopening onto a
    // stale filter would look like a glossary missing two dozen terms,
    // which is a worse failure than losing a query nobody asked us to keep.
    if (next) setQuery("");
    onOpenChange(next);
  }

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetTrigger asChild>{children}</SheetTrigger>
      <SheetContent
        side="right"
        data-testid="glossary-panel"
        overlayProps={{ "data-testid": "glossary-backdrop" }}
        className="w-[390px] max-w-[92vw] gap-0 border-l border-border bg-surface p-0 sm:max-w-[390px]"
      >
        <SheetHeader className="flex-row items-center justify-between border-b border-border px-4 py-[13px]">
          {/* Verbatim from the prototype's `#glov` header. The story text
              says "WC Glossary"; the prototype is this story's declared
              data and UX contract, so the subtitle stays. */}
          <SheetTitle className="font-display text-[15px] font-bold text-text">
            WC Glossary — Manufacturing
          </SheetTitle>
          <SheetDescription className="sr-only">
            Workers&apos; compensation terms used across this console. Search by abbreviation,
            term, or definition text.
          </SheetDescription>
        </SheetHeader>

        <div className="border-b border-border px-[14px] py-2">
          <input
            data-testid="glossary-search"
            // `text`, not `search`: Blink and WebKit draw a native clear ✕
            // inside a search input, and a second ✕ two inches from the one
            // that dismisses the panel is an invitation to close the
            // glossary when you meant to clear the query. The prototype's
            // input is a plain text field for the same visual result.
            type="text"
            // Radix focuses the first tabbable element in the panel on
            // open, and this is it — the prototype autofocuses the search
            // box, so a user can start typing without reaching for a mouse.
            aria-label="Search terms"
            placeholder="Search terms…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="w-full rounded-[20px] border border-border bg-surface-2 px-[11px] py-[7px] text-xs outline-none focus:border-steel"
          />
        </div>

        <p
          role="status"
          aria-live="polite"
          data-testid="glossary-announcement"
          className="sr-only"
        >
          {announcement}
        </p>

        <div
          className="flex-1 overflow-y-auto px-[14px] pt-[5px] pb-6"
          // The skeletons are `aria-hidden`, so without this the list is an
          // empty region while the terms are in flight — indistinguishable
          // from a glossary with nothing in it.
          aria-busy={glossary.isPending}
        >
          {glossary.isPending ? (
            Array.from({ length: 6 }, (_, index) => <RowSkeleton key={index} />)
          ) : glossary.isError ? (
            // Never an invented term list (NFR-3). A glossary that renders
            // empty on failure teaches a new handler that the term they
            // looked up does not exist.
            //
            // `alert`, not `status`: this message *replaces* everything the
            // panel was opened for. A polite region waits for a lull that a
            // user typing into the search box never gives it.
            <p role="alert" data-testid="glossary-error" className="py-[18px] text-xs text-error">
              ⚠ The glossary could not be loaded. Try again in a moment.
            </p>
          ) : terms.length === 0 ? (
            // Tested *before* the query, and that order is the whole point.
            // A successful `{items: [], total: 0}` — 0006 applied without
            // 0007, a `downgrade 0006`, a wiped table — with a query typed
            // would otherwise read `No matches for "mmi".`, which tells a
            // handler the term does not exist. That is the same lie the
            // error branch above exists to avoid, arriving through a 200.
            <p data-testid="glossary-unavailable" className="py-[18px] text-xs text-faint">
              No glossary terms are available.
            </p>
          ) : shown.length === 0 ? (
            <p data-testid="glossary-empty" className="py-[18px] text-xs text-faint">
              {/* The query is echoed exactly as typed, untrimmed — it
                  confirms what was actually searched for, which is the
                  whole job of this line. */}
              {`No matches for "${query}".`}
            </p>
          ) : (
            // Keyed on the abbreviation because `GlossaryTermResponse`
            // withholds both `id` and `sortOrder`, and the database makes
            // that safe rather than lucky: `uq_glossary_term_abbreviation`
            // (migration 0006) is what lets the wire payload stay free of a
            // surrogate id and still be keyable.
            shown.map((term) => <TermRow key={term.abbreviation} term={term} />)
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
