/**
 * How each handling path is *shown* — the prototype's `PATH_META`, UI-owned.
 *
 * The prototype keeps this object next to `PATH_DOCS`, and the two are
 * different kinds of thing. `PATH_DOCS` is regulatory data (which filings a
 * path requires, when they are due, where the blank lives) and lives in
 * `path_required_form`. This is a label, an icon and a colour — display
 * metadata, which the Enums convention keeps in the browser, exactly as the
 * risk band's labels are here rather than in `RiskBand`.
 *
 * **The colours are tokens, not the prototype's hexes — and they are the same
 * colours.** `PATH_META` writes `#1D7A45` / `#D9F0E4` for Path A, `#1D6A96` /
 * `#DAE9F4` for B and `#C73E2D` / `#FADDDA` for C; those six values are
 * exactly `--ok`/`--okd`, `--st`/`--sld` and `--er`/`--erd` in the prototype's
 * own `:root`. So this is a port rather than a re-design: the same palette,
 * reached by name so that a token change moves the banner with everything
 * else.
 *
 * `Record<ClaimPath, …>` rather than a lookup with a fallback: a fourth path
 * added to the enum then fails the build here, beside the other three, rather
 * than rendering an unstyled banner with `undefined` where the label goes.
 * (The prototype's own `PATH_META[path] || PATH_META.B` fallback is what let
 * its always-Path-B bug stay invisible.)
 * [Source: docs/Workers_Comp_Prototype.html lines 1559-1563]
 */
import type { ClaimPath, DocType } from "@/api/claims";

export interface PathMeta {
  label: string;
  icon: string;
  description: string;
  /** The banner's left border and heading. */
  accent: string;
  /** Its wash. */
  wash: string;
  /** The form-code chip: solid accent, white text. */
  chip: string;
}

export const PATH_META: Record<ClaimPath, PathMeta> = {
  a: {
    label: "Path A — Minor Injury",
    icon: "🟢",
    description:
      "First-aid only. No TTD. OSHA log entry only. Claim closed same day.",
    accent: "border-l-ok text-ok",
    wash: "bg-ok-soft",
    chip: "bg-ok text-white",
  },
  b: {
    label: "Path B — Follow-Up Treatment",
    icon: "🔵",
    description:
      "Medical treatment + possible TTD. Full WC claim lifecycle. RTW coordination required.",
    accent: "border-l-steel text-steel",
    wash: "bg-steel-soft",
    chip: "bg-steel text-white",
  },
  c: {
    label: "Path C — Fatality",
    icon: "🔴",
    description:
      "Fatal workplace injury. Death benefits to dependents. OSHA fatality report within 8 hours.",
    accent: "border-l-error text-error",
    wash: "bg-error-soft",
    chip: "bg-error text-white",
  },
};

/**
 * The short chip a document row is tagged with — the prototype's `di()`.
 *
 * UI-owned labelling over the snake_case `doc_type` enum, and deliberately
 * *not* the same map as `DOC_TYPE_LABEL`: the intake checklist spells a type
 * out because a handler reads it once to find out what is missing, while this
 * is a 3-4 character tag in a 28px column beside the document's own name.
 * `froi → C-1` is the prototype's, and it is the form code rather than an
 * abbreviation of the enum — a first report of injury *is* the C-1.
 * [Source: docs/Workers_Comp_Prototype.html line 1587]
 */
export const DOC_TYPE_CHIP: Record<DocType, string> = {
  froi: "C-1",
  incident: "INC",
  medauth: "MED",
  wage: "WAGE",
  rtw: "RTW",
  legal: "LEG",
};
