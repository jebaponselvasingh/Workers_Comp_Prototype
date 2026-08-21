/**
 * The drill-through's filter vocabulary — the browser's one copy of it
 * (Story 5.5, widened by 7.1 and 7.2).
 *
 * Twenty facets, and three different places have to agree about them: the URL
 * a supervisor can bookmark and share, the request the client sends, and the
 * TanStack Query key the answer is cached under. Written out three times they
 * would agree until the first facet was added, so they are written out once —
 * here — and derived everywhere else.
 *
 * **The URL uses the API's own `filter[…]` names.** `?filter[severityBand]=high`
 * is both what the address bar shows and what goes on the wire, so there is no
 * translation step between the two and therefore nothing for a translation step
 * to get wrong. It also means the server's `appliedFilters` — whose `key` is
 * that same string — can be handed straight back to a chip's ✕ as the parameter
 * to delete.
 *
 * **Pure functions only.** No comparison against a number, no arithmetic, no
 * sort, no accumulation: this module reads a `URLSearchParams` and writes one.
 * `noDerivation.test.ts` walks `features/dashboard/` recursively and names this
 * file, which is the guard's answer to "the filter layer looks like plumbing so
 * nobody will look at it".
 *
 * **The labels are the UI's, and the server sends only two of them.** Eighteen
 * facets carry a value this console already has copy for — the four enums have
 * label maps the case file and the queue already share, the five booleans are
 * the KPI cards' own names, `injuryType`/`state`/`sector` are free text where the
 * stored value *is* the label, and the four date bounds are ISO dates a browser
 * formats to its own locale. The two id-valued facets cannot be labelled from
 * an id at all, so `AppliedFilterResponse.display` carries their names and the
 * rule is `display ?? UI_LABEL[key][value] ?? value`. That split is the
 * server's, argued at `AppliedFilterResponse`; this file is the client half of
 * it.
 */
import type { ReturnStatus, RiskBand, Stage } from "@/api/claims";
import type { AppliedFilter } from "@/api/dashboard";
import type { paths } from "@/api/schema";
import { DISABILITY_LABEL } from "@/features/claim-detail/labels";

/**
 * The twenty facet names, in the order a chip row draws them.
 *
 * The same order the server publishes `appliedFilters` in, and the same order
 * the route declares its parameters in — so a URL built here, a chip row drawn
 * from the response, and a reviewer reading the OpenAPI document all see one
 * sequence.
 *
 * **Story 7.1's two are appended, and the position is load-bearing.** The
 * server's `FILTER_KEYS` is read off `DrillFilters`' field order and the two
 * arrived at the end of that dataclass; inserting `fraudBand` beside
 * `fraudFlagged` here — where it reads more naturally — would put the chip row
 * out of step with the server's `appliedFilters` on every URL carrying both.
 */
export const FILTER_KEYS = [
  "stage",
  "severityBand",
  "fraudFlagged",
  "litigation",
  "surgery",
  "oshaRecordable",
  "recoveryStatus",
  "injuryType",
  "state",
  "employerId",
  "handlerId",
  "priority",
  "fraudBand",
  "siuReview",
  // Story 7.2's six, appended for exactly the reason 7.1's two were, and the
  // order within them is the server's `DrillFilters` field order rather than
  // anything read off this screen. Four are a new *kind* of facet — an
  // inclusive date bound on one of the two anchors the Trends section buckets
  // by — and the two anchors are two pairs over two columns on purpose: a point
  // on the DOI series opened with `filter[fnolFrom]` returns a plausible list
  // of the wrong claims. The last two are the cohort split's own columns.
  "fnolFrom",
  "fnolTo",
  "doiFrom",
  "doiTo",
  "disability",
  "sector",
] as const;

export type FilterKey = (typeof FILTER_KEYS)[number];

/**
 * The set of facets a view is showing, as a partial record.
 *
 * Values are strings because that is what a query string holds: `"true"`,
 * `"high"`, `"3"`. Deliberately *not* a typed union per key — the browser has
 * no business validating a facet's vocabulary, which is the server's 422 (see
 * `DrillFilters` on the Python side), and a client-side enum here would be a
 * second copy of one that could only ever be more out of date.
 *
 * **A whole set rather than a single `{key, value}` pair**, which is what the
 * spec's task named, and the difference is load-bearing on the KPI cards: Total
 * Claims, Total Paid and Total Reserve all open the *unfiltered* list, which a
 * single-facet type cannot express (see `KpiCard.drill`). One type for one facet
 * and another for a set would be two ways to say the same thing, so there is
 * one — a set of size zero, one or many.
 */
