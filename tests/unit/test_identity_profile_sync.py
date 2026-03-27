import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from src.models.identity import Identity


def _make_identity(
    identity_id: str = "test_id",
    mxid: str = "@test:matrix.test",
    token: str = "tok_abc",
    password: str | None = "pass_abc",
    display_name: str | None = "Test User",
) -> Identity:
    return Identity(
        id=identity_id,
        identity_type="agent",
        mxid=mxid,
        access_token=token,
        password_hash=password,
        display_name=display_name,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_profile_noop_when_display_name_none():
    """_sync_identity_profile should return immediately when display_name is None."""
    from src.api.routes.identity import _sync_identity_profile

    with patch("src.api.routes.identity.get_identity_service") as mock_svc:
        await _sync_identity_profile("id1", None)
        mock_svc.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_profile_raises_404_when_identity_missing():
    """_sync_identity_profile raises 404 if identity not found."""
    from src.api.routes.identity import _sync_identity_profile

    with patch("src.api.routes.identity.get_identity_service") as mock_svc:
        mock_svc.return_value.get.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await _sync_identity_profile("missing_id", "New Name")
        assert exc_info.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_profile_uses_token_when_healthy():
    """When monitor reports healthy and token exists, use set_user_display_name."""
    from src.api.routes.identity import _sync_identity_profile

    identity = _make_identity()

    with (
        patch("src.api.routes.identity.get_identity_service") as mock_svc,
        patch("src.api.routes.identity.get_identity_token_health_monitor") as mock_monitor_fn,
        patch("src.api.routes.identity.MatrixUserManager") as MockUM,
    ):
        mock_svc.return_value.get.return_value = identity
        mock_monitor = mock_monitor_fn.return_value
        mock_monitor.ensure_identity_healthy = AsyncMock(return_value=True)

        mock_um = MockUM.return_value
        mock_um.set_user_display_name = AsyncMock(return_value=True)
        mock_um.update_display_name = AsyncMock(return_value=False)

        await _sync_identity_profile("test_id", "New Name")

        mock_um.set_user_display_name.assert_called_once_with(
            str(identity.mxid), "New Name", str(identity.access_token)
        )
        mock_um.update_display_name.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_profile_falls_back_to_password():
    """When token sync fails, fall back to password-based update_display_name."""
    from src.api.routes.identity import _sync_identity_profile

    identity = _make_identity()

    with (
        patch("src.api.routes.identity.get_identity_service") as mock_svc,
        patch("src.api.routes.identity.get_identity_token_health_monitor") as mock_monitor_fn,
        patch("src.api.routes.identity.MatrixUserManager") as MockUM,
    ):
        mock_svc.return_value.get.return_value = identity
        mock_monitor = mock_monitor_fn.return_value
        mock_monitor.ensure_identity_healthy = AsyncMock(return_value=True)

        mock_um = MockUM.return_value
        mock_um.set_user_display_name = AsyncMock(return_value=False)
        mock_um.update_display_name = AsyncMock(return_value=True)

        await _sync_identity_profile("test_id", "New Name")

        mock_um.set_user_display_name.assert_called_once()
        mock_um.update_display_name.assert_called_once_with(
            str(identity.mxid), "New Name", str(identity.password_hash)
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_profile_falls_back_to_password_when_token_unhealthy():
    """When monitor says token unhealthy (no token), skip token path, try password."""
    from src.api.routes.identity import _sync_identity_profile

    identity = _make_identity(token="")

    with (
        patch("src.api.routes.identity.get_identity_service") as mock_svc,
        patch("src.api.routes.identity.get_identity_token_health_monitor") as mock_monitor_fn,
        patch("src.api.routes.identity.MatrixUserManager") as MockUM,
    ):
        mock_svc.return_value.get.return_value = identity
        mock_monitor = mock_monitor_fn.return_value
        mock_monitor.ensure_identity_healthy = AsyncMock(return_value=False)

        mock_um = MockUM.return_value
        mock_um.set_user_display_name = AsyncMock(return_value=False)
        mock_um.update_display_name = AsyncMock(return_value=True)

        await _sync_identity_profile("test_id", "New Name")

        mock_um.set_user_display_name.assert_not_called()
        mock_um.update_display_name.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_profile_raises_502_when_all_methods_fail():
    """When both token and password sync fail, raise 502."""
    from src.api.routes.identity import _sync_identity_profile

    identity = _make_identity()

    with (
        patch("src.api.routes.identity.get_identity_service") as mock_svc,
        patch("src.api.routes.identity.get_identity_token_health_monitor") as mock_monitor_fn,
        patch("src.api.routes.identity.MatrixUserManager") as MockUM,
    ):
        mock_svc.return_value.get.return_value = identity
        mock_monitor = mock_monitor_fn.return_value
        mock_monitor.ensure_identity_healthy = AsyncMock(return_value=True)

        mock_um = MockUM.return_value
        mock_um.set_user_display_name = AsyncMock(return_value=False)
        mock_um.update_display_name = AsyncMock(return_value=False)

        with pytest.raises(HTTPException) as exc_info:
            await _sync_identity_profile("test_id", "New Name")
        assert exc_info.value.status_code == 502


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_profile_no_password_skips_fallback():
    """When identity has no password_hash, password fallback is skipped."""
    from src.api.routes.identity import _sync_identity_profile

    identity = _make_identity(password=None)

    with (
        patch("src.api.routes.identity.get_identity_service") as mock_svc,
        patch("src.api.routes.identity.get_identity_token_health_monitor") as mock_monitor_fn,
        patch("src.api.routes.identity.MatrixUserManager") as MockUM,
    ):
        mock_svc.return_value.get.return_value = identity
        mock_monitor = mock_monitor_fn.return_value
        mock_monitor.ensure_identity_healthy = AsyncMock(return_value=True)

        mock_um = MockUM.return_value
        mock_um.set_user_display_name = AsyncMock(return_value=False)
        mock_um.update_display_name = AsyncMock(return_value=False)

        with pytest.raises(HTTPException) as exc_info:
            await _sync_identity_profile("test_id", "New Name")
        assert exc_info.value.status_code == 502
        mock_um.update_display_name.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_profile_refreshes_token_after_password_sync():
    """After successful password-based sync, monitor.ensure_identity_healthy is called again."""
    from src.api.routes.identity import _sync_identity_profile

    identity = _make_identity()

    with (
        patch("src.api.routes.identity.get_identity_service") as mock_svc,
        patch("src.api.routes.identity.get_identity_token_health_monitor") as mock_monitor_fn,
        patch("src.api.routes.identity.MatrixUserManager") as MockUM,
    ):
        mock_svc.return_value.get.return_value = identity
        mock_monitor = mock_monitor_fn.return_value
        mock_monitor.ensure_identity_healthy = AsyncMock(return_value=True)

        mock_um = MockUM.return_value
        mock_um.set_user_display_name = AsyncMock(return_value=False)
        mock_um.update_display_name = AsyncMock(return_value=True)

        await _sync_identity_profile("test_id", "New Name")

        assert mock_monitor.ensure_identity_healthy.call_count == 2


_test_engine = None
_test_session_maker = None


@pytest.fixture(scope="module")
def identity_sqlite_engine():
    global _test_engine, _test_session_maker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    from src.models.agent_mapping import Base
    from src.models.identity import Identity, DMRoom

    _test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    _test_session_maker = sessionmaker(bind=_test_engine)
    Base.metadata.create_all(_test_engine)

    yield _test_engine

    Base.metadata.drop_all(_test_engine)
    _test_engine.dispose()
    _test_engine = None
    _test_session_maker = None


@pytest.fixture(autouse=True)
def patch_database(identity_sqlite_engine, monkeypatch):
    import src.models.agent_mapping as agent_mapping_module
    import src.core.identity_storage as identity_storage_module
    from src.core.identity_storage import IdentityStorageService, DMRoomStorageService
    from src.models.identity import Identity as IdentityModel, DMRoom

    monkeypatch.setattr(agent_mapping_module, "get_engine", lambda: _test_engine)
    monkeypatch.setattr(agent_mapping_module, "get_session_maker", lambda: _test_session_maker)

    IdentityStorageService._instance = None
    IdentityStorageService._initialized = False
    DMRoomStorageService._instance = None
    DMRoomStorageService._initialized = False
    identity_storage_module._identity_service = None
    identity_storage_module._dm_room_service = None

    yield

    if _test_session_maker:
        session = _test_session_maker()
        try:
            session.query(IdentityModel).delete()
            session.query(DMRoom).delete()
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    IdentityStorageService._instance = None
    IdentityStorageService._initialized = False
    DMRoomStorageService._instance = None
    DMRoomStorageService._initialized = False
    identity_storage_module._identity_service = None
    identity_storage_module._dm_room_service = None


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from src.api.app import app

    return TestClient(app)


@pytest.fixture
def sample_identity():
    return {
        "id": "sync_test_001",
        "identity_type": "agent",
        "mxid": "@sync_test:matrix.test",
        "access_token": "token_sync_test",
        "display_name": "Original Name",
        "password_hash": "pass_sync",
    }


@pytest.mark.unit
class TestUpdateIdentityDisplayNameSync:
    """Tests that update_identity triggers _sync_identity_profile when display_name changes."""

    def test_display_name_change_triggers_sync(self, client, sample_identity):
        """PUT with new display_name should call _sync_identity_profile."""
        client.post("/api/v1/identities", json=sample_identity)

        with patch(
            "src.api.routes.identity._sync_identity_profile", new_callable=AsyncMock
        ) as mock_sync:
            response = client.put(
                f"/api/v1/identities/{sample_identity['id']}",
                json={"display_name": "New Name"},
            )

        assert response.status_code == 200
        assert response.json()["display_name"] == "New Name"
        mock_sync.assert_called_once_with(sample_identity["id"], "New Name")

    def test_same_display_name_does_not_trigger_sync(self, client, sample_identity):
        """PUT with same display_name should NOT call _sync_identity_profile."""
        client.post("/api/v1/identities", json=sample_identity)

        with patch(
            "src.api.routes.identity._sync_identity_profile", new_callable=AsyncMock
        ) as mock_sync:
            response = client.put(
                f"/api/v1/identities/{sample_identity['id']}",
                json={"display_name": "Original Name"},
            )

        assert response.status_code == 200
        mock_sync.assert_not_called()

    def test_non_display_name_update_does_not_trigger_sync(self, client, sample_identity):
        """PUT updating only avatar_url should NOT call _sync_identity_profile."""
        client.post("/api/v1/identities", json=sample_identity)

        with patch(
            "src.api.routes.identity._sync_identity_profile", new_callable=AsyncMock
        ) as mock_sync:
            response = client.put(
                f"/api/v1/identities/{sample_identity['id']}",
                json={"avatar_url": "mxc://matrix.test/avatar"},
            )

        assert response.status_code == 200
        mock_sync.assert_not_called()

    def test_sync_failure_returns_502(self, client, sample_identity):
        """If _sync_identity_profile raises 502, the endpoint should propagate it."""
        client.post("/api/v1/identities", json=sample_identity)

        with patch(
            "src.api.routes.identity._sync_identity_profile",
            new_callable=AsyncMock,
            side_effect=HTTPException(status_code=502, detail="Sync failed"),
        ):
            response = client.put(
                f"/api/v1/identities/{sample_identity['id']}",
                json={"display_name": "New Name"},
            )

        assert response.status_code == 502
