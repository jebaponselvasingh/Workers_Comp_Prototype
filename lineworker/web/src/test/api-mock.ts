/**
 * Fetch stub for component tests.
 *
 * Deliberately a stub and not a mock of our own client: the tests exercise
 * the real generated client, the real problem+json translation, and the
 * real TanStack Query wiring — only the network is replaced. Browser-level
 * behaviour against the real API is the e2e suite's job (AD-15).
 */
import { vi } from "vitest";

/**
 * A stubbed route: a canned response, or "pending" for a request that never
 * settles. Loading states are real states — a stub that resolves
 * immediately makes them unobservable, and a test that cannot see the
 * skeleton cannot tell a skeleton from a zero.
 */
export type StubRoute = { status: number; body: unknown } | "pending";

/**
 * A route that may answer differently depending on what was asked.
 *
 * The queue needs this and the other endpoints do not: `?filter=litigation`
 * and `?filter=all` are two different server answers, and "changing the
 * filter refetches rather than narrowing a cached list" (AD-1) is only
 * observable if the stub can tell the two requests apart.
 */
export type StubRouteFor = StubRoute | ((url: string) => StubRoute);

export interface StubRoutes {
  me?: StubRoute;
  personas?: StubRoute;
  login?: StubRoute;
  stats?: StubRoute;
  sla?: StubRoute;
  glossary?: StubRoute;
  claimsQueue?: StubRouteFor;
  /**
   * `GET /claims/{id}` (Story 2.2). A function so a test can answer
   * differently per claim id — which is how "one claim opens and another
   * 404s" is written without two renders.
   */
  claimDetail?: StubRouteFor;
  /**
   * `GET /claims/{id}/documents/{id}/content` (Story 2.5). A function so a
   * test can answer the FROI sheet for one document id and the summary sheet
   * for another — which is how "the viewer renders what it was sent, not what
   * it inferred from the row it was opened by" is written.
   */
  documentSheet?: StubRouteFor;
}

const problem = (status: number, detail: string) => ({
  type: "/problems/unauthenticated",
  title: "Unauthenticated",
  status,
  detail,
});

export const UNAUTHENTICATED = { status: 401, body: problem(401, "Sign in to continue.") };

/** Matches the first supervisor in SEEDED_PERSONAS below. */
export const DEFAULT_LOGIN = {
  status: 200,
  body: { id: 7, name: "David Bline", role: "supervisor", initials: "DB" },
};

export const ME_SUPERVISOR = DEFAULT_LOGIN;
export const ME_HANDLER = {
  status: 200,
  body: { id: 1, name: "Kaya Johnson", role: "handler", initials: "KJ" },
};

/**
 * Jennifer Park's real seed numbers. Component tests do not verify these —
 * that is `test_topbar_stats.py`'s job against the actual database — they
 * verify that whatever the server sends is what the tiles show. Using real
 * numbers anyway keeps a reader from mistaking a stub for a computation.
 */
export const TOPBAR_STATS = {
  status: 200,
  body: { caseload: 27, activeTx: 5, highRisk: 10 },
};

/**
 * Jennifer Park's real seed strip: three missed targets and one made one,
 * so a test can tell pass styling from warn styling without inventing a
 * shape the server never sends. Verdicts and precision are the server's —
 * the component under test must not recompute either.
 */
export const SLA_STRIP = {
  status: 200,
  body: {
    pick: { value: 2.7, target: 1, direction: "below", decimals: 1, status: "warn" },
    approve: { value: 8.1, target: 5, direction: "below", decimals: 1, status: "warn" },
    settle: { value: 62, target: 30, direction: "below", decimals: 0, status: "warn" },
    rtwRate: { value: 95, target: 80, direction: "above", decimals: 0, status: "pass" },
  },
};

/** A brand-new handler's empty book — every segment `no_data` (NFR-3). */
export const SLA_NO_DATA = {
  status: 200,
  body: {
    pick: { value: null, target: 1, direction: "below", decimals: 1, status: "no_data" },
    approve: { value: null, target: 5, direction: "below", decimals: 1, status: "no_data" },
    settle: { value: null, target: 30, direction: "below", decimals: 0, status: "no_data" },
    rtwRate: { value: null, target: 80, direction: "above", decimals: 0, status: "no_data" },
  },
};

/**
 * Six of the 25 seeded glossary terms, verbatim from
 * `server/data/seed/glossary_terms.json` and in its order.
 *
 * A subset rather than the whole file: component tests check the *predicate
 * and the states*, not the contents of the reference data — that is
 * `test_glossary.py`'s job against the database and the e2e spec's against
 * the seed file. The six are chosen so one query can hit each searchable
 * field on its own: "havs" only an abbreviation, "maximum medical" only a
 * term, "audiogram" only a definition.
 */