export type DrillFilters = Partial<Record<FilterKey, string>>;

/** The query-string parameter name for one facet — the API's own alias. */
export function paramNameOf(key: FilterKey): string {
  return `filter[${key}]`;
}

/**
 * The facets present in a `URLSearchParams`, in `FILTER_KEYS` order.
 *
 * Unknown parameter names are dropped rather than carried, which mirrors the
 * server ignoring them: a URL with `?sort=-severity` produces no chip because
 * it produces no narrowing.
 *
 * `""` is treated as absent — `useSelectedClaim`'s ruling, for its reason. A
 * blank value comes from a hand-edited URL or a link that lost its value, and
 * `filter[stage]=` is a facet nobody chose; left in, it would draw a chip with
 * no label and send a parameter the server would refuse with a 422.
 */
export function fromSearchParams(params: URLSearchParams): DrillFilters {
  const filters: DrillFilters = {};
  for (const key of FILTER_KEYS) {
    const raw = params.get(paramNameOf(key));
    if (raw !== null && raw !== "") filters[key] = raw;
  }
  return filters;
}

/**
 * A filter set as query-string parameters, in `FILTER_KEYS` order.
 *
 * Returns a fresh `URLSearchParams` rather than mutating one, so a caller
 * building a link and a caller replacing the current URL use the same function
 * and neither can leave a stale facet behind.
 */
export function toSearchParams(filters: DrillFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if (value !== undefined) params.set(paramNameOf(key), value);
  }
  return params;
}

/**
 * The generated client's query object for a filter set.
 *
 * Spelled out key by key rather than spread from `filters`, because the
 * generated `query` type names all twenty parameters and building it from a
 * loop would need a cast — and a cast here is exactly where a facet renamed on
 * the server would stop failing to compile. Twenty lines, and each of them is a
 * line a rename breaks.
 *
 * **The return type is the generated one, and that is the half that was
 * missing.** Annotated `Record<string, string | undefined>`, the twenty lines
 * above bought nothing: the annotation erases the literal keys, so renaming
 * `filter[oshaRecordable]` on the server, regenerating and running `typecheck`
 * stayed green — and the SPA then sent a parameter name FastAPI ignores,
 * `appliedFilters` came back empty, and the OSHA card silently opened the
 * unfiltered book with no chip. `DrillQuery` is the generated shape, so a
 * renamed, added or removed facet is now a compile error at this call site,
 * which is the only place the browser spells these names.
 */
/**
 * The query object `GET /dashboard/claims` publishes, straight from the
 * generated document — never a hand-written restatement of it.
 */
type DrillQuery = NonNullable<paths["/dashboard/claims"]["get"]["parameters"]["query"]>;

export function toQueryParams(filters: DrillFilters): DrillQuery {
  return {
    "filter[stage]": enumValue(filters.stage) as DrillQuery["filter[stage]"],
    "filter[severityBand]": enumValue(filters.severityBand) as DrillQuery["filter[severityBand]"],
    "filter[fraudFlagged]": boolValue(filters.fraudFlagged),
    "filter[litigation]": boolValue(filters.litigation),
    "filter[surgery]": boolValue(filters.surgery),
    "filter[oshaRecordable]": boolValue(filters.oshaRecordable),
    "filter[recoveryStatus]": enumValue(
      filters.recoveryStatus,
    ) as DrillQuery["filter[recoveryStatus]"],
    "filter[injuryType]": filters.injuryType,
    "filter[state]": filters.state,
    "filter[employerId]": idValue(filters.employerId),
    "filter[handlerId]": idValue(filters.handlerId),
    "filter[priority]": boolValue(filters.priority),
    "filter[fraudBand]": enumValue(filters.fraudBand) as DrillQuery["filter[fraudBand]"],
    "filter[siuReview]": boolValue(filters.siuReview),
    // Story 7.2's six. `dateValue` rather than a pass-through for the same
    // reason `enumValue` exists one line above: a hand-edited URL can hold any
    // string, only the server can say whether it is a date, and it does — with a
    // 422 the list renders as a clearable chip. The four are spelled out
    // individually rather than looped for this function's founding reason: each
    // line is a line a rename breaks.
    "filter[fnolFrom]": dateValue(filters.fnolFrom),
    "filter[fnolTo]": dateValue(filters.fnolTo),
    "filter[doiFrom]": dateValue(filters.doiFrom),
    "filter[doiTo]": dateValue(filters.doiTo),
    "filter[disability]": enumValue(filters.disability) as DrillQuery["filter[disability]"],
    "filter[sector]": filters.sector,
  };
}

