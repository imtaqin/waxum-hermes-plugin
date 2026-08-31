"""Typed, validated configuration for the waxum platform adapter."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .exceptions import WaxumConfigError


@dataclass(frozen=True)
class WaxumConfig:
    base_url: str
    token: str
    session_id: str
    connect_timeout: float = 15.0
    stream_timeout: float = 300.0
    max_retries: int = 5
    backoff_base: float = 1.0
    backoff_max: float = 30.0

    @classmethod
    def from_platform_config(cls, cfg) -> "WaxumConfig":
        """Builds config from Hermes's PlatformConfig, falling back to env vars.

        Hermes passes a ``gateway.config.PlatformConfig`` dataclass whose
        custom keys live in ``cfg.extra`` — it has no ``.get()``. Support
        dict, PlatformConfig (``.extra``), and env-var fallbacks.
        """
        if hasattr(cfg, "get"):
            extra = cfg
        elif getattr(cfg, "extra", None):
            extra = cfg.extra
        else:
            extra = {}
        get = extra.get if hasattr(extra, "get") else lambda k, d=None: d

        base_url = (get("base_url") or os.environ.get("WAXUM_BASE_URL") or "http://127.0.0.1:3451").rstrip("/")
        token = get("token") or os.environ.get("WAXUM_TOKEN")
        session_id = get("session_id") or os.environ.get("WAXUM_SESSION_ID")

        missing = [name for name, val in (("WAXUM_TOKEN", token), ("WAXUM_SESSION_ID", session_id)) if not val]
        if missing:
            raise WaxumConfigError(f"waxum platform: missing required config: {', '.join(missing)}")

        return cls(base_url=base_url, token=token, session_id=session_id)

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}


def check_requirements(cfg=None) -> bool:
    """Cheap presence check used by Hermes before it even builds the adapter.

    ``cfg`` is optional: the gateway calls ``check_fn()`` with no args at
    registration time (env-only probe), and only passes a PlatformConfig
    later from validate_config().
    """
    if hasattr(cfg, "get"):
        extra = cfg
    elif getattr(cfg, "extra", None):
        extra = cfg.extra
    else:
        extra = {}
    get = extra.get if hasattr(extra, "get") else lambda k, d=None: d
    return bool(get("token") or os.environ.get("WAXUM_TOKEN")) and bool(
        get("session_id") or os.environ.get("WAXUM_SESSION_ID")
    )


def validate_config(cfg) -> bool:
    """Hermes platform_registry contract: return True when config is valid.

    (The registry does ``if not entry.validate_config(config): fail``, so a
    ``None`` return is treated as invalid — must be a real bool.)
    """
    try:
        WaxumConfig.from_platform_config(cfg)
        return True
    except WaxumConfigError:
        return False
