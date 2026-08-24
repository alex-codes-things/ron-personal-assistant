"""Provider-neutral AI failures used by local and cloud clients."""

from __future__ import annotations


class AIError(RuntimeError):
    """Base class for a safe, user-facing inference failure."""


class AIConnectionError(AIError):
    """Raised when the selected inference service cannot be reached."""


class AIProtocolError(AIError):
    """Raised when an inference service returns an invalid response."""


class AIAuthenticationError(AIError):
    """Raised when a cloud credential is missing, invalid, or unauthorized."""


class InferenceCancelled(AIError):
    """Raised when a live turn is intentionally replaced by a new request."""
