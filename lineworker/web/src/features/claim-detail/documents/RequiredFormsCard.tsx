/**
 * The path-classified statutory forms card (Story 2.5, AC 1) — the
 * prototype's `pathDocsHTML`.
 *
 * **The path arrives decided.** `claim_path` is a registered derivation
 * (AD-10) with its parameters in a rule document, so this component picks a
 * banner style from a key and holds no rule — which is the whole difference
 * from the prototype, whose `c.path || "B"` classifies nothing at all and
 * shows every one of the 100 claims the same four Path B filings.
 *
 * **The forms arrive decided too**, as rows of `path_required_form` in their
 * seeded order. There is no `PATH_DOCS` constant in this codebase's browser:
 * which filings a fatality requires is regulatory data, and a copy of it in
 * TypeScript would be a second place for it to be wrong.
 *
 * **Download links are external, and open in a new tab.** They point at
 * statutory blanks on a regulator's site, not at `BlobStore` content — a
 * different kind of thing from the document rows below, which open the
 * read-only viewer. `rel="noopener noreferrer"` because `target="_blank"`
 * without it hands the opened page a handle on this one.
 */
import type { ClaimPath, RequiredForm } from "@/api/claims";

import { PATH_META } from "./pathMeta";

export function RequiredFormsCard({
  path,
  forms,
}: {
  path: ClaimPath;
  forms: RequiredForm[];
}) {
  const meta = PATH_META[path];

  return (
    <section
      data-testid="required-forms"
      data-path={path}
      className={`mb-[10px] rounded-lg border border-border border-l-4 p-3 ${meta.accent} ${meta.wash}`}
    >
      <h3
        data-testid="required-forms-banner"
        className="font-display text-[11px] font-bold tracking-[0.3px]"
      >
        {meta.icon} {meta.label} — Required Forms
      </h3>
      <p className="mt-1 mb-[10px] text-[11.5px] leading-[1.5] text-muted-text">
        {meta.description}
      </p>

      {/* No empty branch. Every path has forms — the seed migration refuses a
          file that does not carry all nine — so a "no forms required" state
          here would describe a broken migration rather than a claim. */}
      <ul className="flex flex-col gap-[7px]">
        {forms.map((form) => (
          <li
            key={form.formCode}
            data-testid="required-form"
            data-form-code={form.formCode}
            className="flex items-start gap-[10px] rounded border border-border bg-surface px-3 py-[9px]"
          >
            <span
              className={`mt-px shrink-0 rounded-[3px] px-[7px] py-[3px] font-mono text-[9px] font-bold ${meta.chip}`}
            >
              {form.formCode}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[12.5px] font-bold text-text">
                {form.formName}
              </span>
              <span className="mt-[2px] block text-[11px] leading-[1.4] text-muted-text">
                {form.description}
              </span>
              <span className="mt-1 block text-[10px] text-faint">
                ⏱ {form.timing}
              </span>
            </span>
            <a
              href={form.downloadUrl}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="required-form-download"
              className={`shrink-0 rounded-[3px] px-[10px] py-[5px] text-[11px] font-bold whitespace-nowrap no-underline ${meta.chip}`}
            >
              ⬇ Download
              <span className="sr-only"> {form.formCode} (opens in a new tab)</span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
