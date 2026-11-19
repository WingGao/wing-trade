"""用户认证、邮箱验证码校验、服务端会话与管理员用户管理服务。"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.config import settings as default_settings
from app.domain.enums import UserRole, UserStatus
from app.domain.exceptions import (
    EntityNotFoundError,
    ForbiddenError,
    InvalidEmailError,
    InvalidVerificationCodeError,
    UserAlreadyExistsError,
    ValidationError,
    VerificationCodeAttemptsExceededError,
    VerificationCodeCooldownError,
    VerificationCodeExpiredError,
)
from app.models.auth import EmailVerificationCode, User, UserSession
from app.models.ledger import Account, Strategy
from app.services.auth_bootstrap import normalize_email
from app.services.email import EmailSender, create_email_sender

logger = logging.getLogger("ashare_ledger.auth")

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+$")
_CODE_PATTERN = re.compile(r"^\d{6}$")

CODE_TTL_SECONDS = 300
CODE_COOLDOWN_SECONDS = 60
CODE_MAX_ATTEMPTS = 5


def _as_utc(value: datetime) -> datetime:
    """将数据库可能返回的无时区时间统一归一化为 UTC。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _utcnow() -> datetime:
    """返回当前 UTC 时间戳。"""
    return datetime.now(UTC)


def validate_email(raw_email: str) -> str:
    """校验并规范化邮箱地址，不合法时抛出 InvalidEmailError。"""
    email = normalize_email(raw_email)
    if not email or len(email) > 255 or not _EMAIL_PATTERN.match(email):
        raise InvalidEmailError("请输入有效的邮箱地址", details={"email": raw_email})
    return email


