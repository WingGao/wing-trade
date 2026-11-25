from __future__ import annotations


class DomainError(Exception):
    """Base class for errors safe to expose at an application boundary."""

    code = "DOMAIN_ERROR"

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(DomainError):
    code = "VALIDATION_ERROR"


class InvalidQuantityError(ValidationError):
    code = "INVALID_QUANTITY"


class InvalidPriceError(ValidationError):
    code = "INVALID_PRICE"


class NegativeFeeError(ValidationError):
    code = "NEGATIVE_FEE"


class InsufficientLotQtyError(DomainError):
    code = "INSUFFICIENT_LOT_QTY"


class MatchQuantityMismatchError(DomainError):
    code = "MATCH_QUANTITY_MISMATCH"


class LotOwnershipError(DomainError):
    code = "LOT_OWNERSHIP_MISMATCH"


class FutureLotError(DomainError):
    code = "FUTURE_LOT"


class DuplicateTradeError(DomainError):
    code = "DUPLICATE_TRADE"


class LotHasActiveMatchesError(DomainError):
    code = "LOT_HAS_ACTIVE_MATCHES"


class TradeNotFoundError(DomainError):
    code = "TRADE_NOT_FOUND"


class TradeRevisionConflictError(DomainError):
    """拒绝覆盖用户打开表单后已被修改的交易。"""

    code = "TRADE_REVISION_CONFLICT"


class TradeNotEditableError(DomainError):
    """已撤销的原始成交不允许继续编辑。"""

    code = "TRADE_NOT_EDITABLE"


class EntityNotFoundError(DomainError):
    code = "NOT_FOUND"


class UnsupportedOperationError(DomainError):
    code = "UNSUPPORTED_OPERATION"


class AccountCurrencyMismatchError(DomainError):
    code = "ACCOUNT_CURRENCY_MISMATCH"


class UnauthorizedError(DomainError):
    """未登录或会话已过期、已吊销。"""

    code = "UNAUTHORIZED"


class ForbiddenError(DomainError):
    """当前账号无权执行该操作或未获登录邀请。"""

    code = "FORBIDDEN"


class UserAlreadyExistsError(DomainError):
    """目标邮箱已存在，不能重复邀请。"""

    code = "USER_ALREADY_EXISTS"


class InvalidEmailError(ValidationError):
    """邮箱地址格式不合法。"""

    code = "INVALID_EMAIL"


class InvalidVerificationCodeError(ValidationError):
    """邮箱验证码错误。"""

    code = "INVALID_VERIFICATION_CODE"


class VerificationCodeExpiredError(ValidationError):
    """邮箱验证码已过期或尚未申请。"""

    code = "VERIFICATION_CODE_EXPIRED"


class VerificationCodeAttemptsExceededError(ValidationError):
    """验证码错误次数超限，已失效。"""

    code = "VERIFICATION_CODE_ATTEMPTS_EXCEEDED"


class VerificationCodeCooldownError(DomainError):
    """同一邮箱请求验证码过于频繁，处于冷却期。"""

    code = "VERIFICATION_CODE_COOLDOWN"


class NotificationDeliveryError(DomainError):
    """外部通知渠道发送失败（例如网络异常或企微接口返回非零错误码）。"""

    code = "NOTIFICATION_DELIVERY_FAILED"

