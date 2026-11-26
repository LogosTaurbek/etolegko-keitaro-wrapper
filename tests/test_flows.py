import json
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.keitaro_client import keitaro_client
from app.main import app


@pytest_asyncio.fixture
async def test_session_factory():
    await keitaro_client.close()
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_fk(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        yield session_factory
    finally:
        await keitaro_client.close()
        await engine.dispose()


@pytest.fixture
def respx_mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock_router:
        # Allow in-process ASGI requests from AsyncClient(transport=ASGITransport(app=app))
        mock_router.route(host="test").pass_through()
        yield mock_router


@pytest_asyncio.fixture
async def api_client(test_session_factory, respx_mock):
    async def override_get_db():
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _base_url() -> str:
    return settings.KEITARO_BASE_URL.rstrip("/") + "/admin_api/v1"


def _mock_references(mock_router: respx.MockRouter, base_url: str):
    mock_router.get(f"{base_url}/domains").mock(return_value=Response(200, json=[{"id": 1, "name": "in.posdk.xyz"}]))
    mock_router.get(f"{base_url}/groups", params={"type": "campaigns"}).mock(
        return_value=Response(200, json=[{"id": 10, "name": "FORTESTS"}])
    )
    mock_router.get(f"{base_url}/traffic_sources").mock(
        return_value=Response(200, json=[{"id": 5, "name": "FORTESTS"}])
    )
    mock_router.get(f"{base_url}/offers").mock(
        return_value=Response(
            200,
            json=[
                {"id": 3749, "name": "Miaflow RO", "state": "active"},
                {"id": 3717, "name": "Miaflow 2", "state": "active"},
                {"id": 4001, "name": "Offer Three", "state": "active"},
            ],
        )
    )


@pytest.mark.asyncio
async def test_campaign_creation_and_partial_failure(api_client, respx_mock, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_DOMAIN_NAME", "https://in.posdk.xyz/")
    monkeypatch.setattr(settings, "DEFAULT_GROUP_NAME", "FORTESTS")
    monkeypatch.setattr(settings, "DEFAULT_SOURCE_NAME", "FORTESTS")

    base_url = _base_url()
    _mock_references(respx_mock, base_url)

    camp_route = respx_mock.post(f"{base_url}/campaigns").mock(
        return_value=Response(200, json={"id": 100, "name": "Test Camp", "alias": "wVqN1R"})
    )
    stream_route = respx_mock.post(f"{base_url}/streams").mock(
        side_effect=[
            Response(200, json={"id": 201}),
            Response(200, json={"id": 202}),
        ]
    )
    respx_mock.get(f"{base_url}/campaigns/100/streams").mock(
        return_value=Response(
            200,
            json=[
                {"id": 201, "name": "Geo AU -> Google", "position": 1, "schema": "redirect", "offers": []},
                {
                    "id": 202,
                    "name": "Main Offer",
                    "position": 2,
                    "schema": "landings",
                    "offers": [{"offer_id": 3749, "share": 100, "state": "active"}],
                },
            ],
        )
    )

    resp = await api_client.post("/api/campaigns", json={"name": "Test Camp", "geo": "au", "offer_id": 3749})
    assert resp.status_code == 200
    body = resp.json()
    assert body["keitaro_id"] == 100
    assert len(body["streams"]) == 2

    # Verify campaign payload parameters from .env
    camp_req = json.loads(camp_route.calls[0].request.content)
    assert camp_req["domain_id"] == 1
    assert camp_req["group_id"] == 10
    assert camp_req["traffic_source_id"] == 5
    assert camp_req["type"] == "position"
    assert camp_req["cost_type"] == "CPC"
    assert camp_req["cost_auto"] is True
    assert len(camp_req["alias"]) == 6

    # Verify Flow 1 & Flow 2 payloads
    flow1_req = json.loads(stream_route.calls[0].request.content)
    assert flow1_req["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU"]}]
    assert flow1_req["action_payload"] == "https://google.com"
    flow2_req = json.loads(stream_route.calls[1].request.content)
    assert flow2_req["offers"] == [{"offer_id": 3749, "share": 100, "state": "active"}]

    # Partial failure when second stream fails -> 502 with created_campaign_id and stage
    respx_mock.post(f"{base_url}/campaigns").mock(
        return_value=Response(200, json={"id": 101, "name": "Partial Camp", "alias": "abc123"})
    )
    respx_mock.post(f"{base_url}/streams").mock(
        side_effect=[
            Response(200, json={"id": 301}),
            Response(500, text="Keitaro internal error"),
        ]
    )
    fail_resp = await api_client.post("/api/campaigns", json={"name": "Partial Camp", "geo": "MX", "offer_id": 3749})
    assert fail_resp.status_code == 502
    assert fail_resp.json()["detail"]["created_campaign_id"] == 101
    assert fail_resp.json()["detail"]["stage"] == "flow_2_offers"


@pytest.mark.asyncio
async def test_draft_lifecycle_add_remove_bringback_pin_cancel(api_client, respx_mock, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_DOMAIN_NAME", "https://in.posdk.xyz/")
    monkeypatch.setattr(settings, "DEFAULT_GROUP_NAME", "FORTESTS")
    monkeypatch.setattr(settings, "DEFAULT_SOURCE_NAME", "FORTESTS")

    base_url = _base_url()
    _mock_references(respx_mock, base_url)

    respx_mock.post(f"{base_url}/campaigns").mock(
        return_value=Response(200, json={"id": 100, "name": "Draft Camp", "alias": "wVqN1R"})
    )
    respx_mock.post(f"{base_url}/streams").mock(
        side_effect=[Response(200, json={"id": 201}), Response(200, json={"id": 202})]
    )
    respx_mock.get(f"{base_url}/campaigns/100/streams").mock(
        return_value=Response(
            200,
            json=[
                {"id": 201, "name": "Geo Filter", "position": 1, "schema": "redirect", "offers": []},
                {
                    "id": 202,
                    "name": "Main Offer",
                    "position": 2,
                    "schema": "landings",
                    "offers": [{"offer_id": 3749, "share": 100, "state": "active"}],
                },
            ],
        )
    )

    create_resp = await api_client.post("/api/campaigns", json={"name": "Draft Camp", "geo": "AU", "offer_id": 3749})
    assert create_resp.status_code == 200
    streams = create_resp.json()["streams"]
    non_offer_stream = next(s for s in streams if not s["is_offer_stream"])
    offer_stream = next(s for s in streams if s["is_offer_stream"])
    sid = offer_stream["id"]

    # Non-offer stream cannot be mutated
    bad_mut = await api_client.post(f"/api/streams/{non_offer_stream['id']}/offers", json={"offer_id": 3717})
    assert bad_mut.status_code == 400

    # 1. Add offer 3717 -> is_dirty=True, shares 50/50
    r_add = await api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 3717})
    assert r_add.status_code == 200
    d_add = r_add.json()
    assert d_add["is_dirty"] is True
    assert {o["offer_id"]: o["share"] for o in d_add["offers"]} == {3749: 50, 3717: 50}
    assert {o["offer_id"]: o["status"] for o in d_add["offers"]} == {3749: "active", 3717: "draft_added"}

    # 2. Remove 3749 -> removed with 0%, 3717 becomes 100%
    r_rem = await api_client.post(f"/api/streams/{sid}/offers/3749/remove")
    assert r_rem.status_code == 200
    by_id = {o["offer_id"]: o for o in r_rem.json()["offers"]}
    assert by_id[3749]["status"] == "removed"
    assert by_id[3749]["share"] == 0
    assert by_id[3717]["share"] == 100

    # 3. Bring back 3749 -> active, 50/50
    r_bb = await api_client.post(f"/api/streams/{sid}/offers/3749/bring-back")
    assert r_bb.status_code == 200
    by_id = {o["offer_id"]: o for o in r_bb.json()["offers"]}
    assert by_id[3749]["status"] == "active"
    assert by_id[3749]["share"] == 50
    assert by_id[3717]["share"] == 50

    # 4. Pin 3749 to 25% + add 4001 -> 25 / 38 / 37
    r_pin = await api_client.post(f"/api/streams/{sid}/offers/3749/pin", json={"is_pinned": True, "share": 25})
    assert r_pin.status_code == 200
    by_id = {o["offer_id"]: o for o in r_pin.json()["offers"]}
    assert by_id[3749]["share"] == 25
    assert by_id[3717]["share"] == 75

    r_add3 = await api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 4001})
    assert r_add3.status_code == 200
    by_id = {o["offer_id"]: o for o in r_add3.json()["offers"]}
    assert by_id[3749]["share"] == 25
    assert by_id[3717]["share"] == 38
    assert by_id[4001]["share"] == 37

    # Attempt removing both unpinned offers while 3749 is pinned at 25% -> 400 & rollback!
    await api_client.post(f"/api/streams/{sid}/offers/4001/remove")
    r_fail_rem = await api_client.post(f"/api/streams/{sid}/offers/3717/remove")
    assert r_fail_rem.status_code == 400

    # Verify 3717 was rolled back and remains draft_added with 75%
    r_streams = await api_client.get("/api/campaigns/100/streams")
    s_state = next(s for s in r_streams.json() if s["id"] == sid)
    by_id = {o["offer_id"]: o for o in s_state["offers"]}
    assert by_id[3717]["status"] == "draft_added"
    assert by_id[3717]["share"] == 75

    # 5. Cancel -> draft_added offers (3717, 4001) removed, 3749 restored to 100%, is_dirty=False
    r_cancel = await api_client.post(f"/api/streams/{sid}/cancel")
    assert r_cancel.status_code == 200
    d_cancel = r_cancel.json()
    assert d_cancel["is_dirty"] is False
    assert len(d_cancel["offers"]) == 1
    assert d_cancel["offers"][0]["offer_id"] == 3749
    assert d_cancel["offers"][0]["share"] == 100
    assert d_cancel["offers"][0]["is_pinned"] is False


