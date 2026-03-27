from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.password_consistency import sync_agent_password_consistently


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_agent_password_consistently_success():
    mapping = SimpleNamespace(
        agent_id="agent-1",
        agent_name="Agent One",
        matrix_user_id="@agent_1:matrix.test",
        matrix_password="old-pass",
        room_id="!room:test",
        room_created=True,
    )
    identity = SimpleNamespace(password_hash="old-pass")

    mapping_db = MagicMock()
    mapping_db.get_by_agent_id.return_value = mapping

    identity_service = MagicMock()
    identity_service.get.return_value = identity
    identity_service.update.return_value = identity

    invalidate = MagicMock()

    result = await sync_agent_password_consistently(
        "agent-1",
        "new-pass",
        mapping_db=mapping_db,
        identity_service=identity_service,
        max_retries=1,
        invalidate_cache_fn=invalidate,
    )

    assert result is True
    mapping_db.upsert.assert_called_once_with(
        "agent-1",
        "Agent One",
        "@agent_1:matrix.test",
        "new-pass",
        room_id="!room:test",
        room_created=True,
    )
    identity_service.update.assert_called_once_with("letta_agent-1", password_hash="new-pass")
    invalidate.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_agent_password_consistently_retries_with_backoff_then_succeeds():
    mapping = SimpleNamespace(
        agent_id="agent-2",
        agent_name="Agent Two",
        matrix_user_id="@agent_2:matrix.test",
        matrix_password="old-pass",
        room_id="!room:test",
        room_created=False,
    )
    identity = SimpleNamespace(password_hash="old-pass")

    mapping_db = MagicMock()
    mapping_db.get_by_agent_id.return_value = mapping

    call_count = {"value": 0}

    def upsert_side_effect(*args, **kwargs):
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise RuntimeError("db down")
        return None

    mapping_db.upsert.side_effect = upsert_side_effect

    identity_service = MagicMock()
    identity_service.get.return_value = identity
    identity_service.update.return_value = identity

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    with patch("src.core.password_consistency.asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)):
        result = await sync_agent_password_consistently(
            "agent-2",
            "new-pass",
            mapping_db=mapping_db,
            identity_service=identity_service,
            max_retries=2,
            backoff_seconds=0.25,
            invalidate_cache_fn=MagicMock(),
        )

    assert result is True
    assert sleep_calls == [0.25]
    assert mapping_db.upsert.call_count == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_agent_password_consistently_returns_false_when_identity_missing():
    mapping = SimpleNamespace(
        agent_id="agent-3",
        agent_name="Agent Three",
        matrix_user_id="@agent_3:matrix.test",
        matrix_password="old-pass",
        room_id=None,
        room_created=False,
    )

    mapping_db = MagicMock()
    mapping_db.get_by_agent_id.return_value = mapping

    identity_service = MagicMock()
    identity_service.get.return_value = None

    result = await sync_agent_password_consistently(
        "agent-3",
        "new-pass",
        mapping_db=mapping_db,
        identity_service=identity_service,
        max_retries=1,
        invalidate_cache_fn=MagicMock(),
    )

    assert result is False
    mapping_db.upsert.assert_not_called()
