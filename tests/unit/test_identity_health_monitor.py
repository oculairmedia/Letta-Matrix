import asyncio
from unittest.mock import AsyncMock, MagicMock
from unittest.mock import patch

import pytest

from src.core.identity_health_monitor import IdentityMonitorSummary, IdentityTokenHealthMonitor
from src.models.identity import Identity


def _identity(identity_id: str, mxid: str, token: str = "tok", password: str | None = "pass"):
    return Identity(
        id=identity_id,
        identity_type="agent",
        mxid=mxid,
        access_token=token,
        password_hash=password,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_identity_healthy():
    identity_service = MagicMock()
    user_manager = MagicMock()
    monitor = IdentityTokenHealthMonitor(
        homeserver_url="https://matrix.test",
        identity_service=identity_service,
        user_manager=user_manager,
    )
    identity = _identity("id1", "@id1:matrix.test")

    monitor._validate_identity_token = AsyncMock(return_value=True)

    status = await monitor._check_identity(identity)

    assert status == "healthy"
    identity_service.update.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_identity_relogin_recovered():
    identity_service = MagicMock()
    user_manager = MagicMock()
    monitor = IdentityTokenHealthMonitor(
        homeserver_url="https://matrix.test",
        identity_service=identity_service,
        user_manager=user_manager,
    )
    identity = _identity("id2", "@id2:matrix.test", password="known-pass")

    monitor._validate_identity_token = AsyncMock(return_value=False)
    monitor._login_with_password = AsyncMock(return_value=("new-token", "DEV1"))

    status = await monitor._check_identity(identity)

    assert status == "relogin_recovered"
    identity_service.update.assert_called_once_with(
        "id2",
        access_token="new-token",
        device_id="DEV1",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_identity_reset_recovered_after_failed_login():
    identity_service = MagicMock()
    user_manager = MagicMock()
    monitor = IdentityTokenHealthMonitor(
        homeserver_url="https://matrix.test",
        interval_seconds=1,
        identity_service=identity_service,
        user_manager=user_manager,
    )
    identity = _identity("id3", "@id3:matrix.test", password="old-pass")

    monitor._validate_identity_token = AsyncMock(return_value=False)
    monitor._login_with_password = AsyncMock(side_effect=[None, ("fresh-token", "DEV2")])
    monitor._reset_password_via_admin_room = AsyncMock(return_value=True)

    status = await monitor._check_identity(identity)

    assert status == "reset_recovered"
    identity_service.update.assert_called_once()
    update_args, update_kwargs = identity_service.update.call_args
    assert update_args[0] == "id3"
    assert update_kwargs["access_token"] == "fresh-token"
    assert update_kwargs["device_id"] == "DEV2"
    assert update_kwargs["password_hash"].startswith("IdentityRepair_id3_")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_identity_reset_recovered_syncs_agent_mapping_for_letta_identity():
    identity_service = MagicMock()
    user_manager = MagicMock()
    monitor = IdentityTokenHealthMonitor(
        homeserver_url="https://matrix.test",
        interval_seconds=1,
        identity_service=identity_service,
        user_manager=user_manager,
    )
    identity = _identity("letta_agent-abc", "@agent_abc:matrix.test", password="old-pass")

    monitor._validate_identity_token = AsyncMock(return_value=False)
    monitor._login_with_password = AsyncMock(side_effect=[None, ("fresh-token", "DEVX")])
    monitor._reset_password_via_admin_room = AsyncMock(return_value=True)
    identity_service.update.return_value = identity

    with patch(
        "src.core.identity_health_monitor.sync_agent_password_consistently",
        new=AsyncMock(return_value=True),
    ) as sync_password:
        status = await monitor._check_identity(identity)

    assert status == "reset_recovered"
    assert sync_password.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_identity_reset_recovery_fails_when_cross_store_sync_fails():
    identity_service = MagicMock()
    user_manager = MagicMock()
    monitor = IdentityTokenHealthMonitor(
        homeserver_url="https://matrix.test",
        interval_seconds=1,
        identity_service=identity_service,
        user_manager=user_manager,
    )
    monitor.max_reset_retries = 1
    identity = _identity("letta_agent-fail", "@agent_fail:matrix.test", password="old-pass")

    monitor._validate_identity_token = AsyncMock(return_value=False)
    monitor._login_with_password = AsyncMock(side_effect=[None, ("fresh-token", "DEVY")])
    monitor._reset_password_via_admin_room = AsyncMock(return_value=True)
    identity_service.update.return_value = identity

    with (
        patch("src.core.identity_health_monitor.asyncio.sleep", new=AsyncMock(return_value=None)),
        patch(
            "src.core.identity_health_monitor.sync_agent_password_consistently",
            new=AsyncMock(return_value=False),
        ),
    ):
        status = await monitor._check_identity(identity)

    assert status == "failed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_identity_missing_password():
    identity_service = MagicMock()
    user_manager = MagicMock()
    monitor = IdentityTokenHealthMonitor(
        homeserver_url="https://matrix.test",
        identity_service=identity_service,
        user_manager=user_manager,
    )
    identity = _identity("id4", "@id4:matrix.test", password=None)

    monitor._validate_identity_token = AsyncMock(return_value=False)

    status = await monitor._check_identity(identity)

    assert status == "missing_password"
    identity_service.update.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_once_summary_counts():
    identity_service = MagicMock()
    user_manager = MagicMock()
    monitor = IdentityTokenHealthMonitor(
        homeserver_url="https://matrix.test",
        identity_service=identity_service,
        user_manager=user_manager,
    )

    identities = [
        _identity("a", "@a:matrix.test"),
        _identity("b", "@b:matrix.test"),
        _identity("c", "@c:matrix.test"),
        _identity("d", "@d:matrix.test"),
        _identity("e", "@e:matrix.test"),
    ]
    identity_service.get_all.return_value = identities
    monitor._check_identity = AsyncMock(
        side_effect=["healthy", "relogin_recovered", "reset_recovered", "missing_password", "failed"]
    )

    summary = await monitor.check_once()

    assert summary.total == 5
    assert summary.healthy == 1
    assert summary.relogin_recovered == 1
    assert summary.reset_recovered == 1
    assert summary.missing_password == 1
    assert summary.failed == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_start_stop_monitor_lifecycle():
    identity_service = MagicMock()
    user_manager = MagicMock()
    monitor = IdentityTokenHealthMonitor(
        homeserver_url="https://matrix.test",
        interval_seconds=60,
        identity_service=identity_service,
        user_manager=user_manager,
    )

    wait_event = asyncio.Event()

    async def fake_check_once() -> IdentityMonitorSummary:
        wait_event.set()
        return IdentityMonitorSummary()

    monitor.check_once = AsyncMock(side_effect=fake_check_once)

    await monitor.start()
    await asyncio.wait_for(wait_event.wait(), timeout=1)
    assert monitor._task is not None

    await monitor.stop()
    assert monitor._task is None
