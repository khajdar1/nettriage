"""Postgres access (spec §6.8): verified TLS to Neon, and no prepared statements (Neon's
transaction-mode pooler can't keep them)."""

from __future__ import annotations

from typing import Any

import certifi
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def engine_url(url: str) -> URL:
    """`postgresql://…` with the psycopg 3 driver."""
    return make_url(url).set(drivername="postgresql+psycopg")


def connect_args(url: URL) -> dict[str, Any]:
    """Remote hosts always use `sslmode=verify-full` against certifi's CA bundle, whatever the URL
    says; only a local test server may skip TLS."""
    args: dict[str, Any] = {"prepare_threshold": None}
    if url.host not in LOCAL_HOSTS:
        args |= {"sslmode": "verify-full", "sslrootcert": certifi.where()}
    return args


def create_database_engine(url: str, *, pool_size: int = 2) -> Engine:
    parsed = engine_url(url)
    return create_engine(
        parsed,
        connect_args=connect_args(parsed),
        pool_size=pool_size,
        max_overflow=0,
        pool_pre_ping=True,
    )
