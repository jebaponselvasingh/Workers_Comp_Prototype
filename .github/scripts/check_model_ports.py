"""AD-5's port posture, checked against the *rendered* compose documents.

Run by `.github/workflows/ci.yaml` over four files: the dev profile, the dev
profile merged with the GPU overlay, the e2e profile, and — since Story 8.2 —
the full production stack (dev + GPU + prod overlay).

The rule this enforces is a negative, which is what makes it worth a CI job at
all. "The `ollama` service declares no `ports:` mapping, ever" is invisible in
a diff — a published port arrives as one added line that reviews as a
convenience and turns an internal, unauthenticated inference endpoint into a
host-reachable one that will accept a prompt containing PHI. Nothing else in
the build would fail.

Rendered rather than grepped, because the overlay is the case that matters: a
port added in `compose.gpu.yaml` does not appear in `compose.yaml`'s text at
all, and the merged document is the thing that actually runs.

The e2e profile is checked from the opposite direction. It must run
`model-stub` — deterministic, no downloads, no inference — and must *not*
declare a real `ollama` service, because AD-15 wants the test stack
reproducible and a CI runner has no business pulling several gigabytes of
model weights.
"""

import json
import sys
from pathlib import Path
from typing import Any

#: Which service each profile is expected to serve the model from, and which it
#: must not have at all. Data rather than three `if` blocks, so adding a fourth
#: profile is a row.
EXPECTED: dict[str, tuple[str, str | None]] = {
    "dev.json": ("ollama", "model-stub"),
    "gpu.json": ("ollama", "model-stub"),
    "e2e.json": ("model-stub", "ollama"),
    # The production stack: dev + GPU + the Story 8.2 TLS overlay. It is the
    # profile nothing else can check — no server test boots it and no e2e spec
    # drives it — and it is exactly where a published model port would be
    # rationalised as "the GPU host needs it for debugging".
    "prod.json": ("ollama", "model-stub"),
}


def check(path: Path) -> list[str]:
    services: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")).get("services", {})
    expected = EXPECTED.get(path.name)
    if expected is None:
        # Named rather than a `KeyError` traceback. This runs in CI, where the
        # useful failure says which file arrived and which are understood — a
        # renamed profile would otherwise look like a crashing script rather
        # than an unchecked compose file.
        return [
            f"{path.name}: no expectations declared for this profile "
            f"(known: {', '.join(sorted(EXPECTED))}) — a profile nobody checks "
            "is a profile that can publish the model port"
        ]
    required, forbidden = expected
    problems: list[str] = []

    service = services.get(required)
    if service is None:
        problems.append(
            f"{path.name}: no {required!r} service in the rendered config — "
            "did it move or get renamed?"
        )
    elif service.get("ports"):
        problems.append(
            f"{path.name}: {required!r} publishes {service['ports']}; the model server is "
            "reachable on the internal network only (AD-5)."
        )

    if forbidden and forbidden in services:
        problems.append(
            f"{path.name}: declares a {forbidden!r} service, which this profile must not have"
        )
    return problems


def main(argv: list[str]) -> int:
    problems = [problem for arg in argv for problem in check(Path(arg))]
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print("ok: no profile publishes a model-server port")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