export const GLOSSARY_TERMS = {
  status: 200,
  body: {
    items: [
      {
        abbreviation: "FNOL",
        term: "First Notice of Loss",
        definition:
          "Initial injury report to employer/carrier, starting notice-deadline clock and claims workflow.",
      },
      {
        abbreviation: "MMI",
        term: "Maximum Medical Improvement",
        definition:
          "Condition has stabilized and won't improve further. Triggers PPD rating and settlement discussions.",
      },
      {
        abbreviation: "HAVS",
        term: "Hand-Arm Vibration Syndrome",
        definition:
          "Occupational disease from prolonged vibrating tool use. Causes Raynaud's phenomenon (Vibration White Finger) in manufacturing workers.",
      },
      {
        abbreviation: "NIHL",
        term: "Noise-Induced Hearing Loss",
        definition:
          "Permanent hearing loss from manufacturing floor noise exposure — measurable by audiogram.",
      },
      // Both prototype quirks, so a test can see that neither was cleaned
      // up: a multi-word abbreviation, and a term that is its own.
      {
        abbreviation: "OSHA 300",
        term: "OSHA Recordkeeping Log",
        definition:
          "Federal Form 300 — mandatory recording of workplace injuries/illnesses for manufacturers with 10+ employees.",
      },
      {
        abbreviation: "Apportionment",
        term: "Apportionment",
        definition:
          "Dividing disability liability between current occupational injury and pre-existing/non-occupational conditions.",
      },
    ],
    nextCursor: null,
    total: 6,
  },
};

/**
 * A *successful* response carrying nothing — 0006 applied without 0007, a
 * `downgrade 0006`, a table someone emptied. Not a hypothetical: it is the
 * one way the panel can be handed no terms without any error to report, and
 * the branch it takes decides whether a handler is told the glossary is
 * unavailable or told their term does not exist.
 */
export const GLOSSARY_EMPTY = {
  status: 200,
  body: { items: [], nextCursor: null, total: 0 },
};

/**
 * Queue fixtures built from Kaya Johnson's real seeded book — her stage
 * counts are intake 3, investigation 1, treatment 15, settled 26.
 *
 * Component tests do not verify these numbers (that is
 * `test_claims_queue.py`'s job against the database, and the e2e spec's
 * against the seed file); they verify that whatever the server sends is
 * what the pane renders. Real numbers are used anyway so a reader cannot
 * mistake a stub for a computation — and because a card carrying invented
 * flags would let a test pass against a component that derived them.
 */
function stageGroup(
  items: unknown[],
  total = items.length,
  nextCursor: string | null = null,
) {
  return { items, nextCursor, total };
}

/** One fully-populated card: every badge on, marker on, high risk. */
export const LOUD_CARD = {
  claimId: "WC-20017",
  daysOpen: 140,
  risk: "high",
  workerName: "Marcus Delgado",
  injuryType: "Fall from Height",
  stage: "treatment",
  employerShortName: "Caterpillar",
  fraudFlag: true,
  litigationFlag: true,
  paymentDue: true,
  siuReview: true,
  rtwBlocked: true,
  priorityScore: 187.2,
  priorityMarker: true,
};

/** Its opposite: no badge, no marker, low risk — so a test can see absence. */
export const QUIET_CARD = {
  claimId: "WC-20044",
  daysOpen: 0,
  risk: "low",
  workerName: "Ana Ruiz",
  injuryType: "Laceration",
  stage: "treatment",
  employerShortName: "GE",
  fraudFlag: false,
  litigationFlag: false,
  paymentDue: false,
  siuReview: false,
  rtwBlocked: false,
  priorityScore: 4.5,
  priorityMarker: false,
};

export const INTAKE_CARD = {
  ...QUIET_CARD,
  claimId: "WC-20003",
  stage: "intake",
  workerName: "Priya Raman",
  injuryType: "Repetitive Strain",
  priorityScore: 31.4,
  priorityMarker: true,
};

/** Intake 1 · investigation 0 · treatment 2 · settled 0 — an empty stage
 * and a populated one in the same payload. */
export const CLAIM_QUEUE = {
  status: 200,
  body: {
    groups: {
      intake: stageGroup([INTAKE_CARD]),
      investigation: stageGroup([]),
      treatment: stageGroup([LOUD_CARD, QUIET_CARD]),
      settled: stageGroup([]),
    },
    rulesVersion: 1,
    thresholdsVersion: 1,
    unfilteredTotal: 3,
    filteredTotal: 3,
  },
};

/**
 * The same shape with nothing in it, and an **empty book behind it** —
 * `unfilteredTotal: 0` is what makes this the scope-empty payload rather
 * than the filter-empty one. The two are otherwise byte-identical, which is
 * exactly why the server has to say which it is: see `CLAIM_QUEUE_NO_MATCH`.
 */
