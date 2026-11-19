from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.exceptions import DomainError


def error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _status_for_code(code: str) -> int:
    """将稳定领域错误码映射为统一 HTTP 状态。"""
    if code == "UNAUTHORIZED":
        return 401
    if code == "FORBIDDEN":
        return 403
    if code in {"NOT_FOUND", "TRADE_NOT_FOUND"}:
        return 404
    if code in {
        "INSUFFICIENT_LOT_QTY",
        "LOT_HAS_ACTIVE_MATCHES",
        "DUPLICATE_TRADE",
        "TRADE_REVISION_CONFLICT",
        "TRADE_NOT_EDITABLE",
        "USER_ALREADY_EXISTS",
    }:
        return 409
    if code in {
        "VALIDATION_ERROR",
        "INVALID_QUANTITY",
        "INVALID_PRICE",
        "NEGATIVE_FEE",
        "FUTURE_LOT",
        "MATCH_QUANTITY_MISMATCH",
        "LOT_OWNERSHIP_MISMATCH",
        "ACCOUNT_CURRENCY_MISMATCH",
        "INVALID_EMAIL",
        "INVALID_VERIFICATION_CODE",
        "VERIFICATION_CODE_EXPIRED",
        "VERIFICATION_CODE_ATTEMPTS_EXCEEDED",
    }:
        return 422
    if code == "VERIFICATION_CODE_COOLDOWN":
        return 429
    return 400


async def domain_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """以统一信封返回领域错误，并保持框架异常处理器的通用签名。"""
    if not isinstance(exc, DomainError):
        return await generic_error_handler(_request, exc)
    return JSONResponse(
        status_code=_status_for_code(exc.code),
        content=error_payload(exc.code, exc.message, exc.details),
    )


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """安全序列化 Pydantic 的 Decimal 和异常上下文，避免校验失败变成 500。"""
    if not isinstance(exc, RequestValidationError):
        return await generic_error_handler(_request, exc)
    errors = jsonable_encoder(exc.errors(), custom_encoder={Decimal: str, ValueError: str})
    return JSONResponse(
        status_code=422,
        content=error_payload("VALIDATION_ERROR", "request validation failed", {"errors": errors}),
    )


async def integrity_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    """以通用异常处理器签名返回数据库冲突错误。"""
    return JSONResponse(
        status_code=409,
        content=error_payload("CONFLICT", "resource conflicts with an existing record"),
    )


async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content=error_payload("INTERNAL_ERROR", str(exc)))

