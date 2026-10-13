"""The integration fixture must not open a connection to drop the wrong database."""

import pytest
from sqlalchemy.engine.url import make_url
from tests.integration.conftest import require_local_test_database


def _url(host: str, database: str) -> str:
    return f"postgresql+asyncpg://nexus:super-secret@{host}:5432/{database}"


def test_loopback_development_database_is_allowed() -> None:
    configured = make_url(_url("127.0.0.1", "nexus"))
    require_local_test_database(configured, "nexus_test")


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://nexus:super-secret@localhost:5432/nexus",
        "postgresql+asyncpg://nexus:super-secret@[::1]:5432/nexus",
    ],
)
def test_other_loopback_hosts_are_allowed(url: str) -> None:
    require_local_test_database(make_url(url), "nexus_test")


def test_remote_host_is_refused_without_the_password() -> None:
    configured = make_url(_url("db.example.com", "nexus"))
    with pytest.raises(RuntimeError, match="db.example.com") as exc_info:
        require_local_test_database(configured, "nexus_test")
    message = str(exc_info.value)
    assert "super-secret" not in message
    assert "postgresql" not in message


@pytest.mark.parametrize(
    "name",
    ["nexus", "postgres", 'nexus_test"; DROP DATABASE nexus; --'],
)
def test_only_nexus_test_may_be_dropped(name: str) -> None:
    configured = make_url(_url("127.0.0.1", "nexus"))
    with pytest.raises(RuntimeError, match="Refusing to drop database") as exc_info:
        require_local_test_database(configured, name)
    assert "super-secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    "database",
    ["nexus_test", "postgres", "template0", "template1"],
)
def test_protected_configured_database_is_refused(database: str) -> None:
    configured = make_url(_url("127.0.0.1", database))
    with pytest.raises(RuntimeError, match=database) as exc_info:
        require_local_test_database(configured, "nexus_test")
    assert "super-secret" not in str(exc_info.value)
