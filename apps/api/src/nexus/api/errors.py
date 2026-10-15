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
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
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
    parts: list[str] = []
    for error in exc.errors():
        location = error.get("loc", ())
        field = ".".join(str(part) for part in location if part != "body")
        message = str(error.get("msg", "Invalid value"))
        parts.append(f"{field}: {message}" if field else message)
    if not parts:
        return "Request validation failed."
    return "; ".join(parts)
