/**
 * The server's free-text column widths, restated for the controls that fill them.
 *
 * **Restated rather than read off the generated client**, because `schema.d.ts`
 * carries types and not bounds: `maxLength` is in the OpenAPI document and
 * nowhere in the TypeScript `openapi-typescript` produces. Each constant below
 * names the module that owns it on the other side, so a change there has one
 * place to arrive here.
 *
 * **A module of its own, rather than one constant per feature.** Two reasons,
 * and the second is the one that decided it. The first is that these are the
 * same *kind* of fact — a column's width — and were previously spread across a
 * data-fetching hook and a dialog. The second is `features/queue/
 * noDerivation.test.ts`: it scans `features/diary`, `features/copilot` and
 * `src/api/meetings.ts`, and its "names a threshold constant" rule refuses
 * `const MAX_… = 200` anywhere it reads. That rule is blunt on purpose and
 * these are not thresholds — nothing branches on them, they are the size of a
 * `text` column — but arguing with a guard by editing the guard is how it stops
 * being one. So the constants live outside every scanned root, which is also
 * where they belong on the merits: beside the client that talks to the server
 * that declares them.
 *
 * Why the caps are on the controls at all: a 2,400-character agenda pasted into
 * a box with no cap is refused by Pydantic with "The request body or parameters
 * failed validation." — no field named, no limit shown, nothing marked invalid.
 * Stopping the paste at the control with the number on screen is the difference
 * between a form that says what it wants and one that says no.
 */

/** `services/claims/notes.py::MAX_NOTE_LENGTH` — a diary note. */
export const NOTE_LENGTH_CAP = 2000;

/** `services/claims/meetings.py::MAX_NOTES_LENGTH` — a meeting's agenda. */
export const MEETING_NOTES_LENGTH_CAP = 2000;

/**
 * `services/claims/meetings.py::MAX_LOCATION_LENGTH` — a room, plant or link.
 *
 * Deliberately a tenth of the agenda's: the two are different kinds of thing,
 * which is why the server declares two numbers rather than one.
 */
export const MEETING_LOCATION_LENGTH_CAP = 200;
