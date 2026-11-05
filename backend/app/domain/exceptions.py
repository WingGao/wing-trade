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


class UnsupportedOperationError(DomainError):
    code = "UNSUPPORTED_OPERATION"