/**
 * A URL string as the wire's boolean, or absent.
 *
 * A `DrillFilters` holds strings because a URL holds strings; the generated
 * query type holds the types the route declares. Converting here rather than
 * storing typed values keeps one parse at the edge — `fromSearchParams` — and
 * `=== "true"` rather than a truthiness test because every non-empty string is
 * truthy, `"false"` loudest among them.
 */
function boolValue(raw: string | undefined): boolean | undefined {
  return raw === undefined ? undefined : raw === "true";
}

/**
 * A URL string as the wire's integer id, or absent.
 *
 * `Number` rather than `parseInt`: `parseInt("3abc")` is `3`, which would send a
 * silently different question than the one in the address bar. `NaN` is passed
 * through as-is and refused by the server's own validation with a 422, which is
 * where a malformed id belongs — the browser second-guessing it is the parse
 * this file exists to avoid duplicating.
 */
function idValue(raw: string | undefined): number | undefined {
  return raw === undefined ? undefined : Number(raw);
}

/**
 * A URL string as one of the wire's enum members, or absent.
 *
 * The cast is unavoidable and deliberately narrow: a hand-edited URL can hold
 * any string, and only the server can say whether it is a member — which it
 * does, with a 422 the list renders as a clearable chip beside an honest
 * message. Validating the vocabulary here too would be a second copy of an enum
 * that changes on the server, and the first copy to go stale.
 */
function enumValue(raw: string | undefined): string | undefined {
  return raw;
}

/**
 * A URL string as the wire's ISO date, or absent (Story 7.2).
 *
 * A pass-through, and it is a *named* pass-through rather than the bare field
 * for the reason `enumValue` is: the four date facets are filled from a bucket's
 * published `bucketFrom`/`bucketTo` — a copy of the boundary the server's fold
 * used — and never from a date this file constructed. Naming the conversion is
 * where that rule is written down, and it is where a `new Date(...)` would have
 * to be argued for if anybody ever reached for one. Validation stays the
 * server's 422, exactly as it is for the enums: a second date parser here would
 * be the first copy to go stale, and it would have to agree with FastAPI's about
 * what `2026-02-30` means.
 */
function dateValue(raw: string | undefined): string | undefined {
  return raw;
}

/**
 * The URL's own filter set, shaped like the server's echo of it.
 *
 * The chips normally render `appliedFilters` — the server's reading of the
 * request, which is the only thing that can resolve the two id facets to names.
 * But a **refused** request has no payload to echo, and the refusal a
 * supervisor can actually reach is a stale or hand-edited link carrying a value
 * an enum no longer has (`?filter[stage]=banana` → 422). That is precisely the
 * case where the story requires the filter to be "visible and clearable": with
 * no chips there is no ✕, and the only way out of a permanent error is the
 * address bar.
 *
 * So the URL stands in for the echo when there is no echo. `display` is `null`
 * throughout — this side has no names, only what was asked — which is the same
 * degraded chip the server sends for an id it will not confirm.
 */
export function appliedFromFilters(filters: DrillFilters): AppliedFilter[] {
  return FILTER_KEYS.flatMap((key) =>
    filters[key] === undefined ? [] : [{ key, value: String(filters[key]), display: null }],
  );
}

/**
 * A stable string identifying one filter set, for a TanStack Query key.
 *
 * `toSearchParams().toString()` rather than `JSON.stringify(filters)`, because
 * the second is key-insertion-ordered: two renders that set the same two facets
 * in a different order would produce two cache entries for one resource, and
 * nothing anywhere would say so. This goes through the same ordered builder the
 * URL does, so the key and the address bar cannot disagree about identity.
 */
export function toFilterKey(filters: DrillFilters): string {
  return toSearchParams(filters).toString();
}

/** The path the drill-through list lives at. */
export const DRILL_LIST_PATH = "/dashboard/claims";

/** A `to` for the list under one filter set — `""` search for no filters. */
export function drillHref(filters: DrillFilters): string {
  const query = toSearchParams(filters).toString();
  return query === "" ? DRILL_LIST_PATH : `${DRILL_LIST_PATH}?${query}`;
}

