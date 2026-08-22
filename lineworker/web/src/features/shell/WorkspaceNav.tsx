/**
 * The console's first navigation (Story 7.1, widened by 7.2).
 *
 * There has never been one. Every shell before this held exactly one view —
 * `DashboardShell` was a top bar and an `<Outlet/>`, `WorkspaceShell` is three
 * fixed panes — and Story 5.5's drill-through routes are reached by *clicking a
 * figure*, never by choosing a destination. So this is a new idiom rather than a
 * reuse of one, and it is deliberately the smallest thing that can be called a
 * navigation: a labelled list of links, in a `<nav>`, with the current one
 * marked.
 *
 * **"The current one" is a partition over every route this renders on, not a
 * path match against each link.** `DashboardShell` draws this on every dashboard
 * child route — the dashboard, the drill list, a claim opened from either — and
 * the drill list is where these sections' own charts and tables *go*. So each
 * section entry owns its own subtree and the Portfolio entry owns **everything
 * else**, which makes exactly one of the three current everywhere the nav
 * exists. See `NavSpec`.
 *
 * **That is why adding an entry is two edits, not one.** Story 7.2 added Trends
 * *and* amended Portfolio's predicate in the same breath: `owns` is a partition,
 * so a new section that is not subtracted from the catch-all leaves two entries
 * marked current on its own route — a navigation that says you are in two places
 * at once, which is a quieter failure than the blank one 7.1 fixed. `inSection`
 * below exists so the subtraction is one list rather than a growing chain of
 * `&&`s that a fourth section could be left out of.
 *
 * **It renders for the analyst and for nobody else.** That is the whole of
 * Epic 7's first delivery on the client: the analyst has been a supervisor clone
 * since Epic 5, and the thing that stops being true here is that they now have a
 * section the supervisor does not. A supervisor sees no nav at all — not a
 * disabled one, not one with a single entry — because a navigation with one
 * destination is chrome, and because "the supervisor's dashboard is byte
 * identical to what Epic 5 shipped" is an acceptance criterion this component
 * would break by rendering anything.
 *
 * **Three destinations, and Story 7.3 deliberately did not add a fourth.**
 * Segmentation is not a *place*: it is a filter over the two sections that
 * exist, so it lands as a bar mounted on those routes rather than as an entry
 * beside them — `SegmentationSection` below, which reuses this file's own
 * partition so "which routes are sections" is one list with two readers.
 * Financial decomposition (7.4) is still unbuilt and its slot is still left
 * *unbuilt rather than stubbed-broken*: a disabled entry would be a promise the
 * build cannot keep, and a reader would have no way to tell it from one that is
 * merely failing to load. It lands the same way Trends did: a line in
 * `DESTINATIONS`, and its route added to `SECTION_ROUTES`.
 *
 * **The role comes from the server**, through `useMe` and therefore from
 * `app_user`, exactly as `RequireSession`'s does. This component decides which
 * *links* to draw; it decides nothing about access — the route is guarded by its
 * own `RequireSession allow={["analyst"]}` and the three endpoints behind it are
 * gated in `services/worklist/fraud.py`. A nav that hid an entry would be a
 * courtesy, never a control.
 */
import { Link, useLocation } from "react-router";

import { useMe } from "@/api/auth";
import { hrefWithFilters } from "@/features/dashboard/drill/filters";
import { SegmentationBar } from "@/features/dashboard/segmentation/SegmentationBar";
import { useSegmentation } from "@/features/dashboard/segmentation/useSegmentation";

import { DASHBOARD_ROUTE, FRAUD_ROUTE, TRENDS_ROUTE } from "./routes";

/**
 * The section routes Portfolio's catch-all has to subtract, declared once.
 *
 * The partition's other half, and the reason it is a list: with two sections the
 * subtraction was one call and reading it told you the whole rule; with three it
 * would have been `!inFraudSection(p) && !inTrendsSection(p)`, which is a chain
 * a fourth section can be left out of — and being left out of it is silent,
 * because the symptom is two entries marked current on a route nobody re-tested.
 * Adding a section means adding its route here and its entry below, and the
 * `DESTINATIONS` list is asserted against this one by `App.test.tsx`'s
 * one-current-entry test on every route.
 */
const SECTION_ROUTES: readonly string[] = [FRAUD_ROUTE, TRENDS_ROUTE];

/**
 * Whether a path belongs to one particular section.
 *
 * Exact match or a child of it, never a bare `startsWith`: `/dashboard/fraudulent`
 * is not a Fraud route and there is no reason to leave a prefix check that would
 * one day say it is.
 */
function inSection(route: string): (pathname: string) => boolean {
  return (pathname) => pathname === route || pathname.startsWith(`${route}/`);
}

/** Whether a path belongs to *any* section — Portfolio's complement. */
function inAnySection(pathname: string): boolean {
  for (const route of SECTION_ROUTES) {
    if (inSection(route)(pathname)) return true;
  }
  return false;
}

/**
 * One destination: where it goes, what it is called, and how a test finds it.
 *
 * Declared as data rather than laid out in JSX, `DashboardPage`'s `CardSpec`
 * reason: the *set* is the contract — exactly three entries, and which three —
 * so it should be a list a reviewer can read against the story text rather than
 * a shape inferred from markup.
 */
