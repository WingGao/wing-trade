"""启动时默认管理员初始化与存量数据无损回填。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DEFAULT_DEV_ADMIN_EMAIL
from app.domain.enums import UserRole, UserStatus
from app.models.auth import User
from app.models.ledger import Account, ProfitSetting


def normalize_email(email: str) -> str:
    """规范化邮箱地址（去除首尾空白并转小写）。"""
    return email.strip().lower()


def profit_setting_scope_key(user_id: str, security_id: str | None = None) -> str:
    """构造按用户隔离的止盈配置主键。"""
    if security_id:
        return f"USER:{user_id}:SECURITY:{security_id}"
    return f"USER:{user_id}:GLOBAL"


def ensure_default_admin_and_backfill(
    session: Session,
    default_admin_email: str,
) -> User:
    """确保默认管理员存在，并将所有无归属存量账户与止盈设置无损归属该管理员。"""
    target_email = normalize_email(default_admin_email)
    default_admin = session.scalar(
        select(User).where(User.is_default_admin.is_(True)).limit(1)
    )
    by_email = session.scalar(select(User).where(User.email == target_email).limit(1))

    if default_admin is not None:
        # 若此前使用开发占位邮箱初始化过默认管理员，且现已配置真实邮箱，则原地升级邮箱。
        if (
            default_admin.email == DEFAULT_DEV_ADMIN_EMAIL
            and target_email != DEFAULT_DEV_ADMIN_EMAIL
            and by_email is None
        ):
            default_admin.email = target_email
        default_admin.role = UserRole.ADMIN.value
        default_admin.status = UserStatus.ACTIVE.value
        admin = default_admin
    elif by_email is not None:
        by_email.role = UserRole.ADMIN.value
        by_email.status = UserStatus.ACTIVE.value
        by_email.is_default_admin = True
        admin = by_email
    else:
        admin = User(
            email=target_email,
            display_name="默认管理员",
            role=UserRole.ADMIN.value,
            status=UserStatus.ACTIVE.value,
            is_default_admin=True,
        )
        session.add(admin)
        session.flush()

    # 将所有尚未绑定用户的存量账户归属默认管理员。
    unassigned_accounts = list(
        session.scalars(select(Account).where(Account.user_id.is_(None))).all()
    )
    for account in unassigned_accounts:
        account.user_id = admin.id

    # 将所有旧版全局/单股止盈配置无损迁移到默认管理员命名空间下。
    legacy_settings = list(
        session.scalars(select(ProfitSetting).where(ProfitSetting.user_id.is_(None))).all()
    )
    for setting in legacy_settings:
        new_key = profit_setting_scope_key(admin.id, setting.security_id)
        existing_new = session.get(ProfitSetting, new_key)
        if existing_new is not None:
            session.delete(setting)
            continue
        cloned = ProfitSetting(
            scope_key=new_key,
            user_id=admin.id,
            security_id=setting.security_id,
            target_return_pct=setting.target_return_pct,
            calculation_basis=setting.calculation_basis,
            alert_enabled=setting.alert_enabled,
            created_at=setting.created_at,
        )
        session.delete(setting)
        session.flush()
        session.add(cloned)

    session.flush()
    return admin