export const CLAIM_QUEUE_EMPTY = {
  status: 200,
  body: {
    groups: {
      intake: stageGroup([]),
      investigation: stageGroup([]),
      treatment: stageGroup([]),
      settled: stageGroup([]),
    },
    rulesVersion: 1,
    thresholdsVersion: 1,
    unfilteredTotal: 0,
    filteredTotal: 0,
  },
};

/**
 * No group matched, but the handler has 45 claims — a filter miss.
 *
 * `filteredTotal: 0` beside `unfilteredTotal: 45` is the whole distinction,
 * and it is the server's to make: the groups are byte-identical to
 * `CLAIM_QUEUE_EMPTY`'s.
 */
export const CLAIM_QUEUE_NO_MATCH = {
  status: 200,
  body: { ...CLAIM_QUEUE_EMPTY.body, unfilteredTotal: 45, filteredTotal: 0 },
};

/** A treatment group with more claims than its page — "Show more" appears. */
export const CLAIM_QUEUE_PAGED = {
  status: 200,
  body: {
    groups: {
      intake: stageGroup([]),
      investigation: stageGroup([]),
      treatment: stageGroup([LOUD_CARD], 2, "cursor-page-2"),
      settled: stageGroup([]),
    },
    rulesVersion: 1,
    thresholdsVersion: 1,
    unfilteredTotal: 2,
    filteredTotal: 2,
  },
};

/** What `?cursor=cursor-page-2` answers for that group. */
export const CLAIM_QUEUE_PAGE_TWO = {
  status: 200,
  body: {
    groups: {
      intake: stageGroup([]),
      investigation: stageGroup([]),
      treatment: stageGroup([QUIET_CARD], 2),
      settled: stageGroup([]),
    },
    rulesVersion: 1,
    thresholdsVersion: 1,
    unfilteredTotal: 2,
    filteredTotal: 2,
  },
};

/**
 * Case-file fixtures (Story 2.2).
 *
 * Shaped like the real payload and carrying plausible seed-shaped values,
 * for the queue fixtures' reason: component tests verify that whatever the
 * server sends is what the pane renders, and a fixture with invented
 * *structure* would let a test pass against a component that derived
 * something. Every derived value here — `risk`, `phase`, `coordinationStatus`,
 * `costSplit`, the stepper marks, the checklist — is a value the server
 * decided, so the fixtures spell them out rather than computing them.
 */
function stepper(current: string) {
  const order = ["intake", "investigation", "treatment", "settled"];
  const position = order.indexOf(current);
  return order.map((stage, index) => ({
    stage,
    done: index < position,
    current: index === position,
  }));
}

const TIMELINE = [
  { eventDate: "2026-03-22", description: "FNOL received — Fall from Height", tag: "intake" },
  { eventDate: "2026-03-24", description: "C-1 First Report of Injury filed", tag: "froi" },
  { eventDate: "2026-03-25", description: "Handler assigned — Kaya Johnson", tag: "assignment" },
];

/**
 * The editable vocabularies the server sends with every case file (Story
 * 2.3) — the eleven diagram keys, the five recovery windows, the two
 * disability tokens.
 *
 * The real lists, not a subset, because a select is only trustworthy if it
 * offers exactly what the command accepts; a fixture with three body parts
 * would let a test pass against a component that silently dropped the rest.
 * Their *content* is asserted server-side against the prototype
 * (`test_claim_edit_validation.py`); here they only have to be real.
 */
export const EDIT_OPTIONS = {
  bodyParts: [
    { key: "head", label: "Head" },
    { key: "ears", label: "Ears" },
    { key: "shoulder_right", label: "Right Shoulder" },
    { key: "shoulder_left", label: "Left Shoulder" },
    { key: "forearm_right", label: "Right Forearm" },
    { key: "hand_right", label: "Right Hand" },
    { key: "hand_left", label: "Left Hand" },
    { key: "torso", label: "Torso" },
    { key: "lumbar", label: "Lower Back (Lumbar)" },
    { key: "tibia_left", label: "Left Lower Leg" },
    { key: "tibia_right", label: "Right Lower Leg" },
  ],
  recoveryWindows: ["weeks_0_2", "weeks_2_4", "weeks_4_6", "weeks_6_8", "over_1_year"],
  disabilities: ["temporary", "permanent"],
};

/**
 * The injury-diagram block (Story 2.4).
 *
 * Every marker arrives with its `band` already decided, because that is what
 * the server sends: the fixture spells out `high`/`med` rather than deriving
 * them from the scores beside them, so a `BodyMap` that banded its own
 * colours would pass no test here.
 *
 * Two markers, one primary and one secondary, so a single render can see the
 * pulse (primary only), the ✕ (secondary only) and two different band
 * colours. `INJURY_UNKNOWN_KEY` below covers the hotspot fallback.
 */
