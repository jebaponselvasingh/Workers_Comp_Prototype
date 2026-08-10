/**
 * UX-DR1 — the login screen (AC 1, AC 2).
 *
 * Visual contract from the prototype (gradient backdrop, white card,
 * dataset banner, three role cards with supervisor pre-selected, persona
 * dropdown, "Enter Console →"). The prototype's `pickRole`/`doLogin` are
 * *not* ported: role membership and persona labels come from
 * `/api/personas`, and login is a server round-trip that mints a session.
 *
 * Accessibility over fidelity in one place: the prototype's role cards are
 * click-handled `<div>`s. Here they are real radio inputs inside a
 * fieldset, so they are keyboard-operable and arrow-navigable, and e2e
 * specs can select them by accessible role (AD-15's selector policy).
 */
import { useId, useMemo, useState } from "react";
import { Navigate, useNavigate } from "react-router";

import { type Persona, type UserRole, useLogin, useMe, usePersonas } from "@/api/auth";
import { homeRouteFor } from "@/features/shell/routes";

const ROLE_CARDS: { role: UserRole; icon: string; title: string; blurb: string }[] = [
  { role: "supervisor", icon: "👔", title: "Supervisor", blurb: "Portfolio, SLA, handler performance" },
  { role: "handler", icon: "📋", title: "Claims Handler", blurb: "Caseload, case detail, copilot" },
  { role: "analyst", icon: "📊", title: "Data Analyst", blurb: "Fraud, trends, KPI drill-down" },
];

const DATASET_BANNER =
  "📊 Dataset: WC_Manufacturing_Claims_2026.xlsx · 100 unique employees · 10 employers · " +
  "15 plants · 27 injury types";

