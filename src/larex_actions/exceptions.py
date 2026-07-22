class LarexActionError(Exception):
    """Base exception raised by the LAREX Action SDK."""


class DispatchVerificationError(LarexActionError):
    """Raised when an incoming dispatch request cannot be trusted."""

    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


class ActionCancelled(LarexActionError):
    """Raised when LAREX asks the processor to stop cooperatively."""


class ActionUrlSecurityError(LarexActionError):
    """Raised when a LAREX callback or download URL violates the SDK URL policy."""


class IncrementalResultsUnsupported(LarexActionError):
    """Raised when a processor requests incremental results from an older LAREX server."""


class CustomFileResultsUnsupported(LarexActionError):
    """Raised when a processor returns custom files to an older LAREX server."""