export const INJURY_DIAGRAM = {
  markers: [
    {
      id: null,
      version: null,
      bodyKey: "lumbar",
      bodyPart: "Lower Back",
      injuryType: "Fall from Height",
      severityScore: 78,
      band: "high",
      primary: true,
    },
    {
      id: 41,
      // Deliberately *not* the claim's version (1). A removal compare-and-
      // swaps on the injury row, and a fixture where the two numbers were
      // equal would let a component that sent the wrong one pass.
      version: 3,
      bodyKey: "hand_left",
      bodyPart: "Left Hand",
      injuryType: "Laceration",
      severityScore: 44,
      band: "med",
      primary: false,
    },
  ],
  icd: "S39.012A",
  icdDesc: "Strain of muscle, fascia and tendon of lower back",
  prognosis: {
    mmi: "6-10 mo",
    rtw: "Modified duty 4-6 mo",
    impairment: "PPD possible 5-15%",
    litigation: "Low",
  },
  treatmentPlan: [
    { stepNo: 1, description: "Head/spine CT protocol if fall >4 feet" },
    { stepNo: 2, description: "Orthopedic evaluation for fracture" },
    { stepNo: 3, description: "PT for musculoskeletal recovery" },
  ],
  contraindications:
    "No elevated work platform access until medically cleared. Fall protection protocol review.",
  defaultSeverityScore: 40,
  captureVersion: 1,
  severityMin: 0,
  severityMax: 100,
};

/**
 * A marker whose region this build does not know — deploy skew, or a key
 * added to the server's vocabulary ahead of the SVG.
 *
 * Its own fixture because the `torso` fallback is otherwise rendered by
 * nothing and asserted by nothing: the select cannot offer such a key and
 * the command refuses one, so only a test can produce it. Story 2.2's code
 * review found the same shape of hole in the settled banner's date clause.
 */
export const INJURY_UNKNOWN_KEY = {
  ...INJURY_DIAGRAM,
  markers: [
    { ...INJURY_DIAGRAM.markers[0], bodyKey: "cervical_spine" },
    INJURY_DIAGRAM.markers[1],
  ],
};

/**
 * The Documents & ID block (Story 2.5).
 *
 * `path` is `b` and the four forms are Path B's, because that is what the
 * server would send for this claim — the fixture does not *derive* the path
 * from the claim's severity beside it, so a component that classified in the
 * browser would pass no test here. `DOCUMENTS_BLOCK_PATH_A` below is the
 * second path, for the same reason `INJURY_DIAGRAM` carries two bands.
 *
 * Three documents, one of them a FROI, so a single render sees three different
 * type chips and both viewer variants are reachable. The last has no
 * `filedDate`: 101 seeded documents carry a timing note where a date belongs,
 * and the em dash is the honest rendering.
 */
/**
 * The `derivation_thresholds` version the case-file fixtures were cut from.
 *
 * **One constant because the server makes these two fields equal** (code
 * review, 2026-08-12): `documents_block` sets `path_version =
 * thresholds.version`, so `thresholdsVersion` and `pathVersion` agree in every
 * real payload. The fixtures used to carry 2 and 3 — a response the server
 * cannot produce, in a file whose own doctrine is that the fixture *is* the
 * server's answer. Harmless while nothing compares them, and silently fatal to
 * the first test that does.
 *
 * The queue fixtures above are deliberately not switched to this: they are a
 * different endpoint with no `pathVersion` beside them, and their version is
 * free to differ.
 */
const CASE_FILE_RULE_VERSION = 3;

export const DOCUMENTS_BLOCK = {
  path: "b",
  pathVersion: CASE_FILE_RULE_VERSION,
  requiredForms: [
    {
      formCode: "C-3",
      formName: "FROI — Employee Claim for Compensation (C-3)",
      description:
        "Employee's formal WC claim. Filed when disability exceeds waiting period.",
      timing: "As soon as practicable; carrier within 30 days",
      downloadUrl: "https://www.wcb.ny.gov/content/main/forms/c3.pdf",
    },
    {
      formCode: "RFA-1W",
      formName: "RTW Request for Assistance (RFA-1W)",
      description: "Initiates formal RTW coordination.",
      timing: "When physician grants light duty clearance",
      downloadUrl: "https://www.wcb.ny.gov/content/main/forms/rfa-1w.pdf",
    },
    {
      formCode: "C-4.3",
      formName: "Maximum Medical Improvement — MMI (C-4.3)",
      description: "Treating physician certifies MMI reached.",
      timing: "When physician determines MMI",
      downloadUrl: "https://www.wcb.ny.gov/content/main/forms/c4_3.pdf",
    },
    {
      formCode: "C-11",
      formName: "Employee Change in Employment Status (C-11)",
      description: "Documents RTW, termination, job change, or retirement.",
      timing: "When employment status changes",
      downloadUrl: "https://www.wcb.ny.gov/content/main/forms/c11.pdf",
    },
  ],
  idCard: {
    employeeBusinessId: "EMP-CAT-2043",
    workerName: "Marcus Delgado",
    workerRole: "Assembly Technician",
    policyNum: "CL-POL-CAT-2024-118",
    doi: "2026-03-22",
    handlerName: "Kaya Johnson",
    plant: "Caterpillar – Peoria, IL",
    state: "IL",
    region: "Midwest",
  },
  documents: [
    { id: 11, name: "C-1 First Report of Injury", docType: "froi", filedDate: "2026-03-24" },
    {
      id: 12,
      name: "Medical Authorization & Release (HIPAA)",
      docType: "medauth",
      filedDate: "2026-03-24",
    },
    { id: 13, name: "Surgical Consent & Operative Report", docType: "legal", filedDate: null },
  ],
};

