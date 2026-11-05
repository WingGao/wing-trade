from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.domain.exceptions import DomainError


def error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _status_for_code(code: str) -> int:
    if code in {"NOT_FOUND", "TRADE_NOT_FOUND"}:
        return 404
    if code in {"INSUFFICIENT_LOT_QTY", "LOT_HAS_ACTIVE_MATCHES", "DUPLICATE_TRADE"}:
        return 409
    if code in {"VALIDATION_ERROR", "INVALID_QUANTITY", "INVALID_PRICE", "NEGATIVE_FEE", "FUTURE_LOT", "MATCH_QUANTITY_MISMATCH", "LOT_OWNERSHIP_MISMATCH"}:
        return 422
    return 400


async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=_status_for_code(exc.code), content=error_payload(exc.code, exc.message, exc.details))


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content=error_payload("VALIDATION_ERROR", "request validation failed", {"errors": exc.errors()}))


async def integrity_error_handler(_request: Request, _exc: IntegrityError) -> JSONResponse:
    return JSONResponse(status_code=409, content=error_payload("CONFLICT", "resource conflicts with an existing record"))


async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content=error_payload("INTERNAL_ERROR", str(exc)))