export function LoginScreen() {
  const navigate = useNavigate();
  const personaSelectId = useId();
  // Supervisor pre-selected on first render, per the prototype's boot state.
  const [role, setRole] = useState<UserRole>("supervisor");
  const [chosenPersonaId, setChosenPersonaId] = useState<number | null>(null);
  // Set only if the server hands back a role this build has no shell for —
  // rare, but better surfaced than redirected into a loop.
  const [unroutableRole, setUnroutableRole] = useState<string | null>(null);

  // `isSuccess`, not just `data`: TanStack keeps the last good `me` when a
  // *refetch* fails, so after a session expires mid-visit this screen would
  // still see a signed-in user, redirect to their shell, and be redirected
  // straight back here by the guard's 401 — the infinite loop `routes.ts`
  // warns about, ending in "Maximum update depth exceeded" and a blank
  // page. Being signed in means the last `/me` *succeeded*, not that one
  // once did. (Found writing the cache-leak regression test, 2026-08-10.)
  const { data: me, isSuccess: sessionConfirmed } = useMe();
  const personas = usePersonas();
  const login = useLogin();

  const forRole = useMemo(
    () => (personas.data ?? []).filter((persona: Persona) => persona.role === role),
    [personas.data, role],
  );

  // The selection is *derived*, not synchronised: an explicit choice
  // counts only while it is still in the current role's list, so switching
  // roles falls back to that role's first persona without an effect (and
  // without a window in which a stale id from the previous role could be
  // submitted).
  const personaId =
    forRole.some((persona) => persona.id === chosenPersonaId) && chosenPersonaId !== null
      ? chosenPersonaId
      : (forRole[0]?.id ?? null);

  const noPersonasForRole = personas.isSuccess && forRole.length === 0;

  // Already signed in (a live session, then a visit to "/"): go to the
  // shell rather than offering to log in again.
  const currentHome = me && sessionConfirmed ? homeRouteFor(me.role) : null;
  if (currentHome) return <Navigate to={currentHome} replace />;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (personaId === null) return;
    // Stale-alert reset (code review, 2026-08-10): this state is set but
    // was never cleared, so one unroutable login left the red alert
    // pinned to the form through every later attempt — including ones
    // that failed for an entirely different reason, whose own message the
    // alert's ternary then never reached.
    setUnroutableRole(null);
    login.mutate(personaId, {
      onSuccess: (session) => {
        const home = homeRouteFor(session.role);
        if (home) navigate(home, { replace: true });
        else setUnroutableRole(session.role);
      },
    });
  };

  return (
    // overflow-y-auto + vertical padding: the card is a fixed-size box in a
    // centred flex container, so on a short viewport (devtools open, 200%
    // zoom, landscape phone) it would otherwise overflow in both directions
    // with nothing scrollable — clipping the heading off the top and
    // "Enter Console →" off the bottom, unreachable.
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-linear-[135deg,var(--color-login-from),var(--color-login-to)] p-4">
      <form
        onSubmit={submit}
        aria-labelledby="login-title"
        className="w-[500px] max-w-[92vw] rounded-[10px] bg-surface px-[42px] py-9 shadow-[0_24px_60px_rgba(0,0,0,.4)]"
      >
        <div className="mb-[22px] flex items-center gap-2.5">
          <span
            aria-hidden
            className="flex size-[34px] items-center justify-center rounded-[5px] bg-brand font-mono text-[17px] font-bold text-white"
          >
            L
          </span>
          <div>
            <h1 id="login-title" className="font-display text-[21px] font-bold">
              LINEWORKER
            </h1>
            <span className="block text-[11px] text-faint">
              Manufacturing Workers Compensation Console — United States
            </span>
          </div>
        </div>

        <p className="mb-[18px] inline-block rounded bg-steel-soft px-2.5 py-[5px] text-[10.5px] font-bold text-steel">
          {DATASET_BANNER}
        </p>

        <fieldset className="mb-3.5">
          <legend className="mb-[5px] block text-[11px] font-bold uppercase tracking-[.4px] text-muted-text">
            Select your role
          </legend>
          <div className="grid grid-cols-3 gap-2">
            {ROLE_CARDS.map((card) => (
              <label
                key={card.role}
                className={`relative cursor-pointer rounded-md border-2 px-2.5 py-3 text-center ${
                  role === card.role ? "border-brand bg-brand-soft" : "border-border"
                }`}
              >
                {/* The input covers the whole card rather than being
                    `sr-only`: the card *is* the control (UX-DR1), so the
                    clickable area and the focusable element should be the
                    same rectangle for mouse, keyboard, and automation
                    alike. `opacity-0` hides the native glyph without
                    removing the hit target. */}
                <input
                  type="radio"
                  name="role"
                  value={card.role}
                  checked={role === card.role}
                  onChange={() => {
                    setRole(card.role);
                    // The unroutable-role alert describes the login the
                    // user just attempted, not this new choice.
                    setUnroutableRole(null);
                  }}
                  className="absolute inset-0 size-full cursor-pointer appearance-none opacity-0"
                />
                <span aria-hidden className="block text-xl leading-none">
                  {card.icon}
                </span>
                <span className="mt-[3px] block text-xs font-bold">{card.title}</span>
                <span className="mt-0.5 block text-[9.5px] text-faint">{card.blurb}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <label
          htmlFor={personaSelectId}
          className="mb-[5px] block text-[11px] font-bold uppercase tracking-[.4px] text-muted-text"
        >
          Log in as
        </label>
        <select
          id={personaSelectId}
          value={personaId ?? ""}
          onChange={(event) => setChosenPersonaId(Number(event.target.value))}
          disabled={personas.isPending || forRole.length === 0}
          className="mb-3 w-full rounded border border-border bg-surface-2 px-3 py-[9px] text-[13px] outline-none"
        >
          {forRole.map((persona) => (
            <option key={persona.id} value={persona.id}>
              {persona.label}
            </option>
          ))}
        </select>

        <button
          type="submit"
          disabled={personaId === null || login.isPending}
          className="w-full rounded-[5px] bg-brand py-[11px] font-display text-sm font-bold text-white hover:bg-brand-strong disabled:opacity-60"
        >
          {login.isPending ? "Entering…" : "Enter Console →"}
        </button>

        {/* Inline, never alert() (NFR-3). role=alert so a failure is
            announced to screen readers without stealing focus. */}
        {(login.isError || personas.isError || unroutableRole || noPersonasForRole) && (
          <p role="alert" className="mt-2.5 rounded bg-error-soft px-2.5 py-2 text-xs text-error">
            {login.isError
              ? `Could not enter the console. ${login.error.message}`
              : personas.isError
                ? "Could not load personas. Is the API reachable?"
                : unroutableRole
                  ? `Signed in, but this console has no workspace for the role “${unroutableRole}”.`
                  : // Without this the form just sits there: an empty
                    // dropdown and a disabled button, with nothing failed
                    // and so nothing to explain it.
                    "No personas are configured for this role. Pick another role, or re-seed the demo data."}
          </p>
        )}
      </form>
    </div>
  );
}
