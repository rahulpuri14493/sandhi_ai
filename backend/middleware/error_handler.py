from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


def _serializable_errors(errors: list) -> list:
    """Convert Pydantic error dicts to JSON-safe format (ctx values may be Exceptions)."""
    result = []
    for err in errors:
        safe = {k: v for k, v in err.items() if k != "ctx"}
        if "ctx" in err:
            safe["ctx"] = {k: str(v) for k, v in err["ctx"].items()}
        result.append(safe)
    return result


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": _serializable_errors(exc.errors())},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


async def general_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )
