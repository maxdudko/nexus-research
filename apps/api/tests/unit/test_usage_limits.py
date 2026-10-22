"""Process-local login, billable, and mint budgets."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from tests.conftest import PRINCIPAL, client_for, make_settings, signed_in
from tests.unit.graph_support import (
    QUESTION,
    FakeDiscovery,
    FakeIngestion,
    FakePlanner,
    FakeRetrieval,
    empty_discovery,
    make_context,
    plan_for,
    runtime_for,
)

from nexus.ai.errors import ResearchError
from nexus.ai.planner import Planner
from nexus.api.limits import UsageLimiter
from nexus.main import create_app
from nexus.research.nodes.planning import plan_node
from nexus.research.state import initial_state
from nexus.retrieval.vector_store import EvidenceVectorStore

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
EMAIL = "ada@example.com"
HOST = "203.0.113.8"


def _limiter(**overrides: object) -> tuple[UsageLimiter, dict[str, datetime]]:
    clock = {"now": NOW}

    def read() -> datetime:
        return clock["now"]

    return UsageLimiter(make_settings(**overrides), clock=read), clock


async def test_email_and_ip_buckets_share_a_window_and_success_clears_email() -> None:
    limiter, clock = _limiter()
    for _ in range(5):
        assert await limiter.login_retry_after(EMAIL, HOST) is None
        await limiter.record_login_failure(EMAIL, HOST)

    assert await limiter.login_retry_after(EMAIL, HOST) == 15 * 60
    await limiter.clear_email_failures(EMAIL)
    assert await limiter.login_retry_after(EMAIL, HOST) is None
    assert len(limiter._ip_failures[HOST]) == 5

    clock["now"] = NOW - timedelta(seconds=1)
    address = "203.0.113.5"
    for index in range(30):
        await limiter.record_login_failure(f"user{index}@example.com", address)
    assert await limiter.login_retry_after("fresh@example.com", address) == 15 * 60
    await limiter.clear_email_failures("user0@example.com")
    assert await limiter.login_retry_after("user0@example.com", address) == 15 * 60
    clock["now"] = NOW + timedelta(seconds=15 * 60)
    assert await limiter.login_retry_after("fresh@example.com", address) is None


async def test_billable_and_mint_windows_are_separate() -> None:
    limiter, clock = _limiter()
    user_id = PRINCIPAL.id
    for _ in range(10):
        await limiter.charge_billable(user_id)
    with pytest.raises(ResearchError) as blocked:
        await limiter.charge_billable(user_id)
    assert blocked.value.code == "too_many_requests"
    assert blocked.value.retry_after == 60

    for _ in range(30):
        await limiter.charge_mint(user_id)
    with pytest.raises(ResearchError) as minted:
        await limiter.charge_mint(user_id)
    assert minted.value.retry_after == 60 * 60
    clock["now"] = NOW + timedelta(seconds=60)
    await limiter.charge_billable(user_id)
    with pytest.raises(ResearchError):
        await limiter.charge_mint(user_id)


async def test_one_run_and_one_stream_slot_per_user() -> None:
    limiter, _clock = _limiter()
    other = uuid4()
    await limiter.acquire_run(PRINCIPAL.id)
    await limiter.acquire_stream(PRINCIPAL.id)
    await limiter.acquire_run(other)
    with pytest.raises(ResearchError) as blocked:
        await limiter.acquire_stream(PRINCIPAL.id)
    assert blocked.value.retry_after == 1
    await limiter.release_stream(PRINCIPAL.id)
    await limiter.acquire_stream(PRINCIPAL.id)
    await limiter.release_run(PRINCIPAL.id)
    await limiter.acquire_run(PRINCIPAL.id)


async def test_the_eleventh_billable_request_does_not_construct_a_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[object] = []

    def init(self: Planner, settings: object) -> None:
        del settings
        constructed.append(self)

    async def create_plan(self: Planner, question: str):
        del self, question
        return plan_for(["alpha limits"])

    monkeypatch.setattr(Planner, "__init__", init)
    monkeypatch.setattr(Planner, "create_plan", create_plan)
    app = signed_in(create_app(make_settings()))

    async with client_for(app) as client:
        anonymous = create_app(make_settings())
        async with client_for(anonymous) as outsider:
            rejected = await outsider.post(
                "/api/v1/research/plan",
                json={"question": QUESTION},
            )
        assert rejected.status_code == 401
        assert anonymous.state.limiter._billable == {}

        invalid = await client.post(
            "/api/v1/research/plan",
            json={"question": "short"},
        )
        assert invalid.status_code == 422
        assert app.state.limiter._billable == {}

        for _ in range(10):
            allowed = await client.post(
                "/api/v1/research/plan",
                json={"question": QUESTION},
            )
            assert allowed.status_code == 200
        limited = await client.post(
            "/api/v1/research/plan",
            json={"question": QUESTION},
        )

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "too_many_requests"
    assert int(limited.headers["retry-after"]) >= 1
    assert len(constructed) == 10


async def test_a_foreign_scope_charges_and_does_not_open_qdrant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []

    async def missing(*_args: object, **_kwargs: object) -> None:
        return None

    def init(self: EvidenceVectorStore, *_args: object, **_kwargs: object) -> None:
        del self
        opened.append("open")

    monkeypatch.setattr("nexus.api.ownership.owned_scope", missing)
    monkeypatch.setattr(EvidenceVectorStore, "__init__", init)
    app = signed_in(create_app(make_settings()))
    body = {"question": QUESTION, "research_id": str(uuid4())}

    async with client_for(app) as client:
        for _ in range(10):
            missing_scope = await client.post("/api/v1/research/run", json=body)
            assert missing_scope.status_code == 404
        limited = await client.post("/api/v1/research/run", json=body)

    assert limited.status_code == 429
    assert opened == []
    assert len(app.state.limiter._billable[PRINCIPAL.id]) == 10


async def test_the_thirty_first_mint_does_not_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted: list[UUID] = []

    async def mint(factory: object, owner_id: UUID, now: object) -> UUID:
        del factory, owner_id, now
        inserted.append(uuid4())
        return inserted[-1]

    monkeypatch.setattr("nexus.api.v1.research.mint_scope", mint)
    app = signed_in(create_app(make_settings()))

    async with client_for(app) as client:
        for _ in range(30):
            created = await client.post("/api/v1/research/scopes")
            assert created.status_code == 200
        limited = await client.post("/api/v1/research/scopes")

    assert limited.status_code == 429
    assert len(inserted) == 30
    assert PRINCIPAL.id not in app.state.limiter._billable


async def test_a_dead_session_does_not_call_the_planner() -> None:
    planner = FakePlanner(plan_for(["alpha limits"]))
    context = make_context(
        planner=planner,
        discovery=FakeDiscovery([empty_discovery()]),
        ingestion=FakeIngestion(),
        retrieval=FakeRetrieval([]),
    )

    async def closed() -> bool:
        return False

    context.session_active = closed
    state = initial_state(
        research_id=uuid4(),
        run_id=uuid4(),
        question=QUESTION,
        started_at=NOW,
    )

    update = await plan_node(state, runtime_for(context))

    assert update["status"] == "interrupted"
    assert update["error_code"] == "interrupted"
    assert planner.questions == []