/**
 * Where a read-only claim view should send its Back control.
 *
 * Carried in router state rather than in the URL, because it is not part of the
 * claim's identity: two supervisors opening `WC-0031` from different lists are
 * looking at the same page, and a `?from=` would make them two cache entries
 * and two shareable links to one thing.
 *
 * The consequence is deliberate and worth stating: state does not survive a
 * paste or a reload, so a cold-loaded claim URL falls back to the unfiltered
 * list. That is the honest answer — nothing in a bare claim URL says which
 * narrowing produced it — and it is the AC 4 path, where the supervisor typed
 * the address herself.
 */
export interface DrillOrigin {
  /** A full `pathname?search` to return to. */
  from: string;
}

/** A `to` for the read-only view of one claim. */
export function claimHref(claimId: string): string {
  return `${DRILL_LIST_PATH}/${claimId}`;
}

/**
 * What a chip says the facet *is*, before its value.
 *
 * The card's or the chart's own words, so a supervisor recognises the thing she
 * clicked: "Severity" is the High Risk card's dimension.
 *
 * **`stage` is "Stage" and deliberately not "Status", even though the donut it
 * often comes from is titled "Settlement status".** `status` is a different
 * stored column with a different value set — `settled_closed` holds 54 claims
 * where `stage == settled` holds 62 — and this story's server, oracle and test
 * names spend paragraphs keeping the two apart. Labelling the stage "Status"
 * would put the confusion back in the one string a supervisor actually reads,
 * on a list whose count only makes sense as a stage. "Stage" is also the
 * stepper's word on every claim she opens, so it is the recognisable one.
 */
export const FILTER_LABEL: Record<FilterKey, string> = {
  stage: "Stage",
  severityBand: "Severity",
  fraudFlagged: "Fraud flags",
  litigation: "Litigation",
  surgery: "Surgery required",
  oshaRecordable: "OSHA recordable",
  recoveryStatus: "Recovery",
  injuryType: "Injury type",
  state: "State",
  employerId: "Employer",
  handlerId: "Handler",
  priority: "Priority worklist",
  // Story 7.1's two, and they are named for the *surface* rather than the rule,
  // per this map's own convention: "Fraud band" is the analyst workspace's
  // distribution and "SIU review" is its pipeline. Both sit beside "Fraud flags"
  // above, which is a third population over the same two columns — the chips are
  // the one place a reader sees all three side by side, so they have to be
  // distinguishable at a glance rather than three spellings of "fraud".
  fraudBand: "Fraud band",
  siuReview: "SIU review",
  // Story 7.2's six, named for the *surface* per this map's convention. The
  // four date bounds say which anchor they narrow rather than which column they
  // read, because a supervisor who clicked a point on the DOI series has to be
  // able to see that the list is narrowed on the injury date and not on the
  // filing date — the two are weeks apart on a third of the seeded book, and a
  // chip reading only "From" would make the two drills indistinguishable. "FNOL"
  // is the top bar's and the SLA strip's word for the same date, so it is the
  // recognisable one.
  fnolFrom: "FNOL from",
  fnolTo: "FNOL to",
  doiFrom: "Injury from",
  doiTo: "Injury to",
  disability: "Disability",
  sector: "Sector",
};

/** The fraud-score bands' copy, without the edges the legend quotes. */
const FRAUD_BAND_VALUE_LABEL: Record<string, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
};

/** The settlement donut's copy, which is the KPI cards' copy. */
const STAGE_VALUE_LABEL: Record<Stage, string> = {
  settled: "Settled & Closed",
  treatment: "Under Treatment",
  intake: "Intake",
  investigation: "Investigation",
};

/**
 * The severity donut's three bands, without the boundary the legend quotes.
 *
 * Exported since Story 7.2, `RECOVERY_LABEL_BY_STATUS`' reason one map down: the
 * Trends section's cohort legend splits on this same band, and an analyst who
 * clicks the "Medium" line must not land on a chip reading `med`. One map, two
 * renderings — and it lives here rather than in `claim-detail/labels.ts` because
 * that module spells the gauge's abbreviation ("Med") for a 68px arc, which is
 * the wrong word in a sentence.
 */
export const RISK_LABEL_BY_BAND: Record<RiskBand, string> = {
  high: "High",
  med: "Medium",
  low: "Low",
};

