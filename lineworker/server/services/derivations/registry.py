"""The registry mechanism itself — AD-10's "exactly one computing function".

A derived value (`risk` here; `days_open`, `total_incurred`, `siu_review`
and the rest as their stories land) is registered once under its canonical
name. Consumers ask the registry for it; nobody re-derives.

Registration goes through `register()` rather than a plain module-level
constant so that a second computer for the same name is a loud error at
import time. That is the failure AD-10 exists to prevent: two functions,
each defensible on its own, disagreeing between the queue card and the
dashboard KPI.

Derivations are built *from `Settings`* rather than being plain functions,
because AD-8 puts their parameters in configuration (and, when ZEN lands,
in a versioned JDM document). `for_settings` is therefore the only way to
get a usable computer, which keeps "where did this threshold come from?"
answerable at every call site.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from config import Settings


@dataclass(frozen=True)
class Derivation[T]:
    """One derived value: its canonical name, why it exists, how to build it."""

    name: str
    describes: str
    build: Callable[[Settings], T]

    def for_settings(self, settings: Settings) -> T:
        return self.build(settings)


_REGISTRY: dict[str, Derivation[Any]] = {}


def register[T](derivation: Derivation[T]) -> Derivation[T]:
    if derivation.name in _REGISTRY:
        raise ValueError(
            f"{derivation.name!r} already has a registered computer "
            f"({_REGISTRY[derivation.name].describes}) — AD-10 allows exactly one"
        )
    _REGISTRY[derivation.name] = derivation
    return derivation


def get(name: str) -> Derivation[Any]:
    """Look a derivation up by name.

    Raises rather than returning `None`: a consumer that asked for a
    derived value and got nothing should stop, not fall back to computing
    it locally — which is the exact drift this registry prevents.
    """
    if name not in _REGISTRY:
        raise KeyError(f"no registered computer for {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def registered_names() -> frozenset[str]:
    return frozenset(_REGISTRY)
