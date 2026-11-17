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
