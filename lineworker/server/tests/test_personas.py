"""Persona label + initials derivation (Story 1.3, Task 3).

Labels are derived from stored scope rather than stored as strings, so
these are pure-function tests. The prototype's hand-written labels are the
visual contract; where it was internally inconsistent (it abbreviated
Caterpillar to "CAT" for one handler and spelled "John Deere" out for
another) the derived form uses `employer.short_name` uniformly — a label
that cannot drift from the assignments it describes.
"""

from data.models.enums import UserRole
from data.repositories.identity import initials, persona_label


def test_full_portfolio_supervisor_is_marked_without_a_claim_count() -> None:
    """The prototype said "All 100 claims"; producing that number meant
    counting the claim table from a pre-auth endpoint (AD-7). Reviewed and
    changed 2026-08-10 — the hint stays legible, it just stops publishing
    the portfolio size to unauthenticated callers."""
    assert (
        persona_label("David Bline", UserRole.supervisor, True, [])
        == "David Bline — WC Supervisor (Full portfolio)"
    )


def test_scoped_supervisor_lists_its_employers() -> None:
    assert (
        persona_label("Jennifer Park", UserRole.supervisor, False, ["Toyota", "GM", "3M"])
        == "Jennifer Park — WC Supervisor (Toyota/GM/3M)"
    )


def test_analyst_is_marked_as_a_view_not_a_book() -> None:
    assert (
        persona_label("David Bline", UserRole.analyst, True, [])
        == "David Bline — WC Supervisor (Analyst view)"
    )


def test_handler_lists_its_employers_middot_separated() -> None:
    assert (
        persona_label(
            "Kaya Johnson",
            UserRole.handler,
            False,
            ["Caterpillar", "GE", "John Deere", "Whirlpool"],
        )
        == "Kaya Johnson — Handler (Caterpillar · GE · John Deere · Whirlpool)"
    )


def test_single_employer_handler_has_no_separator() -> None:
    assert (
        persona_label("Sarah Williams", UserRole.handler, False, ["3M"])
        == "Sarah Williams — Handler (3M)"
    )


def test_initials_take_the_first_two_words() -> None:
    assert initials("David Bline") == "DB"
    assert initials("Fatima Al-Mansoori") == "FA"
    assert initials("Liam O'Sullivan") == "LO"


def test_initials_survive_a_single_name() -> None:
    assert initials("Cher") == "C"


def test_a_persona_with_no_employers_says_so_rather_than_rendering_empty_parens() -> None:
    assert (
        persona_label("Nobody Home", UserRole.handler, False, [])
        == "Nobody Home — Handler (No employers assigned)"
    )
