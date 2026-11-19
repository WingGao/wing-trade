"""认证配置校验与 AuthService 核心安全规则单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.session import create_engine_for_url
from app.domain.enums import UserRole, UserStatus
from app.domain.exceptions import (
    ForbiddenError,
    InvalidVerificationCodeError,
    VerificationCodeAttemptsExceededError,
    VerificationCodeCooldownError,
    VerificationCodeExpiredError,
)
from app.models import Account, Base, EmailVerificationCode, User
from app.services.auth import AuthService


class FakeEmailSender:
    """测试用内存发信器，记录每次发送的验证码与邀请通知。"""

    def __init__(self) -> None:
        """初始化发送记录列表。"""
        self.codes: list[tuple[str, str, int]] = []
        self.invites: list[tuple[str, str, str | None]] = []

    def send_login_code(self, to_email: str, code: str, *, expires_minutes: int) -> None:
        """记录发送的验证码。"""
        self.codes.append((to_email, code, expires_minutes))

    def send_invite_notice(
        self,
        to_email: str,
        *,
        inviter_email: str,
        display_name: str | None = None,
    ) -> None:
        """记录发送的邀请通知。"""
        self.invites.append((to_email, inviter_email, display_name))


def _make_session() -> Session:
    """创建隔离的内存 SQLite 会话用于单元测试。"""
    engine = create_engine_for_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    return factory()


def test_settings_auth_and_email_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 console/smtp 模式下的默认管理员邮箱、会话天数与 SMTP 校验规则。"""
    for key in (
        "DEFAULT_ADMIN_EMAIL",
        "EMAIL_PROVIDER",
        "AUTH_SESSION_DAYS",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_FROM",
        "SMTP_USE_TLS",
        "SMTP_USE_SSL",
    ):
        monkeypatch.delenv(key, raising=False)

    dev_settings = Settings.from_env()
    assert dev_settings.email_provider == "console"
    assert dev_settings.default_admin_email == "admin@localhost"
    assert dev_settings.auth_session_days == 30

    monkeypatch.setenv("EMAIL_PROVIDER", "smtp")
    with pytest.raises(ValueError, match="DEFAULT_ADMIN_EMAIL"):
        Settings.from_env()

    monkeypatch.setenv("DEFAULT_ADMIN_EMAIL", "boss@example.com")
    with pytest.raises(ValueError, match="SMTP_HOST"):
        Settings.from_env()

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "noreply@example.com")
    smtp_settings = Settings.from_env()
    assert smtp_settings.email_provider == "smtp"
    assert smtp_settings.default_admin_email == "boss@example.com"
    assert smtp_settings.smtp_use_tls is True
    assert smtp_settings.smtp_use_ssl is False

    # 465 端口未显式指定加密方式时自动启用 SSL（SMTPS）。
    monkeypatch.setenv("SMTP_PORT", "465")
    ssl_settings = Settings.from_env()
    assert ssl_settings.smtp_use_ssl is True
    assert ssl_settings.smtp_use_tls is False

    monkeypatch.setenv("SMTP_USE_TLS", "true")
    monkeypatch.setenv("SMTP_USE_SSL", "true")
    with pytest.raises(ValueError, match="SMTP_USE_TLS and SMTP_USE_SSL"):
        Settings.from_env()

    monkeypatch.setenv("SMTP_USE_SSL", "false")
    monkeypatch.setenv("AUTH_SESSION_DAYS", "0")
    with pytest.raises(ValueError, match="AUTH_SESSION_DAYS"):
        Settings.from_env()


def test_auth_service_code_lifecycle_and_security_rules() -> None:
    """验证未受邀拦截、60 秒冷却、5 分钟过期、5 次锁定、单次消费与 30 天会话。"""
    session = _make_session()
    sender = FakeEmailSender()
    service = AuthService(
        app_settings=Settings(auth_session_days=30, auth_secret_key="test-secret"),
        email_sender=sender,
    )
    t0 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)

    with pytest.raises(ForbiddenError):
        service.request_login_code(session, "stranger@example.com", now=t0)

    invited = User(
        email="member@example.com",
        display_name="成员A",
        role=UserRole.USER.value,
        status=UserStatus.INVITED.value,
    )
    session.add(invited)
    session.flush()

    code, ttl, cooldown = service.request_login_code(session, "MEMBER@example.com", now=t0)
    assert len(code) == 6 and code.isdigit()
    assert ttl == 300
    assert cooldown == 60
    assert sender.codes[-1] == ("member@example.com", code, 5)

    # 数据库仅存哈希，不存明文验证码。
    stored = session.query(EmailVerificationCode).one()
    assert stored.code_hash != code

    # 60 秒冷却期拦截。
    with pytest.raises(VerificationCodeCooldownError) as cooldown_err:
        service.request_login_code(session, "member@example.com", now=t0 + timedelta(seconds=20))
    assert cooldown_err.value.details["retryAfterSeconds"] == 40

    # 连续输错 5 次立即作废。
    wrong_code = "000000" if code != "000000" else "999999"
    for attempt in range(4):
        with pytest.raises(InvalidVerificationCodeError) as err:
            service.verify_login_code(
                session, "member@example.com", wrong_code, now=t0 + timedelta(seconds=30)
            )
        assert err.value.details["remainingAttempts"] == 4 - attempt
    with pytest.raises(VerificationCodeAttemptsExceededError):
        service.verify_login_code(
            session, "member@example.com", wrong_code, now=t0 + timedelta(seconds=31)
        )

    # 冷却结束后重发，测试过期拦截。
    t1 = t0 + timedelta(seconds=61)
    code2, _, _ = service.request_login_code(session, "member@example.com", now=t1)
    with pytest.raises(VerificationCodeExpiredError):
        service.verify_login_code(
            session, "member@example.com", code2, now=t1 + timedelta(seconds=301)
        )

    # 再次重发并成功首次登录：自动激活 + 创建默认账户 + 签发 30 天会话。
    t2 = t1 + timedelta(seconds=61)
    code3, _, _ = service.request_login_code(session, "member@example.com", now=t2)
    user, raw_token, expires_at = service.verify_login_code(
        session, "member@example.com", code3, now=t2 + timedelta(seconds=5)
    )
    assert user.status == UserStatus.ACTIVE.value
    assert (expires_at - (t2 + timedelta(seconds=5))) == timedelta(days=30)
    assert session.query(Account).filter(Account.user_id == user.id).count() == 1

    # 已消费验证码不可复用。
    with pytest.raises(VerificationCodeExpiredError):
        service.verify_login_code(
            session, "member@example.com", code3, now=t2 + timedelta(seconds=10)
        )

    # 30 天内会话有效，过期后失效。
    assert service.authenticate_session(session, raw_token, now=t2 + timedelta(days=29)) is not None
    assert service.authenticate_session(session, raw_token, now=t2 + timedelta(days=31)) is None
    session.close()
