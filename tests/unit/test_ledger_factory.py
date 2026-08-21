import pytest

from recap.ledger import (
    InMemoryLedgerRepository,
    PostgresLedgerRepository,
    SQLiteLedgerRepository,
    build_ledger_repository,
)


def test_factory_builds_memory_repository() -> None:
    assert isinstance(build_ledger_repository("memory"), InMemoryLedgerRepository)


@pytest.mark.asyncio
async def test_factory_builds_sqlite_repository() -> None:
    repository = build_ledger_repository(
        "sqlite",
        sqlite_path=":memory:",
    )

    assert isinstance(repository, SQLiteLedgerRepository)
    await repository.close()


def test_factory_builds_lazy_postgres_repository_without_connecting() -> None:
    repository = build_ledger_repository(
        "postgresql",
        postgres_dsn="postgresql://user:password@localhost/recap",
    )

    assert isinstance(repository, PostgresLedgerRepository)
    assert repository._pool is None


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_factory_requires_backend_configuration(backend: str) -> None:
    with pytest.raises(ValueError):
        build_ledger_repository(backend)


def test_factory_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="Unsupported Ledger backend"):
        build_ledger_repository("redis")
