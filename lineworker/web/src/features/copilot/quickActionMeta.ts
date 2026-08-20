/**
 * How each quick action is *shown* — the prototype's `QAS` array, UI-owned.
 *
 * Named `quickActionMeta.ts` rather than `quickActions.ts`, which is
 * `pathMeta.ts`'s name and its reason one file over: this is display metadata
 * beside a component called `QuickActions.tsx`, and two files whose names differ
 * only in the case of one letter are two files a case-insensitive filesystem
 * cannot tell apart — TypeScript refuses the program outright ("differs from
 * already included file name only in casing"), which is a build failure rather
 * than a preference.
 *
 * The server's contract is seven snake_case keys and nothing else. The glyph,
 * the label and the one-line hint under it are display metadata and live here,
 * which is the Enums convention applied to the copilot: `pathMeta.ts` keeps the
 * handling paths' labels and colours in the browser for the same reason, and
 * `RiskBand`'s labels are here rather than on the enum. What crosses the wire is
 * `quickAction: "reserve"`; the ✓ never does.
 *
 * That matters more than it looks. A `§` or a `↻` in a Python string literal is
 * a glyph nobody can review in the place it renders, it has to survive JSON,
 * logging and a database column that has no business holding it, and the day
 * somebody wants "Reserve check" instead of "Reserve review" it is a server
 * deploy rather than a text edit.
 *
 * **`Record<QuickActionKey, …>` rather than a lookup with a fallback**, which is
 * `PATH_META`'s recorded lesson: an eighth key added to the union then fails the
 * build here, beside the other seven, rather than rendering a button with
 * `undefined` where the label goes. (The prototype's own
 * `PATH_META[path] || PATH_META.B` fallback is what let its always-Path-B bug
 * stay invisible.)
 *
 * The order below is the prototype's own, top to bottom, and it is deliberate
 * rather than alphabetical: the two research actions first, the letter, then the
 * three judgement calls, then provenance last.
 * [Source: docs/Workers_Comp_Prototype.html lines 1648-1656]
 */

/**
 * The seven keys, as a union — the client half of the server's dispatch map.
 *
 * Spelled here rather than derived from `schema.d.ts`, because the generated
 * type for `quickAction` is `string | null`: the server declares the key as a
 * validated string rather than as an enum, since the authority on which keys
 * exist is `agents/graph.py::QUICK_ACTIONS` and OpenAPI would only ever carry a
 * copy of it. So this union is the copy the *browser* needs, and the two are
 * kept honest by the e2e spec, which clicks every button in this map and
 * asserts the server answered — a key that drifted would 422 there rather than
 * fail silently in a component test with a stubbed API.
 */
export type QuickActionKey =
  | "laborlaw"
  | "similar"
  | "rtw"
  | "reserve"
  | "fraud"
  | "nextactions"
  | "data_alignment";

export interface QuickActionMeta {
  /** The prototype's glyph. One character, decorative — `aria-hidden`. */
  icon: string;
  /** What the button says, and what is sent as the run's message. */
  label: string;
  /** The prototype's second line: what pressing it will actually do. */
  hint: string;
}

/**
 * Key → how it renders. The button strip reads this in `Object.entries` order.
 *
 * `label` doubles as the message body the run carries, which is why it reads as
 * a request rather than as a caption. The server routes on the key and never on
 * the message — the label is what lands in the transcript above the answer, so a
 * handler scrolling back sees what they asked for rather than a bare key.
 */
export const QUICK_ACTIONS: Record<QuickActionKey, QuickActionMeta> = {
  laborlaw: {
    icon: "§",
    label: "Labor law & state rules",
    hint: "Reporting, benefits and RTW obligations for this claim's state",
  },
  similar: {
    icon: "↻",
    label: "Similar case outcomes",
    hint: "Comparable claims at this employer, and how they ran",
  },
  rtw: {
    icon: "📄",
    label: "Review RTW Policy",
    hint: "Draft a return-to-work offer letter from the restrictions on file",
  },
  reserve: {
    icon: "✓",
    label: "Reserve review",
    hint: "The reserve adequacy verdict and the exposure behind it",
  },
  fraud: {
    icon: "🔍",
    label: "Fraud risk check",
    hint: "What this claim's fraud derivations say, and what to do about it",
  },
  nextactions: {
    icon: "!",
    label: "Next best actions",
    hint: "The action checklist, explained in the order it is ranked",
  },
  data_alignment: {
    icon: "📊",
    label: "Data alignment note",
    hint: "Where this claim's data comes from — computed, no model involved",
  },
};

/** The keys in render order, so a component does not re-derive the order. */
export const QUICK_ACTION_KEYS = Object.keys(QUICK_ACTIONS) as QuickActionKey[];
