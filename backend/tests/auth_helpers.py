"""集成测试通用认证辅助函数，避免已有业务测试重复编写登录样板。"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import DEFAULT_DEV_ADMIN_EMAIL
from app.models.auth import User
from app.services.auth_bootstrap import ensure_default_admin_and_backfill


def attach_default_admin_auth(application: FastAPI, session: Session) -> User:
    """确保测试库存在默认管理员、回填存量账户，并将测试应用默认鉴权为该管理员。"""
    admin = ensure_default_admin_and_backfill(session, DEFAULT_DEV_ADMIN_EMAIL)
    session.commit()

    def override_current_user() -> User:
        """在既有业务回归测试中返回默认管理员身份。"""
        return admin

    application.dependency_overrides[get_current_user] = override_current_user
    return admin
