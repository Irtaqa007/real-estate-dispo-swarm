"""Global exception handlers for consistent API error responses.

Ensures all errors return a uniform shape:
  { "detail": "<human-readable message>" }

This prevents object-type detail fields (like the deal dedup response)
from appearing as "[object Object]" on the frontend.
"""

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle HTTP exceptions with a consistent detail string."""
    detail = _serialize_detail(exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic/FastAPI validation errors."""
    errors = exc.errors()
    messages = []
    for err in errors:
        loc = " -> ".join(str(l) for l in err.get("loc", []))
        msg = err.get("msg", "Invalid value")
        messages.append(f"{loc}: {msg}" if loc else msg)
    detail = "; ".join(messages)
    return JSONResponse(status_code=422, content={"detail": detail})


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions (500 Internal Server Error)."""
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def _serialize_detail(detail: object) -> str:
    """Convert any detail value to a human-readable string.

    FastAPI allows detail to be a string, dict, or list.
    This ensures it's always a clean string for the frontend.
    """
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        # e.g. {"message": "...", "is_duplicate": True, ...}
        return detail.get("message", str(detail))
    if isinstance(detail, list):
        return "; ".join(str(d) for d in detail)
    return str(detail)