def hash_session_token(raw_token: str) -> str:
    """计算会话令牌的 SHA-256 摘要，避免明文令牌落库。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class AuthService:
    """封装无密码邮箱验证码登录、服务端会话管理与管理员邀请能力。"""

    def __init__(
        self,
        *,
        app_settings: Settings | None = None,
        email_sender: EmailSender | None = None,
    ) -> None:
        """初始化认证服务配置与邮件发送器。"""
        self.settings = app_settings or default_settings
        self.email_sender = email_sender or create_email_sender(self.settings)

    def _hash_code(self, email: str, code: str) -> str:
        """使用服务端密钥对邮箱与验证码计算 HMAC-SHA256 哈希。"""
        payload = f"{email}:{code}".encode()
        return hmac.new(
            self.settings.auth_secret_key.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def ensure_user_default_account(session: Session, user_id: str) -> tuple[Account, Strategy]:
        """确保指定用户至少拥有一个默认 CNY 账户和 DEFAULT 策略。"""
        account = session.scalar(
            select(Account)
            .where(Account.user_id == user_id, Account.currency == "CNY")
            .order_by(Account.created_at)
            .limit(1)
        )
        if account is None:
            account = Account(user_id=user_id, name="默认账户", currency="CNY")
            session.add(account)
            session.flush()
        strategy = session.scalar(
            select(Strategy)
            .where(Strategy.account_id == account.id)
            .order_by(Strategy.created_at)
            .limit(1)
        )
        if strategy is None:
            strategy = Strategy(account_id=account.id, name="DEFAULT")
            session.add(strategy)
            session.flush()
        return account, strategy

    def request_login_code(
        self,
        session: Session,
        raw_email: str,
        *,
        now: datetime | None = None,
    ) -> tuple[str, int, int]:
        """为已受邀或已激活用户生成并发送 6 位登录验证码。"""
        current = _as_utc(now) if now is not None else _utcnow()
        email = validate_email(raw_email)
        user = session.scalar(select(User).where(User.email == email).limit(1))
        if user is None:
            raise ForbiddenError("该邮箱未获邀请，暂不支持自行注册")
        if user.status == UserStatus.DISABLED.value:
            raise ForbiddenError("该账号已被管理员禁用")

        latest = session.scalar(
            select(EmailVerificationCode)
            .where(EmailVerificationCode.email == email)
            .order_by(EmailVerificationCode.created_at.desc(), EmailVerificationCode.id.desc())
            .limit(1)
        )
        if latest is not None:
            elapsed = (current - _as_utc(latest.created_at)).total_seconds()
            if elapsed < CODE_COOLDOWN_SECONDS:
                retry_after = max(1, int(CODE_COOLDOWN_SECONDS - elapsed + 0.999))
                raise VerificationCodeCooldownError(
                    f"发送过于频繁，请在 {retry_after} 秒后重试",
                    details={"retryAfterSeconds": retry_after},
                )

        # 作废该邮箱此前尚未使用的旧验证码，仅保留本次最新验证码有效。
        active_codes = list(
            session.scalars(
                select(EmailVerificationCode).where(
                    EmailVerificationCode.email == email,
                    EmailVerificationCode.consumed_at.is_(None),
                )
            ).all()
        )
        for item in active_codes:
            item.consumed_at = current

        code = f"{secrets.randbelow(1_000_000):06d}"
        record = EmailVerificationCode(
            email=email,
            code_hash=self._hash_code(email, code),
            purpose="LOGIN",
            attempts=0,
            max_attempts=CODE_MAX_ATTEMPTS,
            expires_at=current + timedelta(seconds=CODE_TTL_SECONDS),
            created_at=current,
        )
        session.add(record)
        session.flush()

        self.email_sender.send_login_code(
            email,
            code,
            expires_minutes=CODE_TTL_SECONDS // 60,
        )
        return code, CODE_TTL_SECONDS, CODE_COOLDOWN_SECONDS

    def verify_login_code(
        self,
        session: Session,
        raw_email: str,
        raw_code: str,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
        now: datetime | None = None,
    ) -> tuple[User, str, datetime]:
        """校验邮箱验证码；首登自动激活并创建默认账户，成功后签发服务端会话。"""
        current = _as_utc(now) if now is not None else _utcnow()
        email = validate_email(raw_email)
        code = raw_code.strip()
        if not _CODE_PATTERN.match(code):
            raise InvalidVerificationCodeError("验证码必须为 6 位数字")

        user = session.scalar(select(User).where(User.email == email).limit(1))
        if user is None:
            raise ForbiddenError("该邮箱未获邀请，暂不支持自行注册")
        if user.status == UserStatus.DISABLED.value:
            raise ForbiddenError("该账号已被管理员禁用")

        record = session.scalar(
            select(EmailVerificationCode)
            .where(
                EmailVerificationCode.email == email,
                EmailVerificationCode.consumed_at.is_(None),
            )
            .order_by(EmailVerificationCode.created_at.desc(), EmailVerificationCode.id.desc())
            .limit(1)
        )
        if record is None or _as_utc(record.expires_at) < current:
            raise VerificationCodeExpiredError("验证码已过期或不存在，请重新获取")
        if record.attempts >= record.max_attempts:
            record.consumed_at = current
            session.flush()
            raise VerificationCodeAttemptsExceededError("验证码错误次数过多，请重新获取")

        expected_hash = self._hash_code(email, code)
        if not hmac.compare_digest(record.code_hash, expected_hash):
            record.attempts += 1
            if record.attempts >= record.max_attempts:
                record.consumed_at = current
                session.flush()
                raise VerificationCodeAttemptsExceededError("验证码错误次数过多，请重新获取")
            session.flush()
            remaining = record.max_attempts - record.attempts
            raise InvalidVerificationCodeError(
                f"验证码错误，还可尝试 {remaining} 次",
                details={"remainingAttempts": remaining},
            )

        record.consumed_at = current
        if user.status == UserStatus.INVITED.value:
            user.status = UserStatus.ACTIVE.value
        self.ensure_user_default_account(session, user.id)
        user.last_login_at = current

        raw_token = secrets.token_urlsafe(32)
        expires_at = current + timedelta(days=self.settings.auth_session_days)
        user_session = UserSession(
            user_id=user.id,
            token_hash=hash_session_token(raw_token),
            expires_at=expires_at,
            last_seen_at=current,
            user_agent=(user_agent or "")[:255] or None,
            ip_address=(ip_address or "")[:64] or None,
            created_at=current,
        )
        session.add(user_session)
        session.flush()
        return user, raw_token, expires_at

    def authenticate_session(
        self,
        session: Session,
        raw_token: str | None,
        *,
        now: datetime | None = None,
    ) -> User | None:
        """根据 Cookie 中的会话令牌解析当前有效活跃用户。"""
        if not raw_token:
            return None
        current = _as_utc(now) if now is not None else _utcnow()
        token_hash = hash_session_token(raw_token)
        record = session.scalar(
            select(UserSession).where(UserSession.token_hash == token_hash).limit(1)
        )
        if record is None or record.revoked_at is not None:
            return None
        if _as_utc(record.expires_at) <= current:
            return None
        user = session.get(User, record.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            return None
        return user

    def revoke_session(
        self,
        session: Session,
        raw_token: str | None,
        *,
        now: datetime | None = None,
    ) -> None:
        """吊销指定令牌对应的服务端会话（幂等）。"""
        if not raw_token:
            return
        current = _as_utc(now) if now is not None else _utcnow()
        token_hash = hash_session_token(raw_token)
        record = session.scalar(
            select(UserSession).where(UserSession.token_hash == token_hash).limit(1)
        )
        if record is not None and record.revoked_at is None:
            record.revoked_at = current
            session.flush()

    def revoke_all_user_sessions(
        self,
        session: Session,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> int:
        """立即吊销目标用户的全部活跃会话。"""
        current = _as_utc(now) if now is not None else _utcnow()
        records = list(
            session.scalars(
                select(UserSession).where(
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                )
            ).all()
        )
        for record in records:
            record.revoked_at = current
        if records:
            session.flush()
        return len(records)

    @staticmethod
    def list_users(session: Session) -> list[User]:
        """按创建时间倒序返回系统中的全部用户与待激活邀请记录。"""
        return list(
            session.scalars(select(User).order_by(User.created_at.desc(), User.id.desc())).all()
        )

    def invite_user(
        self,
        session: Session,
        inviter: User,
        raw_email: str,
        *,
        display_name: str | None = None,
    ) -> tuple[User, bool]:
        """管理员按邮箱定向邀请普通用户，并尝试发送邀请提示邮件。"""
        email = validate_email(raw_email)
        existing = session.scalar(select(User).where(User.email == email).limit(1))
        if existing is not None:
            raise UserAlreadyExistsError("该邮箱已存在或已被邀请", details={"email": email})

        cleaned_name = display_name.strip() if display_name else None
        user = User(
            email=email,
            display_name=cleaned_name or None,
            role=UserRole.USER.value,
            status=UserStatus.INVITED.value,
            is_default_admin=False,
            invited_by_user_id=inviter.id,
        )
        session.add(user)
        session.flush()

        invite_email_sent = True
        try:
            self.email_sender.send_invite_notice(
                email,
                inviter_email=inviter.email,
                display_name=user.display_name,
            )
        except Exception:
            invite_email_sent = False
            logger.warning(
                "failed to deliver invite email; invite record preserved",
                extra={"email": email, "inviter_email": inviter.email},
                exc_info=True,
            )
        return user, invite_email_sent

    @staticmethod
    def revoke_invite(session: Session, target_user_id: str) -> None:
        """撤销尚未首次登录激活的邀请记录。"""
        target = session.get(User, target_user_id)
        if target is None:
            raise EntityNotFoundError("未找到目标用户", details={"user_id": target_user_id})
        if target.is_default_admin:
            raise ValidationError("默认管理员不可撤销")
        if target.status != UserStatus.INVITED.value:
            raise ValidationError("仅待激活状态的邀请可以撤销，已激活用户请使用禁用功能")
        session.delete(target)
        session.flush()

    def update_user_status(
        self,
        session: Session,
        actor_admin: User,
        target_user_id: str,
        new_status: str,
        *,
        now: datetime | None = None,
    ) -> User:
        """管理员禁用或重新启用普通用户，禁用时即时吊销该用户所有活跃会话。"""
        if new_status not in {UserStatus.ACTIVE.value, UserStatus.DISABLED.value}:
            raise ValidationError("仅支持切换为 ACTIVE 或 DISABLED 状态")
        target = session.get(User, target_user_id)
        if target is None:
            raise EntityNotFoundError("未找到目标用户", details={"user_id": target_user_id})
        if target.id == actor_admin.id:
            raise ValidationError("不能修改当前登录管理员自身的状态")
        if target.is_default_admin:
            raise ValidationError("不能禁用默认管理员")
        if target.status == UserStatus.INVITED.value and new_status == UserStatus.ACTIVE.value:
            raise ValidationError("待激活用户需通过邮箱验证码首次登录激活")

        target.status = new_status
        if new_status == UserStatus.DISABLED.value:
            self.revoke_all_user_sessions(session, target.id, now=now)
        session.flush()
        return target
