"""API 依赖注入：数据库会话、认证鉴权与按用户隔离的账务归属校验。"""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.domain.enums import UserRole
from app.domain.exceptions import (
    AccountCurrencyMismatchError,
    EntityNotFoundError,
    ForbiddenError,
    TradeNotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.domain.markets import currency_for_market
from app.models.auth import User
from app.models.ledger import Account, Lot, ProfitTarget, Strategy, Trade
from app.services.auth import AuthService


def get_db() -> Generator[Session, None, None]:
    """提供请求级数据库会话并在请求结束时关闭。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    """返回全局复用的认证服务实例，支持在测试中覆盖依赖。"""
    return AuthService()


def get_current_user(
    request: Request,
    session: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> User:
    """从 HttpOnly Cookie 解析并校验当前登录用户，未登录或失效时返回 401。"""
    raw_token = request.cookies.get(auth_service.settings.auth_cookie_name)
    user = auth_service.authenticate_session(session, raw_token)
    if user is None:
        raise UnauthorizedError("请先登录后再访问")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """校验当前登录用户具备管理员角色，否则返回 403。"""
    if current_user.role != UserRole.ADMIN.value:
        raise ForbiddenError("仅管理员可执行此操作")
    return current_user


def request_actor(current_user: User = Depends(get_current_user)) -> str:
    """返回用于审计日志记录的当前登录用户邮箱。"""
    return current_user.email


def ensure_default_context(session: Session, user_id: str) -> tuple[Account, Strategy]:
    """确保指定用户拥有默认 CNY 账户与 DEFAULT 策略。"""
    return AuthService.ensure_user_default_account(session, user_id)


def resolve_user_account_ids(session: Session, user_id: str) -> list[str]:
    """返回归属于指定用户的全部账户 ID 列表。"""
    return list(
        session.scalars(select(Account.id).where(Account.user_id == user_id)).all()
    )


def resolve_account(session: Session, account_id: str | None, *, user_id: str) -> str:
    """校验并返回当前用户可用的账户 ID；访问他人账户统一返回 404。"""
    if account_id:
        account = session.get(Account, account_id)
        if account is None or account.user_id != user_id:
            raise EntityNotFoundError("account was not found", details={"account_id": account_id})
        return account.id
    return ensure_default_context(session, user_id)[0].id


def resolve_user_trade(session: Session, trade_id: str, *, user_id: str) -> Trade:
    """校验并返回归属当前用户的交易记录，越权访问返回 404。"""
    trade = session.get(Trade, trade_id)
    if trade is None:
        raise TradeNotFoundError("trade was not found", details={"trade_id": trade_id})
    account = session.get(Account, trade.account_id)
    if account is None or account.user_id != user_id:
        raise TradeNotFoundError("trade was not found", details={"trade_id": trade_id})
    return trade


def resolve_user_lot(session: Session, lot_id: str, *, user_id: str) -> Lot:
    """校验并返回归属当前用户的持仓批次，越权访问返回 404。"""
    lot = session.get(Lot, lot_id)
    if lot is None:
        raise EntityNotFoundError("lot was not found", details={"lot_id": lot_id})
    account = session.get(Account, lot.account_id)
    if account is None or account.user_id != user_id:
        raise EntityNotFoundError("lot was not found", details={"lot_id": lot_id})
    return lot


def resolve_user_target(session: Session, target_id: str, *, user_id: str) -> ProfitTarget:
    """校验并返回归属当前用户的显式止盈目标，越权访问返回 404。"""
    target = session.get(ProfitTarget, target_id)
    if target is None or target.lot is None:
        raise EntityNotFoundError("target was not found", details={"target_id": target_id})
    account = session.get(Account, target.lot.account_id)
    if account is None or account.user_id != user_id:
        raise EntityNotFoundError("target was not found", details={"target_id": target_id})
    return target


def resolve_strategy(session: Session, account_id: str, strategy_id: str | None) -> str:
    """解析指定账户下的策略 ID，未传时返回或创建 DEFAULT 策略。"""
    if strategy_id:
        strategy = session.get(Strategy, strategy_id)
        if strategy is None or strategy.account_id != account_id:
            raise EntityNotFoundError(
                "strategy was not found", details={"strategy_id": strategy_id}
            )
        return strategy.id
    strategy = session.scalar(
        select(Strategy)
        .where(Strategy.account_id == account_id, Strategy.name == "DEFAULT")
        .limit(1)
    )
    if strategy is None:
        strategy = Strategy(account_id=account_id, name="DEFAULT")
        session.add(strategy)
        session.flush()
    return strategy.id


def validate_account_security(session: Session, account_id: str, security_market: str) -> None:
    """校验账户币种与证券所属市场币种一致。"""
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
            details={
                "account_currency": account.currency,
                "market": security_market,
                "required_currency": expected,
            },
        )
