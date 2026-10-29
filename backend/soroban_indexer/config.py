"""Configuration loaded from environment variables.

Every setting has a default so the service starts with zero configuration
(SQLite + public testnet RPC). ``docs/configuration.md`` and ``.env.example``
document each variable; ``scripts/docs_check.py`` keeps them in sync with the
``ENV_VARS`` table below.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

#: Name -> default (as the string you would put in the environment).
#: An empty string means "unset". This table is the single source of truth
#: used by :func:`load_config` and by the documentation checker.
ENV_VARS: dict[str, str] = {
    "DATABASE_URL": "sqlite:///./soroban_events.db",
    "RPC_URL": "https://soroban-testnet.stellar.org",
    "START_LEDGER": "",
    "CONTRACT_IDS": "",
    "POLL_INTERVAL_SECONDS": "5",
    "PAGE_LIMIT": "100",
    "RPC_TIMEOUT_SECONDS": "30",
    "BACKOFF_INITIAL_SECONDS": "1",
    "BACKOFF_MAX_SECONDS": "60",
    "API_HOST": "0.0.0.0",
    "API_PORT": "8000",
    "CORS_ORIGINS": "*",
    "LOG_LEVEL": "INFO",
}

#: Stellar RPC accepts at most 5 contract IDs per filter and 5 filters per request.
MAX_CONTRACT_IDS = 25
#: Stellar RPC's hard upper bound for ``pagination.limit`` on getEvents.
MAX_PAGE_LIMIT = 10_000

_CONTRACT_ID_RE = re.compile(r"^C[A-Z2-7]{55}$")
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(ValueError):
    """Raised when an environment variable has an invalid value."""


@dataclass(frozen=True)
class Config:
    """Validated runtime configuration for the indexer and the API."""

    database_url: str = ENV_VARS["DATABASE_URL"]
    rpc_url: str = ENV_VARS["RPC_URL"]
    start_ledger: int | None = None
    contract_ids: tuple[str, ...] = field(default_factory=tuple)
    poll_interval_seconds: float = 5.0
    page_limit: int = 100
    rpc_timeout_seconds: float = 30.0
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    api_host: str = ENV_VARS["API_HOST"]
    api_port: int = 8000
    cors_origins: tuple[str, ...] = ("*",)
    log_level: str = "INFO"


def _get(env: Mapping[str, str], name: str) -> str:
    """Return the trimmed value of ``name`` or its documented default."""
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return ENV_VARS[name]
    return raw.strip()


def _parse_int(env: Mapping[str, str], name: str, minimum: int, maximum: int | None = None) -> int:
    """Parse an integer setting and enforce bounds."""
    raw = _get(env, name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" and <= {maximum}" if maximum is not None else ""
        raise ConfigError(f"{name} must be >= {minimum}{upper}, got {value}")
    return value


def _parse_float(env: Mapping[str, str], name: str, minimum: float) -> float:
    """Parse a float setting that must be at least ``minimum``."""
    raw = _get(env, name)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def parse_contract_ids(raw: str) -> tuple[str, ...]:
    """Split a comma-separated list of contract strkeys, validating each one.

    Duplicates are removed while preserving order. Raises :class:`ConfigError`
    if an entry is not a ``C...`` contract address or if there are more than
    :data:`MAX_CONTRACT_IDS` entries.
    """
    ids: list[str] = []
    for part in raw.split(","):
        cid = part.strip()
        if not cid:
            continue
        if not _CONTRACT_ID_RE.match(cid):
            raise ConfigError(
                f"CONTRACT_IDS entry {cid!r} is not a contract address (expected C... strkey, "
                "56 characters)"
            )
        if cid not in ids:
            ids.append(cid)
    if len(ids) > MAX_CONTRACT_IDS:
        raise ConfigError(
            f"CONTRACT_IDS has {len(ids)} entries; Stellar RPC supports at most "
            f"{MAX_CONTRACT_IDS} (5 filters x 5 contract IDs)"
        )
    return tuple(ids)


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a :class:`Config` from ``env`` (defaults to ``os.environ``).

    Raises :class:`ConfigError` with a human-readable message on bad input.
    """
    env = os.environ if env is None else env

    start_raw = _get(env, "START_LEDGER")
    start_ledger = _parse_int(env, "START_LEDGER", minimum=1) if start_raw else None

    log_level = _get(env, "LOG_LEVEL").upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}, got {log_level!r}")

    backoff_initial = _parse_float(env, "BACKOFF_INITIAL_SECONDS", minimum=0.0)
    backoff_max = _parse_float(env, "BACKOFF_MAX_SECONDS", minimum=0.0)
    if backoff_max < backoff_initial:
        raise ConfigError("BACKOFF_MAX_SECONDS must be >= BACKOFF_INITIAL_SECONDS")

    origins = tuple(o.strip() for o in _get(env, "CORS_ORIGINS").split(",") if o.strip())

    return Config(
        database_url=_get(env, "DATABASE_URL"),
        rpc_url=_get(env, "RPC_URL"),
        start_ledger=start_ledger,
        contract_ids=parse_contract_ids(_get(env, "CONTRACT_IDS")),
        poll_interval_seconds=_parse_float(env, "POLL_INTERVAL_SECONDS", minimum=0.0),
        page_limit=_parse_int(env, "PAGE_LIMIT", minimum=1, maximum=MAX_PAGE_LIMIT),
        rpc_timeout_seconds=_parse_float(env, "RPC_TIMEOUT_SECONDS", minimum=0.1),
        backoff_initial_seconds=backoff_initial,
        backoff_max_seconds=backoff_max,
        api_host=_get(env, "API_HOST"),
        api_port=_parse_int(env, "API_PORT", minimum=1, maximum=65535),
        cors_origins=origins or ("*",),
        log_level=log_level,
    )
