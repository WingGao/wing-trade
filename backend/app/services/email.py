"""邮件发送抽象与控制台/SMTP 实现。"""

from __future__ import annotations

import logging
import smtplib
import time
from email.message import EmailMessage
from typing import Protocol

from app.config import Settings

logger = logging.getLogger("ashare_ledger.email")


class EmailSender(Protocol):
    """邮件发送器协议，隔离具体发信通道。"""

    def send_login_code(self, to_email: str, code: str, *, expires_minutes: int) -> None:
        """发送登录验证码邮件。"""

    def send_invite_notice(
        self,
        to_email: str,
        *,
        inviter_email: str,
        display_name: str | None = None,
    ) -> None:
        """发送管理员定向邀请通知邮件。"""


class ConsoleEmailSender:
    """本地开发与测试使用的日志发信器，仅输出验证码和通知日志。"""

    def send_login_code(self, to_email: str, code: str, *, expires_minutes: int) -> None:
        """将登录验证码记录到后端日志，便于本地调试与自动化测试。"""
        logger.info(
            "console email login code: to=%s code=%s expires_minutes=%d",
            to_email,
            code,
            expires_minutes,
            extra={
                "to_email": to_email,
                "code": code,
                "expires_minutes": expires_minutes,
            },
        )

    def send_invite_notice(
        self,
        to_email: str,
        *,
        inviter_email: str,
        display_name: str | None = None,
    ) -> None:
        """将邀请通知记录到后端日志。"""
        logger.info(
            "console email invite notice: to=%s inviter=%s display_name=%s",
            to_email,
            inviter_email,
            display_name or "",
            extra={
                "to_email": to_email,
                "inviter_email": inviter_email,
                "display_name": display_name,
            },
        )


class SmtpEmailSender:
    """基于 Python 标准库 smtplib 的生产 SMTP 邮件发送器。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender_address: str,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        use_ssl: bool = False,
        timeout: int = 10,
    ) -> None:
        """初始化 SMTP 连接参数。"""
        self.host = host
        self.port = port
        self.sender_address = sender_address
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.use_ssl = use_ssl
        self.timeout = timeout

    def _mode_label(self) -> str:
        """返回当前 SMTP 传输加密模式说明。"""
        if self.use_ssl:
            return "SSL"
        if self.use_tls:
            return "STARTTLS"
        return "PLAIN"

    def _deliver(self, message: EmailMessage, *, mail_type: str) -> None:
        """通过 SMTP 或 SMTPS 连接投递邮件消息，并记录完整调试与耗时日志（不记录密码与验证码）。"""
        to_email = str(message["To"])
        subject = str(message["Subject"])
        mode = self._mode_label()
        started = time.perf_counter()

        logger.debug(
            "smtp deliver start: type=%s host=%s port=%d mode=%s from=%s to=%s auth=%s timeout=%ds",
            mail_type,
            self.host,
            self.port,
            mode,
            self.sender_address,
            to_email,
            bool(self.username),
            self.timeout,
            extra={
                "mail_type": mail_type,
                "smtp_host": self.host,
                "smtp_port": self.port,
                "smtp_mode": mode,
                "from_email": self.sender_address,
                "to_email": to_email,
                "subject": subject,
                "smtp_auth": bool(self.username),
            },
        )

        try:
            server: smtplib.SMTP
            if self.use_ssl:
                logger.debug("smtp opening SSL connection: %s:%d", self.host, self.port)
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                logger.debug("smtp opening connection: %s:%d", self.host, self.port)
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            try:
                server.ehlo()
                if not self.use_ssl and self.use_tls:
                    logger.debug("smtp upgrading connection with STARTTLS")
                    server.starttls()
                    server.ehlo()
                if self.username:
                    logger.debug("smtp authenticating user=%s", self.username)
                    server.login(self.username, self.password)
                logger.debug("smtp sending message: type=%s to=%s", mail_type, to_email)
                server.send_message(message)
            finally:
                try:
                    server.quit()
                except smtplib.SMTPException:
                    # 部分国内邮箱（如 QQ/163）在 QUIT 阶段会提前断开连接，不影响已送达邮件。
                    logger.debug(
                        "smtp quit returned non-standard response; ignoring after delivery"
                    )
                    server.close()
        except Exception:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "smtp deliver failed: type=%s host=%s port=%d mode=%s to=%s duration_ms=%.2f",
                mail_type,
                self.host,
                self.port,
                mode,
                to_email,
                elapsed_ms,
                extra={
                    "mail_type": mail_type,
                    "smtp_host": self.host,
                    "smtp_port": self.port,
                    "smtp_mode": mode,
                    "to_email": to_email,
                    "duration_ms": elapsed_ms,
                },
            )
            raise

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "smtp deliver succeeded: type=%s to=%s host=%s:%d mode=%s duration_ms=%.2f",
            mail_type,
            to_email,
            self.host,
            self.port,
            mode,
            elapsed_ms,
            extra={
                "mail_type": mail_type,
                "smtp_host": self.host,
                "smtp_port": self.port,
                "smtp_mode": mode,
                "to_email": to_email,
                "duration_ms": elapsed_ms,
            },
        )

    def send_login_code(self, to_email: str, code: str, *, expires_minutes: int) -> None:
        """构造并发送中文登录验证码邮件。"""
        message = EmailMessage()
        message["Subject"] = "【交易账本】您的登录验证码"
        message["From"] = self.sender_address
        message["To"] = to_email
        message.set_content(
            f"您好，\n\n"
            f"您的登录验证码为：{code}\n"
            f"验证码将在 {expires_minutes} 分钟内有效，请勿向他人泄露。\n\n"
            f"如非本人操作，请忽略此邮件。\n"
        )
        self._deliver(message, mail_type="login_code")

    def send_invite_notice(
        self,
        to_email: str,
        *,
        inviter_email: str,
        display_name: str | None = None,
    ) -> None:
        """构造并发送管理员邀请激活提示邮件。"""
        greeting = f"{display_name}，您好：" if display_name else "您好："
        message = EmailMessage()
        message["Subject"] = "【交易账本】您已获邀使用系统"
        message["From"] = self.sender_address
        message["To"] = to_email
        message.set_content(
            f"{greeting}\n\n"
            f"管理员（{inviter_email}）已邀请您使用交易账本。\n"
            f"请直接前往系统登录页面，输入本邮箱（{to_email}）获取验证码即可完成首次激活并登录。\n"
        )
        self._deliver(message, mail_type="invite_notice")


def create_email_sender(app_settings: Settings) -> EmailSender:
    """根据应用配置创建对应的邮件发送器实例。"""
    if app_settings.email_provider == "smtp":
        logger.debug(
            "initializing smtp email sender: host=%s port=%d from=%s tls=%s ssl=%s auth=%s",
            app_settings.smtp_host,
            app_settings.smtp_port,
            app_settings.smtp_from,
            app_settings.smtp_use_tls,
            app_settings.smtp_use_ssl,
            bool(app_settings.smtp_user),
        )
        return SmtpEmailSender(
            host=app_settings.smtp_host,
            port=app_settings.smtp_port,
            sender_address=app_settings.smtp_from,
            username=app_settings.smtp_user,
            password=app_settings.smtp_password,
            use_tls=app_settings.smtp_use_tls,
            use_ssl=app_settings.smtp_use_ssl,
        )
    logger.debug("initializing console email sender")
    return ConsoleEmailSender()
