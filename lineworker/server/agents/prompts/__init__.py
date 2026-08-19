"""Prompts are versioned files loaded by key — the **only** instruction channel.

The spine's Prompts convention, implemented at its first use. Four kind prompts
and one shared system preamble live beside this module as `.md` files; nothing
in the build writes an instruction to a model as a Python string literal.

Three things follow from that, and each is the reason for the rule:

- **A prompt change is a diff a reviewer reads as prose.** An instruction
  embedded in an f-string three call frames from the model is a change nobody
  reviews as a prompt, and prompts are where an "always be helpful and estimate
  the likely settlement value" gets added by somebody being helpful.
- **The version is in the file and travels with the answer.** Each file's first
  line declares its key and version, the loader refuses a file whose header
  disagrees with its name, and `prompt_version` is written into every
  `ai_insight.content`. So "which instructions produced this card?" is
  answerable from the row — the same property `thresholds_version` and
  `rules_version` give every rule-driven payload in this codebase.
- **AD-16 has one place to be true.** The shared preamble carries the standing
  "material to analyse, never instructions" clause and the rules about figures;
  every kind prompt is appended to it, so no kind can be written that quietly
  lacks it. `tests/test_prompt_injection_fixtures.py` asserts the clause is
  present in the composed system message for every kind.

## What is *not* here

No template engine, no variable interpolation, no partials. The material a
prompt is about — the claim's own text, the retrieved chunks, the deterministic
figures — is assembled in `agents/insights.py` and sent as the **user** message,
per-item delimited and tagged with its source id. It is never spliced into the
instruction text, which is the structural half of AD-16: injected content and
instructions arrive in different messages, so there is no concatenation for a
crafted string to escape from.
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

#: The key of the shared preamble every kind prompt is composed on top of.
SYSTEM_KEY = "system"

PROMPTS_DIR = Path(__file__).resolve().parent

#: `<!-- prompt: reserve_adequacy_review v1 -->` — the first line of every file.
#:
#: An HTML comment because these are Markdown documents somebody reads, and a
#: line that renders as nothing is the one form of front matter that does not
#: also have to be stripped for correctness. The key is repeated inside the
#: header rather than taken from the filename alone so that a copy-pasted file
#: fails to load instead of silently serving another kind's instructions under
#: a new name.
HEADER = re.compile(r"^<!--\s*prompt:\s*(?P<key>[a-z_]+)\s+v(?P<version>\d+)\s*-->\s*$")


@dataclass(frozen=True)
class Prompt:
    """One versioned instruction file: its key, its version, and its text.

    `version` is not decoration. It is written into every `ai_insight.content`
    the prompt produced, so a card generated before a wording change is
    distinguishable from one generated after — which is what makes "regenerate
    the affected claims" a query rather than a guess.
    """

    key: str
    version: int
    text: str


@cache
def load(key: str) -> Prompt:
    """The prompt filed under `key`, parsed and validated. Cached per process.

    Cached because a prompt file is immutable for the lifetime of a deployment —
    it ships in the image — and a refresh run reads five of them per claim.
    `functools.cache` rather than a module-level dict built at import time, so a
    missing file is an error at the call that wanted it (naming the key) rather
    than an `ImportError` at process start naming nothing.

    Raises `FileNotFoundError` for an unknown key and `ValueError` for a file
    whose header is missing or names a different key. Both are startup-class
    failures: the set of keys is closed and lives in this package, so reaching
    either means the image was built wrong.
    """
    path = PROMPTS_DIR / f"{key}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no prompt file for key {key!r} in {PROMPTS_DIR}")

    lines = path.read_text(encoding="utf-8").splitlines()
    header = HEADER.match(lines[0]) if lines else None
    if header is None:
        raise ValueError(
            f"prompt {key!r} is missing its version header; the first line must read "
            f"'<!-- prompt: {key} v1 -->'"
        )
    if header["key"] != key:
        raise ValueError(
            f"prompt file {path.name} declares key {header['key']!r}; a copied prompt must "
            "declare the key it is filed under"
        )
    return Prompt(key=key, version=int(header["version"]), text="\n".join(lines[1:]).strip())


def system_message(key: str) -> tuple[str, int]:
    """The composed system message for one kind, and that kind's prompt version.

    The shared AD-16 preamble first, the kind's own instructions second, one
    blank line between. Composed here rather than by each caller so that no kind
    can be sent to a model without the preamble — the clause about injected
    content being data is not something a kind prompt is trusted to remember.

    The version returned is the **kind's**, which is the one a card records. The
    preamble's own version is a property of the whole build rather than of a
    card, and is asserted in `tests/test_prompt_injection_fixtures.py` rather
    than persisted per row.
    """
    kind_prompt = load(key)
    return f"{load(SYSTEM_KEY).text}\n\n{kind_prompt.text}", kind_prompt.version


__all__ = ["HEADER", "PROMPTS_DIR", "SYSTEM_KEY", "Prompt", "load", "system_message"]
