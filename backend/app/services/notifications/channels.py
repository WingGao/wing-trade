"""可扩展通知推送通道抽象与企业微信群机器人 Webhook 实现。"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.exceptions import NotificationDeliveryError, ValidationError

logger = logging.getLogger("ashare_ledger.notifications")

WECOM_CHANNEL_TYPE = "wecom_webhook"
_WECOM_ALLOWED_HOST = "qyapi.weixin.qq.com"
_WECOM_ALLOWED_PATH = "/cgi-bin/webhook/send"
_WECOM_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,128}$")
_RETRYABLE_WECOM_ERRCODES = frozenset({-1, 45009})


@dataclass(frozen=True, slots=True)
class HttpPostResponse:
    """抽象 HTTP POST 响应，便于标准库发送与测试替身保持统一契约。"""

    status_code: int
    body: str


HttpPostTransport = Callable[[str, bytes, dict[str, str], float], HttpPostResponse]


class PushChannel(Protocol):
    """通用外部消息推送通道协议，解耦止盈引擎与具体消息渠道。"""

    channel_type: str

    def send_markdown(self, target: str, content: str) -> None:
        """向指定目标发送 Markdown 格式通知，失败时抛出 NotificationDeliveryError。"""


def validate_wecom_webhook_url(raw_url: str) -> str:
    """严格校验企业微信群机器人 Webhook 地址，防止非法地址与内网 SSRF。"""
    cleaned = raw_url.strip()
    if not cleaned:
        raise ValidationError("企业微信 Webhook 地址不能为空")
    if len(cleaned) > 512:
        raise ValidationError("企业微信 Webhook 地址长度超出限制")

    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme.lower() != "https":
        raise ValidationError("企业微信 Webhook 必须使用 https 协议")
    if parsed.username or parsed.password or parsed.port is not None:
        raise ValidationError("企业微信 Webhook 不能包含认证信息或自定义端口")
    if (parsed.hostname or "").lower() != _WECOM_ALLOWED_HOST:
        raise ValidationError("仅允许使用企业微信官方 Webhook 域名（qyapi.weixin.qq.com）")
    if parsed.path != _WECOM_ALLOWED_PATH:
        raise ValidationError("企业微信 Webhook 路径必须为 /cgi-bin/webhook/send")
    if parsed.fragment:
        raise ValidationError("企业微信 Webhook 不能包含片段标识")

    query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    keys = query_params.get("key")
    if not keys or len(keys) != 1 or not _WECOM_KEY_PATTERN.fullmatch(keys[0]):
        raise ValidationError("企业微信 Webhook 缺少合法的 key 参数")

    normalized_query = urllib.parse.urlencode({"key": keys[0]})
    return urllib.parse.urlunsplit(
        ("https", _WECOM_ALLOWED_HOST, _WECOM_ALLOWED_PATH, normalized_query, "")
    )


def mask_wecom_webhook_url(url: str | None) -> str | None:
    """对企业微信 Webhook 中的 key 参数做脱敏处理，确保前端与日志不泄露完整密钥。"""
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    keys = query_params.get("key")
    if not keys or not keys[0]:
        return f"https://{_WECOM_ALLOWED_HOST}{_WECOM_ALLOWED_PATH}?key=****"
    key = keys[0]
    masked_key = f"{key[:4]}****{key[-4:]}" if len(key) > 8 else "****"
    return f"https://{_WECOM_ALLOWED_HOST}{_WECOM_ALLOWED_PATH}?key={masked_key}"


def _urllib_post(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout_seconds: float,
) -> HttpPostResponse:
    """使用 Python 标准库执行 HTTPS POST 请求并返回状态码与响应文本。"""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = getattr(response, "status", response.getcode())
            raw = response.read().decode("utf-8", errors="replace")
            return HttpPostResponse(status_code=int(status_code), body=raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
        return HttpPostResponse(status_code=int(exc.code), body=raw)


class WeComWebhookChannel:
    """企业微信群机器人 Webhook 推送通道实现。"""

    channel_type = WECOM_CHANNEL_TYPE

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        transport: HttpPostTransport | None = None,
    ) -> None:
        """初始化企微通道超时时间及可替换 HTTP 传输函数。"""
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._transport = transport or _urllib_post

    def send_markdown(self, target: str, content: str) -> None:
        """发送企业微信 Markdown 卡片消息并校验返回码，失败时抛出脱敏领域异常。"""
        webhook_url = validate_wecom_webhook_url(target)
        masked_url = mask_wecom_webhook_url(webhook_url)
        payload = json.dumps(
            {
                "msgtype": "markdown",
                "markdown": {"content": content},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}

        try:
            response = self._transport(webhook_url, payload, headers, self.timeout_seconds)
        except Exception as exc:
            logger.warning(
                "wecom webhook network error: target=%s error_type=%s",
                masked_url,
                type(exc).__name__,
            )
            raise NotificationDeliveryError(
                "企业微信推送网络请求失败，请检查服务器网络连通性",
                details={"retryable": True, "errorType": type(exc).__name__},
            ) from exc

        if response.status_code != 200:
            retryable = response.status_code == 429 or response.status_code >= 500
            logger.warning(
                "wecom webhook http error: target=%s status=%d",
                masked_url,
                response.status_code,
            )
            raise NotificationDeliveryError(
                f"企业微信接口返回异常状态码（HTTP {response.status_code}）",
                details={"retryable": retryable, "statusCode": response.status_code},
            )

        parsed_body: dict[str, Any]
        try:
            parsed = json.loads(response.body)
            if not isinstance(parsed, dict):
                raise ValueError("response body is not a JSON object")
            parsed_body = parsed
        except Exception as exc:
            logger.warning("wecom webhook invalid json response: target=%s", masked_url)
            raise NotificationDeliveryError(
                "企业微信接口返回了无法解析的响应",
                details={"retryable": True},
            ) from exc

        errcode = parsed_body.get("errcode")
        errmsg = str(parsed_body.get("errmsg", "")).strip()
        if errcode != 0:
            retryable = errcode in _RETRYABLE_WECOM_ERRCODES
            logger.warning(
                "wecom webhook rejected message: target=%s errcode=%s errmsg=%s",
                masked_url,
                errcode,
                errmsg,
            )
            detail_msg = f"（errcode={errcode}）" if errcode is not None else ""
            if errmsg:
                detail_msg = f"（errcode={errcode}: {errmsg}）"
            raise NotificationDeliveryError(
                f"企业微信推送失败{detail_msg}",
                details={"retryable": retryable, "errcode": errcode, "errmsg": errmsg},
            )

        logger.info("wecom webhook message delivered: target=%s", masked_url)
