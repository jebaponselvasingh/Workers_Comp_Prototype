/**
 * The persistent top bar — identity and caseload at a glance (UX-DR2, FR-TOP-1/2).
 *
 * Rendered by both shells, in the prototype's order: brand · role badge ·
 * spacer · Glossary · Switch · three stat tiles · SLA strip · user chip.
 *
 * Everything with a number in it came from the server. The tiles render
 * `/api/stats/topbar` verbatim — three counts `services/worklist` computed
 * over the caller's employer scope (AD-1, AD-7) — and this component holds
 * no arithmetic and no fallback that would let it invent one. The
 * prototype's `getMine()` + `setStats()` pair, which filtered a global
 * claim array in the browser, is precisely what this replaces.
 *
 * Two seams are deliberately inert here and belong to sibling stories:
 * the 📖 Glossary button (Story 1.6 opens the panel) and the SLA strip slot
 * (Story 1.5 fills it). Both are rendered rather than omitted so those
 * stories add content to a frame instead of relitigating this layout.
 */
import { useNavigate } from "react-router";

import type { UserRole } from "@/api/auth";
import { useMe, useLogout } from "@/api/auth";
import { useTopBarStats } from "@/api/stats";

import { LOGIN_ROUTE } from "./routes";

/** Badge text, verbatim from the prototype (UX notes). */
const ROLE_BADGE: Record<UserRole, string> = {
  supervisor: "👔 Supervisor",
  handler: "📋 Handler",
  analyst: "📊 Analyst",
};

/**
 * The chip's role line. Not the same strings as the badge, and not the same
 * as the persona picker's titles either: the analyst persona is a
 * supervisor wearing the analyst hat, so the picker calls him "WC
 * Supervisor (Analyst view)" while the chip says what he is doing now.
 *
 * `Record<UserRole, …>` rather than a lookup with a fallback: a role added
 * to `user_role` becomes a TypeScript error here, which is a better place
 * to find out than a bar rendering "undefined" next to someone's name.
 */
const ROLE_LABEL: Record<UserRole, string> = {
  supervisor: "WC Supervisor",
  handler: "Claims Handler",
  analyst: "Data Analyst",
};

type TileTone = "neutral" | "warn" | "error";

const TONE_CLASS: Record<TileTone, string> = {
  neutral: "text-text",
  warn: "text-warn",
  error: "text-error",
};

interface TileProps {
  testId: string;
  label: string;
  title: string;
  value: number | undefined;
  tone: TileTone;
  isPending: boolean;
}

function StatTile({ testId, label, title, value, tone, isPending }: TileProps) {
  return (
    <div
      data-testid={testId}
      title={title}
      className="flex flex-col items-end border-r border-border pr-[11px] leading-tight"
    >
      {isPending ? (
        <span
          data-testid="stat-skeleton"
          aria-hidden
          className="my-[3px] h-[11px] w-7 animate-pulse rounded bg-surface-2"
        />
      ) : (
        // The value carries its own test id so assertions can match it
        // exactly: "15" contains "5", and a tile-level substring check
        // would pass on the wrong number.
        <span
          data-testid={`${testId}-value`}
          className={`font-mono text-sm font-bold ${TONE_CLASS[tone]}`}
        >
          {/* An em dash, never a 0: "no high-risk claims" and "we could not
              count them" are different facts, and one of them sends a
              handler home early (NFR-3, honest degradation). */}
          {value ?? "—"}
        </span>
      )}
      <span className="text-[9px] tracking-[0.4px] text-faint uppercase">{label}</span>
    </div>
  );
}

export function TopBar() {
  const navigate = useNavigate();
  const logout = useLogout();
  const me = useMe();
  const stats = useTopBarStats();

  const role = me.data?.role;

  return (
    <header
      data-testid="top-bar"
      className="flex h-[50px] flex-shrink-0 items-center gap-3 border-b border-border bg-surface px-4"
    >
      <div className="flex items-center gap-2 font-display text-[15px] font-bold">
        <span
          aria-hidden
          className="flex size-[22px] items-center justify-center rounded-[3px] bg-brand font-mono text-[11px] font-bold text-white"
        >
          L
        </span>
        LINEWORKER
        <span className="ml-1 text-[11px] font-normal text-faint">Manufacturing WC</span>
      </div>

      {role && (
        <div
          data-testid="role-badge"
          className="rounded-full bg-steel-soft px-[10px] py-[3px] text-[11px] font-bold text-steel"
        >
          {ROLE_BADGE[role]}
        </div>
      )}

      <div className="flex-1" />

      <button
        type="button"
        // Seam for Story 1.6: the glossary panel does not exist yet, so the
        // affordance is disabled and says why rather than being hidden —
        // a button that appears between stories reads as a regression.
        disabled
        title="The domain glossary arrives in Story 1.6."
        className="flex items-center gap-[5px] rounded-full border border-border bg-surface-2 px-[11px] py-[5px] text-[11.5px] font-semibold text-muted-text disabled:opacity-60"
      >
        📖 Glossary
      </button>

      <button
        type="button"
        onClick={() => {
          // Navigate on settle, not on success: if the logout request
          // itself failed, keeping the user parked on a screen they can no
          // longer refresh is the worse outcome — and the cache is cleared
          // either way.
          logout.mutate(undefined, {
            onSettled: () => navigate(LOGIN_ROUTE, { replace: true }),
          });
        }}
        disabled={logout.isPending}
        className="flex items-center gap-[5px] rounded-full border border-border bg-surface-2 px-[11px] py-[5px] text-[11.5px] font-semibold text-muted-text hover:border-steel hover:text-steel disabled:opacity-60"
      >
        ↩ Switch
      </button>

      {stats.isError && (
        <span
          role="status"
          data-testid="stats-error"
          title="The server could not compute your caseload statistics. The numbers below are unknown, not zero."
          className="text-[11px] font-semibold text-error"
        >
          ⚠ Stats unavailable
        </span>
      )}

      <StatTile
        testId="stat-caseload"
        label="Caseload"
        title="Claims in your book of business"
        value={stats.data?.caseload}
        tone="neutral"
        isPending={stats.isPending}
      />
      <StatTile
        testId="stat-active-tx"
        label="Active Tx"
        title="Claims currently in the treatment stage"
        value={stats.data?.activeTx}
        tone="warn"
        isPending={stats.isPending}
      />
      <StatTile
        testId="stat-high-risk"
        label="High Risk"
        title="Severity ≥ 65/100"
        value={stats.data?.highRisk}
        tone="error"
        isPending={stats.isPending}
      />

      {/* Seam for Story 1.5: the SLA strip renders here, between the tiles
          and the user chip. Empty until then — not a skeleton, because
          nothing is loading. */}
      <div data-testid="sla-strip-slot" className="contents" />

      {me.data && (
        <div
          data-testid="user-chip"
          className="flex items-center gap-[7px] rounded-full border border-border py-1 pr-[9px] pl-1"
        >
          <div
            data-testid="user-avatar"
            aria-hidden
            className="flex size-6 items-center justify-center rounded-full bg-gradient-to-br from-steel to-[#124e72] text-[10px] font-bold text-white"
          >
            {/* Derived by the server (`/me`) and merely displayed here.
                Initials are presentation, not a domain derivation (AD-10
                does not bind them) — but the rule already had one
                implementation, and one is the right number. */}
            {me.data.initials}
          </div>
          <div>
            <div className="text-xs font-semibold">{me.data.name}</div>
            <div className="text-[9.5px] text-faint">{ROLE_LABEL[me.data.role]}</div>
          </div>
        </div>
      )}
    </header>
  );
}
