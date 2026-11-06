"""Removing a run from history while this process still streams it."""

from uuid import UUID, uuid4

from tests.conftest import PRINCIPAL, client_for, make_settings, signed_in

from nexus.api.limits import UsageLimiter
from nexus.api.v1.runs import get_history
from nexus.main import create_app


class FakeHistory:
    """Owns the runs in ``owned`` and records every delete call."""

    def __init__(self, owned: set[UUID]) -> None:
        self.owned = owned
        self.deleted: list[UUID] = []

    async def get_run(self, run_id: UUID, *, owner_id: UUID) -> object | None:
        assert owner_id == PRINCIPAL.id
        return object() if run_id in self.owned else None

    async def delete_run(self, run_id: UUID, *, owner_id: UUID) -> bool:
        assert owner_id == PRINCIPAL.id
        self.deleted.append(run_id)
        return run_id in self.owned


async def test_active_run_registry_is_per_run() -> None:
    limiter = UsageLimiter(make_settings())
    first, second = uuid4(), uuid4()

    await limiter.mark_run_active(first)
    assert await limiter.run_is_active(first)
    assert not await limiter.run_is_active(second)
    await limiter.mark_run_finished(first)
    await limiter.mark_run_finished(first)
    assert not await limiter.run_is_active(first)


async def test_a_streaming_run_cannot_be_removed_until_it_finishes() -> None:
    live, stale = uuid4(), uuid4()
    history = FakeHistory({live, stale})
    app = signed_in(create_app(make_settings()))
    app.dependency_overrides[get_history] = lambda: history

    async with client_for(app) as client:
        await app.state.limiter.mark_run_active(live)
        blocked = await client.delete(f"/api/v1/research/runs/{live}")
        removed_stale = await client.delete(f"/api/v1/research/runs/{stale}")
        await app.state.limiter.mark_run_finished(live)
        removed_live = await client.delete(f"/api/v1/research/runs/{live}")

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "run_in_progress"
    assert removed_stale.status_code == 200
    assert removed_live.status_code == 200
    assert history.deleted == [stale, live]


async def test_another_accounts_live_run_stays_not_found() -> None:
    foreign = uuid4()
    history = FakeHistory(set())
    app = signed_in(create_app(make_settings()))
    app.dependency_overrides[get_history] = lambda: history

    async with client_for(app) as client:
        await app.state.limiter.mark_run_active(foreign)
        response = await client.delete(f"/api/v1/research/runs/{foreign}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert history.deleted == []
