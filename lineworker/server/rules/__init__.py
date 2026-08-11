"""The ZEN JDM tier (AD-8) — parameters as versioned, effective-dated data.

Two modules and a directory of documents:

- `documents/*.jdm.json` — the authored rule documents, committed so a
  reviewer reads the diff that changes a weight.
- `engine.py` — loads the effective version of a document out of
  `rule_document` and evaluates it. The only importer of `zen`.
- `parameters.py` — validates the evaluated result into a frozen typed
  block. The only place a rule document's key names appear.

Consumers import the *blocks*, never the engine: `services/derivations`
builds its computers from `DerivationThresholds`, `services/worklist` scores
from `PriorityWeights`. That keeps the formula tier ignorant of where its
tunables came from, which is what makes replacing a document a data change
rather than a code change.
"""

from rules.engine import LoadedDocument, RuleDocumentMissing, RuleEvaluationFailed
from rules.parameters import (
    DERIVATION_THRESHOLDS_KEY,
    PRIORITY_WEIGHTS_KEY,
    DerivationThresholds,
    PriorityWeights,
    RuleParameterError,
    thresholds_for,
    weights_for,
)

__all__ = [
    "DERIVATION_THRESHOLDS_KEY",
    "PRIORITY_WEIGHTS_KEY",
    "DerivationThresholds",
    "LoadedDocument",
    "PriorityWeights",
    "RuleDocumentMissing",
    "RuleEvaluationFailed",
    "RuleParameterError",
    "thresholds_for",
    "weights_for",
]