/** The minor path — a different banner, a different form set, two forms. */
export const DOCUMENTS_BLOCK_PATH_A = {
  ...DOCUMENTS_BLOCK,
  path: "a",
  requiredForms: [
    {
      formCode: "C-2F",
      formName: "Minor Injury Report",
      description: "Employer's first-aid-only / minor injury report.",
      timing: "Within 10 days of incident",
      downloadUrl: "https://www.wcb.ny.gov/content/main/forms/c2F.pdf",
    },
    {
      formCode: "FAR-1",
      formName: "Employee First Aid / Injury Report",
      description: "Employee-completed onsite first aid and injury description.",
      timing: "Same shift or within 24h",
      downloadUrl: "https://www.fnsb.gov/DocumentCenter/View/18587/",
    },
  ],
};

/**
 * A claim whose file holds nothing (AC 5).
 *
 * Unreachable against the dev seed — every seeded claim carries three to eight
 * documents — which is the reason it needs a fixture rather than a claim id.
 * The forms card is *not* empty here: which filings a path requires does not
 * depend on what has been filed, and a state that blanked both would say
 * something the data does not.
 */
export const DOCUMENTS_BLOCK_EMPTY = { ...DOCUMENTS_BLOCK, documents: [] };

/**
 * The Photos block (Story 2.6).
 *
 * Three cards, all of them the seeded shape: `hasBlob: false` and
 * `blobUrl: null`, because the prototype has no image files behind its photo
 * rows and all 293 seeded rows carry a null `blob_key`.
 *
 * **`count` is 3 and is written out rather than spread from the array**, on
 * purpose: the fixture is the server's answer, and the server is the thing
 * that computes it. A component that derived the tab label from
 * `photos.length` would pass against a fixture whose count *was*
 * `photos.length`, which is why `PHOTOS_BLOCK_MISCOUNTED` below exists.
 */
export const PHOTOS_BLOCK = {
  count: 3,
  photos: [
    {
      id: 31,
      caption: "Platform / Scaffolding Fall area — post-incident overview",
      source: "Plant safety, 03/22",
      hasBlob: false,
      blobUrl: null,
    },
    {
      id: 32,
      caption: "OSHA investigation — incident scene documentation",
      source: "OSHA inspector, 03/24",
      hasBlob: false,
      blobUrl: null,
    },
    {
      id: 33,
      caption: "PPE worn at time of injury — Platform / Scaffolding Fall",
      source: "EHS audit, 03/25",
      hasBlob: false,
      blobUrl: null,
    },
  ],
};

/**
 * The empty state (Story 2.6, AC 3, NFR-3).
 *
 * Unreachable against the dev seed — every one of the 100 claims carries two
 * to four photos — which is the reason it needs a fixture rather than a claim
 * id, exactly as `DOCUMENTS_BLOCK_EMPTY` does.
 */
export const PHOTOS_BLOCK_EMPTY = { count: 0, photos: [] };

/**
 * A count that disagrees with the rows — a payload no real server sends.
 *
 * It exists so the tab label has something to be *wrong* against: with a
 * faithful fixture, a component reading `photos.length` and one reading
 * `count` are indistinguishable. This is the fixture that tells them apart.
 */
export const PHOTOS_BLOCK_MISCOUNTED = { ...PHOTOS_BLOCK, count: 9 };

/**
 * The two states a photo with bytes can be in, which is why the server sends
 * two fields rather than one.
 *
 * - `hasBlob` with a `blobUrl` — a presigning store (MinIO): render the image.
 * - `hasBlob` with a null `blobUrl` — a mounted volume, whose `BlobStore.url`
 *   answers `None` *by design*. There is a file; this deployment cannot hand
 *   the browser a direct link to it. A UI that read the null URL as "no photo"
 *   would show the placeholder for a whole grid of real photographs.
 */
