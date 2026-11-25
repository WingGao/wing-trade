from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    SerializerFunctionWrapHandler,
    field_serializer,
    field_validator,
)

from app.domain.time import format_beijing_iso, require_aware_utc

AwareDateTime = Annotated[datetime, AfterValidator(require_aware_utc)]


def to_camel(value: str) -> str:
    """将下划线字段名转换为驼峰别名。"""
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class APIModel(BaseModel):
    """API 请求与响应基类：统一驼峰映射、拒绝二进制浮点，并统一以北京时间序列化时间字段。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_binary_float(cls, value: Any) -> Any:
        """拒绝 JSON 二进制浮点进入精确账务字段。"""
        if isinstance(value, float):
            raise ValueError("JSON floating point values are not accepted for exact ledger fields")
        return value

    @field_serializer("*", mode="wrap", when_used="json")
    def serialize_beijing_datetime(
        self,
        value: Any,
        handler: SerializerFunctionWrapHandler,
    ) -> Any:
        """统一将响应模型中的 datetime 字段序列化为带 +08:00 的北京时间 ISO 字符串。"""
        if isinstance(value, datetime):
            return format_beijing_iso(value)
        return handler(value)


class ErrorBody(APIModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorResponse(APIModel):
    error: ErrorBody


def decimal_string(value: Decimal | None, scale: int = 4) -> str | None:
    if value is None:
        return None
    return f"{value:.{scale}f}"