@pytest.mark.asyncio
async def test_external_deletion_and_conflict_protection(api_client, respx_mock):
    base_url = _base_url()
    _mock_references(respx_mock, base_url)

    respx_mock.get(f"{base_url}/campaigns").mock(
        return_value=Response(200, json=[{"id": 55, "name": "Existing Camp", "alias": "xyz999"}])
    )
    # Initial fetch: stream has 2 offers
    fetch_route = respx_mock.get(f"{base_url}/campaigns/55/streams").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 501,
                    "name": "Main",
                    "position": 1,
                    "schema": "landings",
                    "filters": [{"name": "country", "mode": "reject", "payload": ["US"]}],
                    "offers": [
                        {"offer_id": 3749, "share": 50, "state": "active"},
                        {"offer_id": 3717, "share": 50, "state": "active"},
                    ],
                }
            ],
        )
    )

    f1 = await api_client.post("/api/campaigns/55/fetch-streams")
    assert f1.status_code == 200
    sid = f1.json()["streams"][0]["id"]

    # Local draft mutation -> Fetch without discard=true returns 409
    await api_client.post(f"/api/streams/{sid}/offers", json={"offer_id": 4001})
    f_conflict = await api_client.post("/api/campaigns/55/fetch-streams?discard=false")
    assert f_conflict.status_code == 409

    # External deletion of ALL offers in Keitaro + Fetch with discard=true
    fetch_route.mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 501,
                    "name": "Main",
                    "position": 1,
                    "schema": "landings",
                    "filters": [{"name": "country", "mode": "reject", "payload": ["US"]}],
                    "offers": [],
                }
            ],
        )
    )
    f2 = await api_client.post("/api/campaigns/55/fetch-streams?discard=true")
    assert f2.status_code == 200
    stream_after = f2.json()["streams"][0]
    assert stream_after["is_offer_stream"] is True
    assert stream_after["is_dirty"] is False
    assert stream_after["has_offers"] is False
    # Draft-added 4001 was discarded; 3749 and 3717 became removed with share=0
    assert {o["offer_id"]: (o["status"], o["share"]) for o in stream_after["offers"]} == {
        3749: ("removed", 0),
        3717: ("removed", 0),
    }

    # Cannot push empty stream (0 active offers)
    push_empty = await api_client.post(f"/api/streams/{sid}/push")
    assert push_empty.status_code == 400

    # Bring back 3749 -> 100%
    bb = await api_client.post(f"/api/streams/{sid}/offers/3749/bring-back")
    assert bb.status_code == 200
    assert bb.json()["has_offers"] is True

    # Remote changed in Keitaro (e.g. offer 4001 added externally) -> Push without force returns 409
    respx_mock.get(f"{base_url}/streams/501").mock(
        return_value=Response(
            200,
            json={
                "id": 501,
                "name": "Main",
                "position": 1,
                "schema": "landings",
                "filters": [{"name": "country", "mode": "reject", "payload": ["US"]}],
                "offers": [{"offer_id": 4001, "share": 100, "state": "active"}],
            },
        )
    )
    push_conflict = await api_client.post(f"/api/streams/{sid}/push?force=false")
    assert push_conflict.status_code == 409

    # Push with force=true succeeds, preserves filters, updates committed state and clears is_dirty
    put_route = respx_mock.put(f"{base_url}/streams/501").mock(
        return_value=Response(
            200,
            json={
                "id": 501,
                "name": "Main",
                "position": 1,
                "schema": "landings",
                "filters": [{"name": "country", "mode": "reject", "payload": ["US"]}],
                "offers": [{"offer_id": 3749, "share": 100, "state": "active"}],
            },
        )
    )
    push_ok = await api_client.post(f"/api/streams/{sid}/push?force=true")
    assert push_ok.status_code == 200
    pushed_data = push_ok.json()
    assert pushed_data["is_dirty"] is False
    put_body = json.loads(put_route.calls[0].request.content)
    assert put_body["filters"] == [{"name": "country", "mode": "reject", "payload": ["US"]}]
    assert put_body["offers"] == [{"offer_id": 3749, "share": 100, "state": "active"}]


@pytest.mark.asyncio
async def test_offers_search_and_sync(api_client, respx_mock):
    base_url = _base_url()
    _mock_references(respx_mock, base_url)

    # First search populates empty cache from Keitaro
    r1 = await api_client.get("/api/offers/search", params={"q": "Mia"})
    assert r1.status_code == 200
    assert len(r1.json()) == 2

    # Search by exact ID
    r2 = await api_client.get("/api/offers/search", params={"q": "4001"})
    assert r2.status_code == 200
    assert [o["id"] for o in r2.json()] == [4001]

    # Explicit sync
    r3 = await api_client.post("/api/offers/sync")
    assert r3.status_code == 200
    assert r3.json() == {"status": "success", "count": 3}
