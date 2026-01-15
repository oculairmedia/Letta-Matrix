import builtins
import builtins
import json
import pytest

from src.core import mapping_service


@pytest.fixture
def reset_mapping_cache():
    mapping_service.invalidate_cache()
    mapping_service._mapping_cache = None
    mapping_service._cache_valid = False


@pytest.mark.integration
@pytest.mark.sqlite
def test_get_all_mappings_uses_db(sqlite_db, monkeypatch, tmp_path, reset_mapping_cache):
    sqlite_db.upsert(
        agent_id="agent-db-only",
        agent_name="DbOnly",
        matrix_user_id="@db:matrix.test",
        matrix_password="pw",
        room_id="!dbroom:matrix.test",
        room_created=True,
    )

    json_path = tmp_path / "agent_user_mappings.json"
    json_path.write_text(json.dumps({
        "agent-db-only": {
            "agent_id": "agent-db-only",
            "agent_name": "JsonValue",
            "matrix_user_id": "@json:matrix.test",
            "matrix_password": "pw",
            "room_id": "!jsonroom:matrix.test",
            "room_created": True,
            "created": True,
            "invitation_status": {}
        }
    }))

    def _get_db_override():
        return sqlite_db

    monkeypatch.setattr(mapping_service, "_get_db", _get_db_override)

    def guarded_open(path, *args, **kwargs):
        if str(path).endswith("agent_user_mappings.json"):
            raise AssertionError("mapping_service should not read JSON mappings")
        return builtins.open(path, *args, **kwargs)

    monkeypatch.setattr(mapping_service, "open", guarded_open, raising=False)

    mappings = mapping_service.get_all_mappings()

    assert "agent-db-only" in mappings
    assert mappings["agent-db-only"]["agent_name"] == "DbOnly"
    assert mappings["agent-db-only"]["room_id"] == "!dbroom:matrix.test"


@pytest.mark.integration
@pytest.mark.sqlite
def test_get_mapping_by_agent_id_uses_db(sqlite_db, monkeypatch, reset_mapping_cache):
    sqlite_db.upsert(
        agent_id="agent-lookup",
        agent_name="Lookup",
        matrix_user_id="@lookup:matrix.test",
        matrix_password="pw",
        room_id="!lookroom:matrix.test",
        room_created=True,
    )

    def _get_db_override():
        return sqlite_db

    monkeypatch.setattr(mapping_service, "_get_db", _get_db_override)

    mapping = mapping_service.get_mapping_by_agent_id("agent-lookup")

    assert mapping is not None
    assert mapping["agent_name"] == "Lookup"
    assert mapping["room_id"] == "!lookroom:matrix.test"