interface NavSpec {
  to: string;
  label: string;
  testId: string;
  /**
   * Whether this destination owns the route the nav is being rendered on.
   *
   * **Not `NavLink`'s `end` flag, and the difference is a whole class of route.**
   * `end` compares against the link's own path, so Portfolio with `end: true` and
   * Fraud with `end: false` marked nothing at all on `/dashboard/claims` — which
   * is where the Fraud section's *own* primary interaction goes, every band
   * segment, every pipeline bar and every rate row. An analyst who clicked a
   * chart landed on a page whose navigation had gone blank, under a component
   * whose docstring promises "the current one marked".
   *
   * A predicate instead makes the marking a **partition**: Portfolio owns
   * everything that is not *a* section, so exactly one entry is current on
   * every route `DashboardShell` renders this on, including the drill list and
   * the read-only claim view behind it. The catch-all is the complement of
   * `SECTION_ROUTES` rather than of one route — see that constant.
   */
  owns: (pathname: string) => boolean;
}

const DESTINATIONS: readonly NavSpec[] = [
  {
    to: DASHBOARD_ROUTE,
    label: "Portfolio",
    testId: "nav-portfolio",
    // The dashboard, the drill list, and a claim opened from either — all of
    // them are the portfolio the analyst was reading, whichever figure they
    // arrived through. **The complement of every section**, never of one: see
    // `SECTION_ROUTES`.
    owns: (pathname) => !inAnySection(pathname),
  },
  {
    to: FRAUD_ROUTE,
    label: "Fraud",
    testId: "nav-fraud",
    owns: inSection(FRAUD_ROUTE),
  },
  {
    to: TRENDS_ROUTE,
    label: "Trends",
    testId: "nav-trends",
    owns: inSection(TRENDS_ROUTE),
  },
];

/**
 * The segmentation bar, on the analyst's sections and nowhere else (Story 7.3).
 *
 * **Here rather than in `DashboardShell` for that file's own reason**, and the
 * reason is sharper than it was for the nav: the *route* half of this rule is
 * `SECTION_ROUTES`, which already exists in this file because the nav's
 * "current" marking is a partition over it. Deciding it in the shell would mean
 * a second reading of the same list, free to disagree the first time a fourth
 * section landed in one of them and not the other — and the symptom would be a
 * section whose filter bar is missing while its nav entry is marked current.
 *
 * The **role** half is `WorkspaceNav`'s too: a supervisor sees no bar, not a
 * disabled one, because the sections it filters are not theirs to reach.
 *
 * **Not on the portfolio route.** That view is Epic 5's dashboard — scoped for
 * the analyst rather than borrowed from the supervisor — and its endpoints take
 * no segmentation at all, so a bar there would be a control that narrowed
 * nothing and said so only by leaving every figure unchanged.
 *
 * A component rather than a conditional in the shell's JSX, so the two rules are
 * one function a test can render on its own.
 */
export function SegmentationSection() {
  const { data: me } = useMe();
  const { pathname } = useLocation();

  if (me?.role !== "analyst") return null;
  if (!inAnySection(pathname)) return null;
  return <SegmentationBar />;
}

/** The current destination's tokens, and every other one's. */
const CURRENT = "border-brand text-text";
const RESTING = "border-transparent text-muted-text hover:text-text";

export function WorkspaceNav() {
  const { data: me } = useMe();
  const { pathname } = useLocation();
  /**
   * The active segmentation, carried between the sections it applies to.
   *
   * **A plain `to={FRAUD_ROUTE}` drops the query string**, which is a filter
   * silently widening back to the whole portfolio the moment an analyst moves
   * from one section of one filtered book to another — and it looks like a
   * correct page, because every figure on it is right for a filter nobody
   * cancelled.
   *
   * Carried on the **sections only**. Portfolio is Epic 5's dashboard, scoped
   * for the analyst rather than borrowed from the supervisor, and its endpoints
   * take no segmentation at all — so a filter in that address bar would be a
   * narrowing with no control to see it by and no figure moved by it, which is
   * worse than dropping it. The section controls (`grain`, `anchor`, `cohort`,
   * the three sorts) are dropped by construction: they belong to one section,
   * and this builds from the ten dimensions rather than from the whole query
   * string.
   */
  const { segmentation } = useSegmentation();

  // Not `me?.role === "analyst"` inline at the call site, because the call site
  // is a layout: a shell that decided who sees a nav would put a role branch in
  // the file whose job is a top bar and an outlet. Deciding it here keeps
  // `DashboardShell` a frame and keeps this component's own docstring the place
  // the rule is written down.
  if (me?.role !== "analyst") return null;

  return (
    <nav
      aria-label="Analyst workspace"
      data-testid="workspace-nav"
      className="flex gap-[18px] border-b border-border px-[18px]"
    >
      {DESTINATIONS.map((destination) => {
        // `Link` plus an explicit `aria-current` rather than `NavLink`: the
        // marking is a property of the *section* a route belongs to, and
        // `NavLink` can only compare against its own path. Stamping the attribute
        // here also makes it the thing a test reads, which is the thing a screen
        // reader reads.
        const current = destination.owns(pathname);
        return (
          <Link
            key={destination.testId}
            to={
              destination.to === DASHBOARD_ROUTE
                ? destination.to
                : hrefWithFilters(destination.to, segmentation)
            }
            aria-current={current ? "page" : undefined}
            data-testid={destination.testId}
            className={`-mb-px border-b-2 py-[7px] font-display text-[11px] font-bold tracking-[0.3px] uppercase focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none ${
              current ? CURRENT : RESTING
            }`}
          >
            {destination.label}
          </Link>
        );
      })}
    </nav>
  );
}
