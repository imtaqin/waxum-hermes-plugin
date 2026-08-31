"""Error hierarchy for the waxum client. Callers can catch narrowly."""


class WaxumError(Exception):
    """Base class for every error this plugin raises."""


class WaxumConfigError(WaxumError):
    """Missing or invalid WAXUM_* configuration."""


class WaxumAuthError(WaxumError):
    """waxum rejected the bearer token (401)."""


class WaxumSessionUnavailable(WaxumError):
    """The waxum session exists but has no live client (503) — not connected."""


class WaxumRequestError(WaxumError):
    """Any other non-2xx response or transport failure."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status