export const PHOTOS_BLOCK_WITH_IMAGE = {
  count: 2,
  photos: [
    {
      ...PHOTOS_BLOCK.photos[0],
      hasBlob: true,
      blobUrl: "https://blobs.example/photo-31.jpg",
    },
    { ...PHOTOS_BLOCK.photos[1], hasBlob: true, blobUrl: null },
  ],
};

/** The two viewer sheets the content endpoint answers (Story 2.5, AC 4). */
export const DOCUMENT_SHEET_FROI = {
  status: 200,
  body: {
    documentId: 11,
    name: "C-1 First Report of Injury",
    docType: "froi",
    sheetVariant: "froi",
    rows: [
      { label: "Claim ID", text: "WC-20017", cents: null },
      { label: "Policy Number", text: "CL-POL-CAT-2024-118", cents: null },
      { label: "Employee", text: "Marcus Delgado (EMP-CAT-2043)", cents: null },
      { label: "Employer / Plant", text: "Caterpillar – Peoria, IL", cents: null },
      { label: "Date of Injury", text: "2026-03-22", cents: null },
      { label: "Filed", text: "2026-03-24", cents: null },
      { label: "Injury Type", text: "Fall from Height", cents: null },
      { label: "Body Part", text: "Lower Back", cents: null },
      { label: "ICD-10", text: "S39.012A", cents: null },
      { label: "Cause", text: "Fall from Elevated Platform", cents: null },
      { label: "Severity", text: "78", cents: null },
      // The one money row: cents on the wire, formatted by the dialog.
      { label: "AWW", text: null, cents: 143_200 },
      { label: "Handler", text: "Kaya Johnson", cents: null },
      { label: "OSHA Recordable", text: "Yes — OSHA 300 Filed", cents: null },
    ],
    signatures: ["Supervisor / Date", "Adjuster / Date"],
    hasBlob: false,
    blobUrl: null,
  },
};

export const DOCUMENT_SHEET_SUMMARY = {
  status: 200,
  body: {
    documentId: 13,
    name: "Surgical Consent & Operative Report",
    docType: "legal",
    sheetVariant: "summary",
    rows: [
      { label: "Claim ID", text: "WC-20017", cents: null },
      { label: "Policy Number", text: "CL-POL-CAT-2024-118", cents: null },
      { label: "Employee", text: "Marcus Delgado (EMP-CAT-2043)", cents: null },
      { label: "Employer / Plant", text: "Caterpillar – Peoria, IL", cents: null },
      // Undated, as 101 seeded documents are — the dialog renders an em dash.
      { label: "Filed", text: null, cents: null },
      { label: "Status", text: "On file", cents: null },
      { label: "Handler", text: "Kaya Johnson", cents: null },
    ],
    signatures: ["Supervisor / Date", "Adjuster / Date"],
    hasBlob: false,
    blobUrl: null,
  },
};

const HEADER = {
  claimId: "WC-20017",
  workerName: "Marcus Delgado",
  workerRole: "Assembly Technician",
  employerName: "Caterpillar Inc.",
  state: "IL",
  injuryType: "Fall from Height",
  // The stored label and the diagram key are different vocabularies — the
  // fixture keeps them different so a component that confused the two would
  // fail here rather than in a browser.
  bodyPart: "Lower Back",
  bodyKey: "lumbar",
  cause: "Fall from Elevated Platform",
  icd: "S39.012A",
  severityScore: 78,
  risk: "high",
  stage: "treatment",
  fraudFlag: true,
  fraudScore: 62,
  litigationFlag: true,
  surgeryRequired: true,
  oshaRecordable: true,
};

/** Treatment: both derived states, and every header badge switched on. */
export const CLAIM_DETAIL_TREATMENT = {
  status: 200,
  body: {
    claimId: "WC-20017",
    version: 1,
    thresholdsVersion: CASE_FILE_RULE_VERSION,
    editOptions: EDIT_OPTIONS,
    injury: INJURY_DIAGRAM,
    documents: DOCUMENTS_BLOCK,
    photos: PHOTOS_BLOCK,
    requirementsVersion: null,
    header: HEADER,
    stepper: stepper("treatment"),
    overview: {
      stageVariant: "treatment",
      phase: "approaching_mmi",
      phaseNote: "Nearing maximum medical improvement — RTW and closure planning underway.",
      expectedDays: 42,
      daysOpen: 140,
      recovery: "weeks_4_6",
      paidMedicalCents: 1_240_000,
      paidIndemnityCents: 860_000,
      reserveCents: 4_500_000,
      coordinationStatus: "coordination_gap",
      coordinationNote:
        "RTW follow-up is overdue — recommended return date has passed with no confirmed update from employer or claimant.",
      returnStatus: "under_treatment",
      commStatus: "documents_received_and_approved",
      handlerName: "Kaya Johnson",
      timeline: TIMELINE,
      timelineTruncated: true,
    },
  },
};

