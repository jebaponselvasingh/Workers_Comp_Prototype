/**
 * The console's first navigation (Story 7.1).
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
 * the drill list is where this section's own charts and tables *go*. So the
 * Fraud entry owns its subtree and the Portfolio entry owns the rest, which
 * makes exactly one of the two current everywhere the nav exists. See `NavSpec`.
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
 * **Two destinations, and the other three Epic 7 sections are not here.** Trends
 * (7.2), segmentation (7.3) and financial decomposition (7.4) are unbuilt, and
 * the story's own instruction is to leave their slots *unbuilt rather than
 * stubbed-broken*. A disabled "Trends" entry would be a promise the build cannot
 * keep, and a reader would have no way to tell it from one that is merely
 * failing to load. When 7.2 lands it adds a line here.
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

import { DASHBOARD_ROUTE, FRAUD_ROUTE } from "./routes";

/**
 * Whether a path belongs to the Fraud section.
 *
 * Exact match or a child of it, never a bare `startsWith`: `/dashboard/fraudulent`
 * is not a Fraud route and there is no reason to leave a prefix check that would
 * one day say it is.
 */
function inFraudSection(pathname: string): boolean {
  return pathname === FRAUD_ROUTE || pathname.startsWith(`${FRAUD_ROUTE}/`);
}

/**
 * One destination: where it goes, what it is called, and how a test finds it.
 *
 * Declared as data rather than laid out in JSX, `DashboardPage`'s `CardSpec`
 * reason: the *set* is the contract — exactly two entries, and which two — so it
 * should be a list a reviewer can read against the story text rather than a
 * shape inferred from markup.
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
   * everything that is not the Fraud section, so exactly one entry is current on
   * every route `DashboardShell` renders this on, including the drill list and
   * the read-only claim view behind it.
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
    // arrived through.
    owns: (pathname) => !inFraudSection(pathname),
  },
  {
    to: FRAUD_ROUTE,
    label: "Fraud",
    testId: "nav-fraud",
    owns: inFraudSection,
  },
];

/** The current destination's tokens, and every other one's. */
const CURRENT = "border-brand text-text";
const RESTING = "border-transparent text-muted-text hover:text-text";

export function WorkspaceNav() {
  const { data: me } = useMe();
  const { pathname } = useLocation();

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
            to={destination.to}
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
