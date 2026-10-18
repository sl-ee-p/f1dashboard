"""Runtime configuration, read from environment variables."""

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    # A random key per process is fine: the app keeps no sessions or logins.
    secret_key: str = field(
        default_factory=lambda: os.environ.get("F1DASH_SECRET_KEY") or secrets.token_hex(32)
    )
    cache_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("F1DASH_CACHE_DIR", "cache")).resolve()
    )
    openf1_url: str = field(
        default_factory=lambda: os.environ.get("F1DASH_OPENF1_URL", "https://api.openf1.org/v1")
    )
    jolpica_url: str = field(
        default_factory=lambda: os.environ.get(
            "F1DASH_JOLPICA_URL", "https://api.jolpi.ca/ergast/f1"
        )
    )
    multiviewer_url: str = field(
        default_factory=lambda: os.environ.get(
            "F1DASH_MULTIVIEWER_URL", "https://api.multiviewer.app/api/v1"
        )
    )
    # Minimum seconds between requests to the same upstream host.
    request_interval: float = field(
        default_factory=lambda: float(os.environ.get("F1DASH_REQUEST_INTERVAL", "0.5"))
    )
    live_enabled: bool = field(default_factory=lambda: _bool("F1DASH_LIVE_ENABLED", True))
    max_live_streams: int = field(
        default_factory=lambda: int(os.environ.get("F1DASH_MAX_LIVE_STREAMS", "4"))
    )
    offline: bool = field(default_factory=lambda: _bool("F1DASH_OFFLINE", False))
