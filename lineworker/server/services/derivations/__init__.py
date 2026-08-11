"""The one registry of derived-value computers (AD-10).

Import this package to reach a derivation; never re-implement one at a call
site. Entries register themselves on import, so the imports below are the
registration list — a derived value that is not imported here does not
exist as far as `get()` is concerned.

    thresholds = await rules.thresholds_for(db)
    risk = derivations.risk.for_thresholds(thresholds)
    band = risk.of(claim.severity_score)

Story 1.4 registered `risk`. Story 2.1 adds the four the queue needs —
`days_open`, `siu_review`, `rtw_blocked`, `payment_due` — and moves the
parameter source from `Settings` to the versioned JDM documents in `rules/`
(AD-8), which is why the builder argument is now `DerivationThresholds`.
`total_paid`, `total_incurred` and the treatment-phase derivations join them
as their stories land — each one function, here, called by every consumer.

**Naming rule for the modules below** (code review, 2026-08-10): a module
is named after the *rule* (`risk_band`, `open_duration`, `queue_flags`),
never after the derived value it exports (`risk`, `days_open`).
Re-exporting a name that matches its own module silently rebinds the package
attribute — `import services.derivations.risk` would hand back the
`Derivation` instance, and `services.derivations.risk.RiskBand` would raise
`AttributeError` with no obvious cause. This package is the template every
later derivation copies, so the trap is worth avoiding by convention rather
than documenting per entry.
"""

from services.derivations.open_duration import OpenDurationDerivation, days_open, utc_today
from services.derivations.queue_flags import (
    PaymentDueDerivation,
    RtwBlockedDerivation,
    SiuReviewDerivation,
    hash_bucket,
    payment_due,
    rtw_blocked,
    siu_review,
)
from services.derivations.registry import Derivation, get, register, registered_names
from services.derivations.risk_band import RiskBand, RiskDerivation, risk

__all__ = [
    "Derivation",
    "OpenDurationDerivation",
    "PaymentDueDerivation",
    "RiskBand",
    "RiskDerivation",
    "RtwBlockedDerivation",
    "SiuReviewDerivation",
    "days_open",
    "get",
    "hash_bucket",
    "payment_due",
    "register",
    "registered_names",
    "risk",
    "rtw_blocked",
    "siu_review",
    "utc_today",
]
