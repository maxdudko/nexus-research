"""HTTP envelopes for research and request-validation failures."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT

from nexus.ai.errors import ResearchError


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers that return the research error envelope."""

    @app.exception_handler(ResearchError)
    async def research_error(
        _request: Request,
        exc: ResearchError,
    ) -> JSONResponse:
        headers = None
        if exc.retry_after is not None:
            headers = {"Retry-After": str(exc.retry_after)}
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": _validation_message(exc),
                }
            },
        )


def _validation_message(exc: RequestValidationError) -> str:
    """Name the field only. Provider and Pydantic text can contain the value."""
    parts: list[str] = []
    for error in exc.errors():
        if not isinstance(error, dict):
            continue
        location = error.get("loc", ())
        if isinstance(location, tuple | list):
            field = ".".join(str(part) for part in location if part != "body")
        else:
            field = ""
        parts.append(f"{field}: invalid value" if field else "invalid value")
    if not parts:
        return "Request validation failed."
    return "; ".join(parts)