/**
 * The recovery-status bars' copy (`renderSV`, line 1094).
 *
 * Keyed by `ReturnStatus`, and **deliberately not**
 * `claim-detail/labels.ts`'s `RECOVERY_LABEL`, which is keyed by
 * `RecoveryWindow` — a recovery *window* is "6-8 Weeks" and a recovery *status*
 * is "Under Therapy". Two enums, similar names, and reusing the wrong one would
 * type-check nowhere and confuse a reader everywhere, which is why the
 * difference is written down rather than left to the type checker.
 *
 * It lives here rather than in `charts/PortfolioCharts.tsx`, where it was
 * written, because the chip and the bar have to say the same words: a
 * supervisor who clicks "Under Therapy" must not land on a chip reading
 * "returned_and_under_therapy". `PortfolioCharts` imports it from here, and the
 * dependency points that way — this module reaches for nothing in `charts/`, so
 * there is no cycle.
 */
export const RECOVERY_LABEL_BY_STATUS: Record<ReturnStatus, string> = {
  returned_and_fully_recovered: "Fully Recovered",
  under_treatment: "Under Treatment",
  returned_and_under_therapy: "Under Therapy",
};

/** A boolean facet reads as its own name — "Fraud flags: Yes". */
const BOOLEAN_VALUE_LABEL: Record<string, string> = {
  true: "Yes",
  false: "No",
};

/**
 * Wire value → display label, per facet, for the ten the client owns.
 *
 * A lookup with a fallback rather than an exhaustive `Record` per key, because
 * the *value* arrives as a string off a URL a person can edit: `filter[stage]=
 * banana` is refused by the server with a 422, but the chip is drawn from the
 * URL before that answer comes back, and a map access on an unknown key would
 * render `undefined`. `chipLabel` falls back to the raw value, which is both
 * honest and the same thing the server's `display: null` means for free text.
 *
 * The two id facets are absent on purpose: their label is the payload's
 * `display`, resolved server-side off rows it had already read, because nothing
 * here can turn `handlerId=4` into a name.
 */
const VALUE_LABEL: Partial<Record<FilterKey, Record<string, string>>> = {
  stage: STAGE_VALUE_LABEL,
  severityBand: RISK_LABEL_BY_BAND,
  recoveryStatus: RECOVERY_LABEL_BY_STATUS,
  fraudFlagged: BOOLEAN_VALUE_LABEL,
  litigation: BOOLEAN_VALUE_LABEL,
  surgery: BOOLEAN_VALUE_LABEL,
  oshaRecordable: BOOLEAN_VALUE_LABEL,
  priority: BOOLEAN_VALUE_LABEL,
  fraudBand: FRAUD_BAND_VALUE_LABEL,
  siuReview: BOOLEAN_VALUE_LABEL,
  // Story 7.2's one labelled facet. **Imported rather than restated**, which is
  // the opposite of what `stage` two entries up does and for a reason worth
  // stating: `stage` genuinely reads differently on a case file ("Settled") and
  // on this dashboard ("Settled & Closed"), so two maps are two audiences. A
  // disability is "Temporary" or "Permanent" wherever it appears, and a second
  // copy of two words would be two words free to drift. The other five of the
  // six are absent on purpose — the four date bounds are ISO dates a chip prints
  // as sent (the server's own ruling on `AppliedFilterResponse.display`), and
  // `sector` is free text where the stored value *is* the label.
  disability: DISABILITY_LABEL,
};

/**
 * One chip's text: `display ?? UI_LABEL[key][value] ?? value`.
 *
 * The order is the contract stated on `AppliedFilterResponse`: the server's
 * resolved name wins where it sent one (the two ids), this file's copy wins for
 * the enums and booleans, and free text falls through as itself because the
 * stored value *is* the label.
 */
export function chipLabel(key: FilterKey, value: string, display: string | null): string {
  const label = FILTER_LABEL[key];
  const text = display ?? VALUE_LABEL[key]?.[value] ?? fallbackValueLabel(key, value);
  return `${label}: ${text}`;
}

/**
 * What an id-valued facet says when the server sent no name for it.
 *
 * `display` is `null` for the two id facets whenever no row in the caller's
 * book carries that id — which is exactly the AD-7 case, a supervisor pasting
 * an employer id from outside her scope, and also the innocent case of an
 * employer with no claims today. The server cannot tell her which without
 * answering "does this exist?", so it sends neither name.
 *
 * Falling through to the bare value printed "Employer: 3", and an integer names
 * nothing — while the acceptance criterion for that exact paste is an empty
 * state that *names the active filter*. `#3` is not a name either, but it reads
 * as an identifier rather than as a count or a rank, which is the confusion the
 * bare number invites. Every other facet's stored value is already its label
 * and falls through unchanged.
 */
function fallbackValueLabel(key: FilterKey, value: string): string {
  return key === "employerId" || key === "handlerId" ? `#${value}` : value;
}
