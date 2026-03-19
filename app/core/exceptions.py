import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class PDFParseError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class GenerationError(Exception):
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ValidationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class SessionNotFoundError(Exception):
    def __init__(self, session_id: str):
        self.message = f"Session '{session_id}' not found or expired"
        super().__init__(self.message)


async def pdf_parse_error_handler(request: Request, exc: PDFParseError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "pdf_parse_error", "message": exc.message},
    )


async def generation_error_handler(request: Request, exc: GenerationError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "generation_error", "message": exc.message},
    )


async def session_not_found_handler(request: Request, exc: SessionNotFoundError):
    return JSONResponse(
        status_code=404,
        content={"error": "session_not_found", "message": exc.message},
    )


async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all for any unhandled exception — logs the full traceback and returns 500."""
    logger.exception(
        "Unhandled exception on %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "message": "An unexpected error occurred."},
    )
