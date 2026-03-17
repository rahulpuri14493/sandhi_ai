from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Handles validation exceptions by returning a JSON response with a 422 status code.

    Args:
    request: The incoming request.
    exc: The validation error.

    Returns:
    A JSON response with a 422 status code and the validation errors.
    """
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": exc.errors()
        },  # Extract validation errors from the exception
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """
    Handles HTTP exceptions by returning a JSON response with the exception's status code and detail.

    Args:
    request: The incoming request.
    exc: The HTTP exception.

    Returns:
    A JSON response with the exception's status code and detail.
    """
    return JSONResponse(
        status_code=exc.status_code,  # Use the exception's status code
        content={"detail": exc.detail},  # Use the exception's detail
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handles general exceptions by returning a JSON response with a 500 status code.

    Args:
    request: The incoming request.
    exc: The general exception.

    Returns:
    A JSON response with a 500 status code and a generic error message.
    """
    # Check if the exception is a StarletteHTTPException before returning a generic error message
    if isinstance(exc, StarletteHTTPException):
        return http_exception_handler(request, exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )
