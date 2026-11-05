from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.models import Fees


def begin_write(session: Session) -> None:
    if session.in_transaction():
        session.rollback()
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
    else:
        session.begin()


def finish_write(session: Session, success: bool) -> None:
    if success:
        session.commit()
    else:
        session.rollback()


def fees_from(data: Any) -> Fees:
    source = data.fees or data
    return Fees(
        commission=source.commission,
        stamp_duty=source.stamp_duty,
        transfer_fee=source.transfer_fee,
        other_fee=source.other_fee,
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def decimal_dict(value: Decimal | None) -> str | None:
    return None if value is None else str(value)

