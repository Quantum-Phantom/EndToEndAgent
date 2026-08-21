"""Configuration-driven Ledger repository selection."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from recap.ledger.postgres import PostgresLedgerRepository
from recap.ledger.service import InMemoryLedgerRepository, LedgerRepository
from recap.ledger.sqlite import SQLiteLedgerRepository

LedgerBackend = Literal["memory", "sqlite", "postgresql"]


def build_ledger_repository(
    backend: str,
    *,
    sqlite_path: str | Path | None = None,
    postgres_dsn: str | None = None,
) -> LedgerRepository:
    normalized = backend.strip().lower()
    if normalized == "memory":
        return InMemoryLedgerRepository()
    if normalized == "sqlite":
        if sqlite_path is None:
            raise ValueError("sqlite_path is required for the SQLite Ledger backend")
        return SQLiteLedgerRepository(sqlite_path)
    if normalized in {"postgres", "postgresql"}:
        if not postgres_dsn:
            raise ValueError("postgres_dsn is required for the PostgreSQL Ledger backend")
        return PostgresLedgerRepository.from_dsn(postgres_dsn)
    raise ValueError(
        f"Unsupported Ledger backend: {backend!r}; expected memory, sqlite or postgresql"
    )
