from __future__ import annotations

from collections.abc import Generator

from fastapi import Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.domain.exceptions import AccountCurrencyMismatchError, EntityNotFoundError, ValidationError
from app.domain.markets import currency_for_market
from app.models.ledger import Account, Strategy


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def request_actor(x_actor: str | None = Header(default=None)) -> str | None:
    return x_actor


def ensure_default_context(session: Session) -> tuple[Account, Strategy]:
    account = session.scalar(
        select(Account).where(Account.currency == "CNY").order_by(Account.created_at).limit(1)
    )
    if account is None:
        account = Account(name="Default Account", currency="CNY")
        session.add(account)
        session.flush()
    strategy = session.scalar(
        select(Strategy).where(Strategy.account_id == account.id).order_by(Strategy.created_at).limit(1)
    )
    if strategy is None:
        strategy = Strategy(account_id=account.id, name="DEFAULT")
        session.add(strategy)
        session.flush()
    return account, strategy


def resolve_account(session: Session, account_id: str | None) -> str:
    if account_id:
        account = session.get(Account, account_id)
        if account is None:
            raise EntityNotFoundError("account was not found", details={"account_id": account_id})
        return account.id
    return ensure_default_context(session)[0].id


def resolve_strategy(session: Session, account_id: str, strategy_id: str | None) -> str:
    if strategy_id:
        strategy = session.get(Strategy, strategy_id)
        if strategy is None or strategy.account_id != account_id:
            raise EntityNotFoundError("strategy was not found", details={"strategy_id": strategy_id})
        return strategy.id
    strategy = session.scalar(
        select(Strategy).where(Strategy.account_id == account_id, Strategy.name == "DEFAULT").limit(1)
    )
    if strategy is None:
        strategy = Strategy(account_id=account_id, name="DEFAULT")
        session.add(strategy)
        session.flush()
    return strategy.id


def validate_account_security(session: Session, account_id: str, security_market: str) -> None:
    account = session.get(Account, account_id)
    if account is None:
        raise EntityNotFoundError("account was not found", details={"account_id": account_id})
    try:
        expected = currency_for_market(security_market)
    except ValueError as exc:
        raise ValidationError(str(exc), details={"market": security_market}) from exc
    if account.currency != expected:
        raise AccountCurrencyMismatchError(
            "account currency does not match security market",
            details={"account_currency": account.currency, "market": security_market, "required_currency": expected},
        )

