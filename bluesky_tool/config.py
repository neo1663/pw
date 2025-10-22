from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class TargetConfig:
    """Configuration for a target account whose followers should be engaged."""

    handle: str
    follow_limit: Optional[int] = None
    like_latest_post: bool = True
    like_limit: Optional[int] = None


@dataclass
class DMConfig:
    """Configuration options related to direct messages for new followers."""

    enabled: bool = False
    message: str = ""
    limit_per_run: Optional[int] = None
    cooldown_hours: float = 0.0


@dataclass
class AccountConfig:
    """Configuration for a single Bluesky automation account."""

    handle: str
    app_password: str
    service: str = "https://bsky.social"
    proxy: Optional[str] = None
    follow_targets: List[TargetConfig] = field(default_factory=list)
    dm: DMConfig = field(default_factory=DMConfig)
    follow_delay_seconds: Optional[float] = 2.5
    like_delay_seconds: Optional[float] = 2.5
    new_followers_page_size: int = 50


@dataclass
class Config:
    """Root configuration for the automation run."""

    accounts: List[AccountConfig]
    storage_dir: Path = Path("state")
    default_follow_delay_seconds: float = 2.5
    default_like_delay_seconds: float = 2.5


class ConfigurationError(ValueError):
    """Raised when the configuration file cannot be parsed correctly."""


def _build_target(data: Dict[str, Any]) -> TargetConfig:
    if "handle" not in data:
        raise ConfigurationError("Each follow target must define a 'handle'.")

    return TargetConfig(
        handle=data["handle"],
        follow_limit=data.get("follow_limit"),
        like_latest_post=bool(data.get("like_latest_post", True)),
        like_limit=data.get("like_limit"),
    )


def _build_dm(data: Dict[str, Any] | None) -> DMConfig:
    if not data:
        return DMConfig()

    return DMConfig(
        enabled=bool(data.get("enabled", False)),
        message=str(data.get("message", "")),
        limit_per_run=data.get("limit_per_run"),
        cooldown_hours=float(data.get("cooldown_hours", 0.0)),
    )


def _build_account(data: Dict[str, Any]) -> AccountConfig:
    if "handle" not in data:
        raise ConfigurationError("Each account must define a 'handle'.")
    if "app_password" not in data:
        raise ConfigurationError("Each account must define an 'app_password'.")

    follow_targets_raw = data.get("follow_targets", [])
    if not isinstance(follow_targets_raw, list):
        raise ConfigurationError("'follow_targets' must be a list of target definitions.")

    follow_targets = [_build_target(item) for item in follow_targets_raw]
    dm_config = _build_dm(data.get("dm"))

    def _float_or_default(value: Any, default: float) -> float:
        if value is None:
            return default
        return float(value)

    return AccountConfig(
        handle=str(data["handle"]),
        app_password=str(data["app_password"]),
        service=str(data.get("service", "https://bsky.social")),
        proxy=data.get("proxy"),
        follow_targets=follow_targets,
        dm=dm_config,
        follow_delay_seconds=_float_or_default(
            data.get("follow_delay_seconds", data.get("delay_seconds")), 2.5
        ),
        like_delay_seconds=_float_or_default(
            data.get("like_delay_seconds", data.get("delay_seconds")), 2.5
        ),
        new_followers_page_size=int(data.get("new_followers_page_size", 50)),
    )


def load_config(path: Path) -> Config:
    """Load a configuration file from disk."""

    if not path.exists():
        raise ConfigurationError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    accounts_raw = data.get("accounts")
    if not accounts_raw:
        raise ConfigurationError("The configuration file must define at least one account.")
    if not isinstance(accounts_raw, list):
        raise ConfigurationError("'accounts' must be a list.")

    accounts = [_build_account(item) for item in accounts_raw]

    storage_dir = data.get("storage", {}).get("directory") if isinstance(data.get("storage"), dict) else data.get("storage_dir")
    storage_dir_path = Path(storage_dir) if storage_dir else Path("state")

    return Config(
        accounts=accounts,
        storage_dir=storage_dir_path,
        default_follow_delay_seconds=float(data.get("default_follow_delay_seconds", 2.5)),
        default_like_delay_seconds=float(data.get("default_like_delay_seconds", 2.5)),
    )


__all__ = [
    "Config",
    "AccountConfig",
    "DMConfig",
    "TargetConfig",
    "ConfigurationError",
    "load_config",
]
