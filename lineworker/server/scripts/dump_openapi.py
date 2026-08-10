"""Print the OpenAPI document to stdout — the input to the generated TS client.

    uv run python -m scripts.dump_openapi > openapi.json

Runs against the app factory only: no lifespan, no database, no network, so
it works in CI and in a clean checkout. `web/package.json`'s `generate:api`
pipes this into openapi-typescript; CI regenerates and diffs the result, so
an endpoint whose Python signature changed without the client being
regenerated fails the build instead of failing a user.
"""

import json
import sys

from api import create_app


def main() -> None:
    json.dump(create_app().openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
