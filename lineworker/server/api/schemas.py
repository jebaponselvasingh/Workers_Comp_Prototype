"""Shared API model base — the camelCase boundary (Naming convention).

DB and Python are snake_case; API JSON and TypeScript are camelCase. That
translation happens in exactly one place: the alias generator configured
here. Every request and response model in every story inherits it, so the
generated OpenAPI client's field names are a property of the convention
rather than of whoever wrote the endpoint.

`populate_by_name` keeps the Python-side field names usable when
constructing models in tests and services.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
