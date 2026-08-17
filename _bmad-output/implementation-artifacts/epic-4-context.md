# Epic 4 Context: Diary, Meetings & Stakeholder Emails

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Epic 4 gives the handler the coordination half of the job and gives every coordination act a paper trail. It delivers the right-pane Diary panel with its three sub-tabs — Notes, Meetings, Emails — backed by three new persisted tables: dated claim-linked diary notes, claim-linked meetings across ten stakeholder types with complete / delete / convert-to-email, and templated stakeholder emails composed from six claim-aware templates. It matters because in the prototype all of this lived in throwaway in-memory stores that a page reload erased; here every note, meeting and email is a row written by an audited command, attributable to an actor and recoverable. The epic deliberately stops short of real egress: an email "send" and a meeting "schedule" are logs of intent, not SMTP or ICS traffic. This is also where the copilot panel shell first appears on screen, carrying the Diary tab while its Actions (chat) tab stays visibly disabled until Epic 6.

## Stories

- Story 4.1: Meeting Scheduling & Management
- Story 4.2: Claim-Linked Diary Notes
- Story 4.3: Templated Stakeholder Emails

## Requirements & Constraints

- **Meetings.** A scheduler modal offering ten meeting types, a read-only linked claim, required date plus optional time, participant checkboxes across the six stakeholder roles, notes/agenda and location/link. Saved meetings list sorted by date with Upcoming/Done styling showing type, date/time, location, claim, notes and participant tags. "Done" and "Delete" are persisted, audited status changes. A seed migration gives each handler persona two demo meetings, which is what closes the seeded-meetings clause of the login requirement.
- **Diary notes.** Dated notes tagged to the active claim, newest-first, with a time-of-day greeting, today's date, the active claim and a today's-meetings summary carrying "Done" and "Open Claim" actions wired to the meeting data and queue selection. The prototype's note-triggered SLA recalculation is obsolete — the SLA strip is always server-computed.
- **Emails.** A composer with recipient checkboxes, required subject, body, read-only claim reference and a Normal/High/Urgent priority. Six quick templates live in a table, not in code, and pre-fill subject, body and recipient set with merged claim data. "Send" persists a log row and confirms non-blockingly; the Emails sub-tab lists sends reverse-chronologically with subject, recipients, snippet and sent badge. A meeting's "Email participants" opens the composer pre-filled with that meeting's participants and claim — the convert-to-email path.
- **Validation and states.** Missing required fields block save with inline validation at the control, never a native dialog. Every list surface carries explicit loading, empty and error states.
- **Universal.** Every mutation persists with an audit event in the same transaction, under optimistic-concurrency compare-and-swap on the entity version. Reads and writes are scoped server-side; write commands accept only the handler role. Each story ships one Playwright spec against the freshly reset composed stack as its done-gate.

## Technical Decisions

- **Ownership.** The diary aggregate — notes, meetings, emails, templates — is owned by `services/claims`; its commands are the only writers of those tables. Timeline events, if a mutation warrants one, are emitted only as a side effect of the owning command.
- **Send-as-log is an architectural decision, not a shortcut.** Real SMTP and calendar/ICS egress is a deferred later epic with its own compliance review. Do not add an outbound mail or calendar client, and do not model the log row as if a delivery attempt occurred.
- **New tables, new PHI.** Diary notes, meetings and email bodies are PHI-class: encrypted at rest and in transit, never written into operational logs (IDs and event names only), and they must be reachable by the single purge cascade owned by `services/audit`. Add them to that cascade's coverage rather than inventing per-table deletion.
- **Frontend state discipline.** Server state exclusively through TanStack Query with keys declared in the shared `queryKeys` module. Optimistic updates only for user-entered scalars; a 409 rolls back and renders the returned fresh entity inline. Local UI state via React state/context only.
- **Rate the panel shell carefully.** The copilot panel is introduced here as a shell hosting the Diary sub-tabs. Do not stub chat plumbing — the Actions tab renders disabled, and Epic 6 brings the assistant-ui LangGraph runtime over SSE.
- **Forward seam for the copilot.** Diary notes and email bodies are untrusted content that will later enter model context. Store them as plain data with no instruction-channel semantics, and expect them to be delimited and tagged at retrieval time, never treated as commands.
- **Mark-stale seam.** Every audited command written here must gain a mark-stale call to the embeddings owner when Epic 6 arrives if it mutates an embedded source field — leave the seam rather than pre-building it.

## UX & Interaction Patterns

- **Diary sub-tabs** (Notes · Meetings · Emails) live in the right pane of the handler's three-pane layout, with reverse-chronological lists and Upcoming/Done meeting styling carrying the ✓ / ✉ / Delete actions.
- **Two modals**, both following the same discipline: required-field validation shown inline at the field, dismissal without side effects, and a read-only claim reference the user cannot retarget.
- **Disabled-with-tooltip is the house pattern for seams.** "Email participants" ships disabled in 4.1 and enables in 4.3; Epic 3's "Log Diary Entry" deep link ships disabled and this epic enables it, opening the Notes sub-tab focused on the add-note input.
- **Feedback is non-blocking.** There is currently no toast provider, queue or live-region host in the SPA — earlier stories satisfied the requirement with in-place updates plus a polite live region and inline refusals. This epic has two surfaces that want a real toast (email send, meeting save), so it is the natural place to build that primitive once; if you do not, match the existing polite-live-region pattern rather than assuming a toast exists.
- **Visual identity:** the prototype's dark, information-dense console aesthetic and ok/warn/error status semantics, via Tailwind tokens over vendored shadcn/ui components.

## Cross-Story Dependencies

- **Epics 1–2 → all of Epic 4.** Scaffold, audit table, auth/scope dependency, the seeded personas and claims, the queue selection this epic's "Open Claim" action drives, and the three-pane shell the Diary panel occupies.
- **4.1 → 4.2 → 4.3.** Meetings must exist before the Notes tab can summarize today's meetings, and before the composer can be pre-filled from a meeting. The "Email participants" button is created disabled in 4.1 and enabled by 4.3 — the same control, two stories.
- **Epic 3 → 4.2.** Story 3.5's action checklist ships a disabled "Log Diary Entry" deep link; 4.2 enables that target. Note that 3.5 also omitted the diary check-in's completion toggle, because there was no entity to write to — decide during 4.2 whether that row gains a completion control or stays a link.
- **Timeline event tag styling.** Runtime-only event tags are currently rendered raw, with a proper label map deferred across several stories. Meetings and diary notes are the next two tags to join that set; if this epic adds them, the map is now overdue.
- **Forward:** Epic 6's copilot reads diary and meeting content as retrieval material and will draft the RTW letter through the same human-gated write path; Epic 8's purge cascade must cover the three tables created here.
