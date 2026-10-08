"""Domain exceptions. The UI turns these into friendly message boxes."""


class POSError(Exception):
    """Base class for expected, user-facing errors."""


class ValidationError(POSError):
    """Input is missing or malformed."""


class NotFoundError(POSError):
    """A referenced record does not exist."""


class ConflictError(POSError):
    """The change conflicts with existing data (e.g. duplicate barcode)."""


class InsufficientStockError(POSError):
    """Not enough stock to complete the operation."""


class PermissionDenied(POSError):
    """The current role may not perform this action."""


class AuthenticationError(POSError):
    """Login failed or the account is locked."""