/** Intake: a checklist with one row of each state, and no badges at all. */
export const CLAIM_DETAIL_INTAKE = {
  status: 200,
  body: {
    claimId: "WC-20003",
    version: 1,
    thresholdsVersion: CASE_FILE_RULE_VERSION,
    editOptions: EDIT_OPTIONS,
    injury: INJURY_DIAGRAM,
    documents: DOCUMENTS_BLOCK,
    photos: PHOTOS_BLOCK,
    requirementsVersion: 1,
    header: {
      ...HEADER,
      claimId: "WC-20003",
      workerName: "Priya Raman",
      stage: "intake",
      risk: "low",
      severityScore: 22,
      fraudFlag: false,
      litigationFlag: false,
      surgeryRequired: false,
      oshaRecordable: false,
    },
    stepper: stepper("intake"),
    overview: {
      stageVariant: "intake",
      employeeBusinessId: "EMP-1042",
      workerName: "Priya Raman",
      workerRole: "Machine Operator",
      plant: "Peoria Assembly",
      doi: "2026-03-22",
      froiDate: "2026-03-24",
      assignDate: "2026-03-25",
      handlerName: "Kaya Johnson",
      commStatus: "need_for_additional_information",
      injuryType: "Repetitive Strain",
      cause: "Repetitive Motion",
      bodyPart: "Right Wrist",
      severityScore: 22,
      risk: "low",
      awwCents: 118_000,
      reserveCents: 850_000,
      checklist: [
        { docType: "froi", received: true },
        { docType: "incident", received: false },
        { docType: "medauth", received: true },
        { docType: "wage", received: false },
      ],
      timeline: TIMELINE,
    },
  },
};

/** Investigation: payments made, so the cost bar is drawn. */
export const CLAIM_DETAIL_INVESTIGATION = {
  status: 200,
  body: {
    claimId: "WC-20051",
    version: 3,
    thresholdsVersion: CASE_FILE_RULE_VERSION,
    editOptions: EDIT_OPTIONS,
    injury: INJURY_DIAGRAM,
    documents: DOCUMENTS_BLOCK,
    photos: PHOTOS_BLOCK,
    requirementsVersion: null,
    header: { ...HEADER, claimId: "WC-20051", stage: "investigation", risk: "med" },
    stepper: stepper("investigation"),
    overview: {
      stageVariant: "investigation",
      injuryType: "Laceration",
      cause: "Contact with Machine Guard",
      bodyPart: "Left Hand",
      icd: "S61.412A",
      icdDesc: "Laceration without foreign body of left wrist",
      disability: "temporary",
      recovery: "weeks_2_4",
      awwCents: 104_000,
      totalPaidCents: 1_000_000,
      reserveCents: 2_200_000,
      policyNum: "WC-POL-88213",
      fraudScore: 18,
      severityScore: 44,
      risk: "med",
      paidIndemnityCents: 400_000,
      paidMedicalCents: 550_000,
      costSplit: { indemnityPct: 40, medicalPct: 55, expensePct: 5 },
      timeline: TIMELINE,
    },
  },
};

/** Investigation with nothing paid — the "Active — payments pending" branch. */
export const CLAIM_DETAIL_INVESTIGATION_UNPAID = {
  status: 200,
  body: {
    ...CLAIM_DETAIL_INVESTIGATION.body,
    overview: {
      ...CLAIM_DETAIL_INVESTIGATION.body.overview,
      totalPaidCents: 0,
      paidIndemnityCents: 0,
      paidMedicalCents: 0,
      costSplit: null,
    },
  },
};

/** Settled, with the null settlement date every seeded claim actually has. */
export const CLAIM_DETAIL_SETTLED = {
  status: 200,
  body: {
    claimId: "WC-20068",
    version: 5,
    thresholdsVersion: CASE_FILE_RULE_VERSION,
    editOptions: EDIT_OPTIONS,
    injury: INJURY_DIAGRAM,
    documents: DOCUMENTS_BLOCK,
    photos: PHOTOS_BLOCK,
    requirementsVersion: null,
    header: { ...HEADER, claimId: "WC-20068", stage: "settled", risk: "low" },
    stepper: stepper("settled"),
    overview: {
      stageVariant: "settled",
      settlementDate: null,
      totalPaidCents: 3_100_000,
      paidIndemnityCents: 1_500_000,
      paidMedicalCents: 1_400_000,
      paidExpenseCents: 200_000,
      reserveCents: 0,
      costSplit: { indemnityPct: 48, medicalPct: 45, expensePct: 7 },
      disability: "permanent",
      returnStatus: "returned_and_fully_recovered",
      daysToSettlement: 212,
      litigationFlag: false,
      handlerName: "Kaya Johnson",
      timeline: TIMELINE,
    },
  },
};

