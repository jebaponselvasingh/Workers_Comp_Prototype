"""The one registry of derived-value computers (AD-10).

Import this package to reach a derivation; never re-implement one at a call
site. Entries register themselves on import, so the imports below are the
registration list — a derived value that is not imported here does not
exist as far as `get()` is concerned.

    risk = derivations.risk.for_settings(settings)
    band = risk.of(claim.severity_score)

Story 1.4 registers `risk`. `days_open`, `total_paid`, `total_incurred`,
`siu_review`, `rtw_blocked`, `payment_due` and the SLA aggregates join it
as their stories land — each one function, here, called by every consumer.

**Naming rule for the modules below** (code review, 2026-08-10): a module
is named after the *rule* (`risk_band`), never after the derived value it
exports (`risk`). Re-exporting a name that matches its own module silently
rebinds the package attribute — `import services.derivations.risk` would
hand back the `Derivation` instance, and `services.derivations.risk.RiskBand`
would raise `AttributeError` with no obvious cause. This package is the
template every later derivation copies, so the trap is worth avoiding by
convention rather than documenting per entry.
"""

from services.derivations.registry import Derivation, get, register, registered_names
from services.derivations.risk_band import RiskBand, RiskDerivation, risk

__all__ = [
    "Derivation",
    "RiskBand",
    "RiskDerivation",
    "get",
    "register",
    "registered_names",
    "risk",
]
