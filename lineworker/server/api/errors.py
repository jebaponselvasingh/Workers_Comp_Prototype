"""RFC 9457 problem+json — registered once, inherited by every router.

The Errors convention makes this the *only* error envelope the API speaks.
Registering the handlers on the app (rather than raising problem-shaped
responses per route) is what makes that true for routers that do not exist
yet: a bare `HTTPException` or an unhandled exception in a Story 6 endpoint
still leaves as problem+json.

`type` is a relative URI reference (RFC 9457 §3.1.1 permits one) so the SPA
can switch on a stable machine-readable identifier without us minting a
public documentation domain we do not own.

AD-11: `detail` carries no claim data. The 500 handler deliberately says
nothing about the underlying exception — that goes to the structured log.
"""

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_CONTENT_TYPE = "application/problem+json"

log = structlog.get_logger()


class ProblemDocument(BaseModel):
    """The RFC 9457 body, as a model so it reaches the OpenAPI schema.

    Not `ApiModel`: these member names are fixed by the RFC and are already
    single lowercase words, so the camelCase alias generator must not touch
    them.
    """

    type: str
    title: str
    status: int
    detail: str


class ProblemException(Exception):
    """Raise to answer with a problem+json document."""

    def __init__(
        self,
        *,
        status_code: int,
        title: str,
        detail: str,
        type_: str = "about:blank",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.type = type_
        self.headers = headers

    def to_response(self) -> JSONResponse:
        return problem_response(
            status_code=self.status_code,
            title=self.title,
            detail=self.detail,
            type_=self.type,
            headers=self.headers,
        )


class AuthenticationRequired(ProblemException):
    """No session, an unknown session, or an expired one — always 401 (AC 4)."""

    def __init__(self, detail: str = "Sign in to continue.") -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Unauthenticated",
            detail=detail,
            type_="/problems/unauthenticated",
        )


def problem_response(
    *,
    status_code: int,
    title: str,
    detail: str,
    type_: str = "about:blank",
    headers: dict[str, str] | None = None,
    **extensions: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": type_,
        "title": title,
        "status": status_code,
        "detail": detail,
        **extensions,
    }
    return JSONResponse(
        body,
        status_code=status_code,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
    )


_TITLES = {
    400: "Bad Request",
    401: "Unauthenticated",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Content",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemException)
    async def _problem(_: Request, exc: ProblemException) -> JSONResponse:
        return exc.to_response()

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Covers 404/405 from the router and any plain HTTPException a route
        # raises — the convention holds even where a story forgot about it.
        return problem_response(
            status_code=exc.status_code,
            title=_TITLES.get(exc.status_code, "Error"),
            detail=str(exc.detail),
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title=_TITLES[422],
            detail="The request body or parameters failed validation.",
            type_="/problems/validation-error",
            # Field paths and messages only — never the submitted values,
            # which for this API are claim data (AD-11).
            errors=[
                {"loc": [str(part) for part in err["loc"]], "msg": err["msg"]}
                for err in exc.errors()
            ],
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error(
            "request.unhandled_exception",
            error=type(exc).__name__,
            path=request.url.path,
            method=request.method,
        )
        return problem_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal Server Error",
            detail="The request could not be completed.",
        )