/**
 * A settled claim whose settlement event *does* carry a date.
 *
 * No seeded claim does — the prototype writes `Closed` where the date
 * belongs — so without this fixture the banner's date clause was rendered by
 * nothing and asserted by nothing, while its own comment claimed it "gains
 * the date without a change" (code review, 2026-08-12).
 */
export const CLAIM_DETAIL_SETTLED_DATED = {
  status: 200,
  body: {
    ...CLAIM_DETAIL_SETTLED.body,
    overview: { ...CLAIM_DETAIL_SETTLED.body.overview, settlementDate: "2026-07-29" },
  },
};

/** A claim with no history at all — the timeline card's empty state. */
export const CLAIM_DETAIL_NO_TIMELINE = {
  status: 200,
  body: {
    ...CLAIM_DETAIL_SETTLED.body,
    overview: { ...CLAIM_DETAIL_SETTLED.body.overview, timeline: [] },
  },
};

/**
 * The 404 the server answers for a claim outside the caller's scope — and,
 * identically, for one that does not exist. The SPA must not try to tell
 * them apart; the sameness is the point (AD-7).
 */
export const CLAIM_DETAIL_NOT_FOUND = {
  status: 404,
  body: {
    type: "/problems/claim-not-found",
    title: "Not Found",
    status: 404,
    detail: "No claim WC-9999 in your caseload.",
  },
};

export const SEEDED_PERSONAS = {
  status: 200,
  body: {
    items: [
      {
        id: 7,
        name: "David Bline",
        role: "supervisor",
        label: "David Bline — WC Supervisor (Full portfolio)",
      },
      {
        id: 8,
        name: "Jennifer Park",
        role: "supervisor",
        label: "Jennifer Park — WC Supervisor (3M/GM/Toyota)",
      },
      {
        id: 1,
        name: "Kaya Johnson",
        role: "handler",
        label: "Kaya Johnson — Handler (Caterpillar · GE)",
      },
      {
        id: 10,
        name: "David Bline",
        role: "analyst",
        label: "David Bline — WC Supervisor (Analyst view)",
      },
    ],
    nextCursor: null,
    total: 4,
  },
};

function respond(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: {
      "content-type": status >= 400 ? "application/problem+json" : "application/json",
    },
  });
}

/** Never settles — the request stays in flight for the life of the test. */
const pending = (): Promise<Response> => new Promise<Response>(() => {});

function answer(route: StubRoute): Promise<Response> {
  return route === "pending" ? pending() : Promise.resolve(respond(route.status, route.body));
}

function answerFor(route: StubRouteFor, url: string): Promise<Response> {
  return answer(typeof route === "function" ? route(url) : route);
}

/** Install a fetch stub for `/api/*`; unmatched paths answer 404. */
export function stubApi(routes: StubRoutes): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : (input as Request).url;

      if (url.includes("/api/me")) {
        return answer(routes.me ?? UNAUTHENTICATED);
      }
      if (url.includes("/api/personas")) {
        return answer(routes.personas ?? SEEDED_PERSONAS);
      }
      if (url.includes("/api/stats/topbar")) {
        return answer(routes.stats ?? TOPBAR_STATS);
      }
      if (url.includes("/api/stats/sla")) {
        return answer(routes.sla ?? SLA_STRIP);
      }
      if (url.includes("/api/glossary")) {
        return answer(routes.glossary ?? GLOSSARY_TERMS);
      }
      if (url.includes("/api/claims/queue")) {
        // The whole URL, query string included, so a stub can branch on the
        // filter or the cursor — see `StubRouteFor`.
        return answerFor(routes.claimsQueue ?? CLAIM_QUEUE, url);
      }
      // Before the case file, for the mirror of the reason the queue is: the
      // document sheet's URL *contains* a claim path, so a stub matching the
      // case file first would answer a viewer's request with a case file.
      if (url.includes("/documents/") && url.includes("/content")) {
        return answerFor(routes.documentSheet ?? DOCUMENT_SHEET_FROI, url);
      }
      // After the queue, deliberately: the two share a prefix, and the
      // server resolves the same ambiguity the same way (the queue route is
      // declared first). A stub that matched detail first would answer the
      // queue's request with a case file and no test would say why.
      if (url.includes("/api/claims/")) {
        return answerFor(routes.claimDetail ?? CLAIM_DETAIL_TREATMENT, url);
      }
      if (url.includes("/api/auth/logout")) {
        return respond(204, null);
      }
      if (url.includes("/api/auth/login")) {
        // The default carries a real role: `homeRouteFor` reads it, so an
        // empty body would silently route every login to /dashboard and a
        // future handler-login test would assert against the wrong shell.
        return answer(routes.login ?? DEFAULT_LOGIN);
      }
      return respond(404, problem(404, "Not Found"));
    }),
  );
}
