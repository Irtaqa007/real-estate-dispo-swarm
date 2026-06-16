"""Global exception handlers for consistent API error responses.

Ensures all errors return a uniform shape:
  { "detail": "<human-readable message>" }

This prevents object-type detail fields (like the deal dedup response)
from appearing as "[object Object]" on the frontend.
"""

import logging
from typing import Union

from fastapi import Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
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


async def response_validation_handler(request: Request, exc: ResponseValidationError) -> JSONResponse:
    """Handle ResponseValidationError (response data doesn't match the schema).

    In FastAPI 0.111.0, ResponseValidationError stores:
    - exc.errors  : list of Pydantic error dicts ({"loc", "msg", "type", "input"})
    - exc.body    : raw response content (detached ORM objects — do NOT access)

    IMPORTANT: Never pass exc as a logging %s argument — its __str__() calls
    repr() on body items, which can trigger lazy loads on detached ORM instances.
    """
    try:
        # (1) exc.errors() returns the actual Pydantic validation error dicts
        #     (inherited from ValidationException → stored as self._errors)
        errors = exc.errors()
        if errors and isinstance(errors, list):
            logger.error(
                "Response validation error on %s %s:\n%s",
                request.method, request.url.path,
                _format_validation_errors(errors),
            )
        else:
            # (2) Fall back: log generic info without touching exc.body items
            body = getattr(exc, "body", None)
            item_type = type(body[0]).__name__ if isinstance(body, list) and body else "?"
            item_count = len(body) if isinstance(body, list) else "?"
            logger.error(
                "Response validation error on %s %s (%s item(s) of type %s)",
                request.method, request.url.path, item_count, item_type,
            )
    except Exception:
        logger.error(
            "Response validation error on %s %s (unable to format details)",
            request.method, request.url.path, exc_info=True,
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error — response data does not match the API schema. Check server logs for details."},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions (500 Internal Server Error)."""
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method, request.url.path, exc, exc_info=True,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def _format_validation_errors(errors: Union[list, str]) -> str:
    """Format Pydantic validation errors into a readable string.

    The "input" field of a Pydantic error dict may contain ORM objects that
    are detached from the session — accessing their attributes via repr()
    would trigger DetachedInstanceError. Use _safe_input_repr() to avoid this.
    """
    if isinstance(errors, str):
        return errors
    lines = []
    for err in errors:
        loc = " -> ".join(str(l) for l in err.get("loc", []))
        msg = err.get("msg", "Unknown error")
        inp = err.get("input", "")
        typ = err.get("type", "")
        lines.append(f"  [{typ}] {loc}: {msg} (input={_safe_input_repr(inp)})")
    return "\n".join(lines) if lines else "(empty error list)"


def _safe_input_repr(value: object) -> str:
    """Safely format a value for Pydantic error output.

    The value could be:
    - A primitive (str, int, float, bool, None) — use repr()
    - A container (list, dict) — use repr() with try/except
    - An ORM object (Buyer, Deal, etc.) — use type name + memory id
      (no attribute access to avoid DetachedInstanceError)
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return repr(value)
    if isinstance(value, (list, dict)):
        try:
            return repr(value)
        except Exception:
            return f"<{type(value).__name__} len={len(value)}>"
    # ORM object or other complex type — safe representation
    return f"<{type(value).__name__} at 0x{id(value):x}>"


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
