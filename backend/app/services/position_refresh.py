"""持久化范围待刷新状态；读取不创建记录，写入共享调用方事务。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import PositionRefreshState, Security, utcnow


class PositionRefreshService:
    """统一管理账户、证券、策略三元范围，避免各读取端状态漂移。"""

    @staticmethod
    def get(
        session: Session, *, account_id: str, security_id: str, strategy_id: str
    ) -> PositionRefreshState | None:
        """只读查找范围记录；旧账本没有记录时不隐式写入。"""
        return session.scalar(
            select(PositionRefreshState).where(
                PositionRefreshState.account_id == account_id,
                PositionRefreshState.security_id == security_id,
                PositionRefreshState.strategy_id == strategy_id,
            )
        )

    @classmethod
    def needs_refresh(
        cls, session: Session, *, account_id: str, security_id: str, strategy_id: str
    ) -> bool:
        """缺失状态行按无需刷新读取，绝不在 GET 路径创建记录。"""
        state = cls.get(
            session, account_id=account_id, security_id=security_id, strategy_id=strategy_id
        )
        return state is not None and state.needs_refresh

    @classmethod
    def mark(cls, session: Session, *, account_id: str, security_id: str, strategy_id: str) -> None:
        """与交易编辑同事务创建或置真范围状态，不自行提交。"""
        state = cls.get(
            session, account_id=account_id, security_id=security_id, strategy_id=strategy_id
        )
        if state is None:
            state = PositionRefreshState(
                account_id=account_id,
                security_id=security_id,
                strategy_id=strategy_id,
                needs_refresh=True,
            )
            session.add(state)
        else:
            state.needs_refresh = True
            state.updated_at = utcnow()

    @classmethod
    def clear(
        cls, session: Session, *, account_id: str, security_id: str, strategy_id: str
    ) -> None:
        """仅在重放成功后清除目标范围，不为旧的无标记范围创建行。"""
        state = cls.get(
            session, account_id=account_id, security_id=security_id, strategy_id=strategy_id
        )
        if state is not None and state.needs_refresh:
            state.needs_refresh = False
            state.updated_at = utcnow()

    @staticmethod
    def pending_scopes(session: Session, account_id: str) -> list[dict[str, str | None]]:
        """返回账户全部待刷新范围，包含没有活动批次的证券和策略。"""
        rows = session.execute(
            select(PositionRefreshState, Security)
            .join(Security, Security.id == PositionRefreshState.security_id)
            .where(
                PositionRefreshState.account_id == account_id,
                PositionRefreshState.needs_refresh.is_(True),
            )
            .order_by(Security.market, Security.symbol, PositionRefreshState.strategy_id)
        )
        return [
            {
                "accountId": state.account_id,
                "securityId": state.security_id,
                "strategyId": state.strategy_id,
                "market": security.market,
                "symbol": security.symbol,
                "name": security.name,
            }
            for state, security in rows
        ]
