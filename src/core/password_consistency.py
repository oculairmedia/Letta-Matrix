import asyncio
import logging
import os
from typing import Callable, Optional

from src.core.identity_storage import IdentityStorageService, get_identity_service
from src.core.mapping_service import invalidate_cache
from src.models.agent_mapping import AgentMappingDB


logger = logging.getLogger(__name__)


async def sync_agent_password_consistently(
    agent_id: str,
    new_password: str,
    *,
    identity_service: Optional[IdentityStorageService] = None,
    mapping_db: Optional[AgentMappingDB] = None,
    max_retries: Optional[int] = None,
    backoff_seconds: Optional[float] = None,
    invalidate_cache_fn: Callable[[], None] = invalidate_cache,
) -> bool:
    identity_service = identity_service or get_identity_service()
    mapping_db = mapping_db or AgentMappingDB()
    retries = max_retries if max_retries is not None else int(os.getenv("PASSWORD_SYNC_MAX_RETRIES", "3"))
    backoff = (
        backoff_seconds
        if backoff_seconds is not None
        else float(os.getenv("PASSWORD_SYNC_BACKOFF_SECONDS", "0.5"))
    )

    identity_id = f"letta_{agent_id}"

    for attempt in range(1, retries + 1):
        previous_identity_password: Optional[str] = None
        previous_mapping_password: Optional[str] = None
        try:
            mapping = mapping_db.get_by_agent_id(agent_id)
            if mapping is None:
                raise RuntimeError(f"Agent mapping missing for {agent_id}")

            identity = identity_service.get(identity_id)
            if identity is None:
                raise RuntimeError(f"Identity missing for {identity_id}")

            previous_identity_password = (
                str(identity.password_hash) if identity.password_hash is not None else None
            )
            previous_mapping_password = str(mapping.matrix_password)

            room_id = str(mapping.room_id) if mapping.room_id is not None else None
            mapping_db.upsert(
                str(mapping.agent_id),
                str(mapping.agent_name),
                str(mapping.matrix_user_id),
                new_password,
                room_id=room_id,
                room_created=bool(mapping.room_created),
            )
            invalidate_cache_fn()

            updated_identity = identity_service.update(identity_id, password_hash=new_password)
            if updated_identity is None:
                raise RuntimeError(f"Identity update failed for {identity_id}")

            return True
        except Exception as exc:
            logger.warning(
                "Password consistency sync failed for %s (attempt %s/%s): %s",
                agent_id,
                attempt,
                retries,
                exc,
            )
            try:
                if previous_mapping_password is not None:
                    mapping = mapping_db.get_by_agent_id(agent_id)
                    if mapping is not None:
                        room_id = str(mapping.room_id) if mapping.room_id is not None else None
                        mapping_db.upsert(
                            str(mapping.agent_id),
                            str(mapping.agent_name),
                            str(mapping.matrix_user_id),
                            previous_mapping_password,
                            room_id=room_id,
                            room_created=bool(mapping.room_created),
                        )
                        invalidate_cache_fn()
                if previous_identity_password is not None:
                    identity_service.update(identity_id, password_hash=previous_identity_password)
            except Exception as rollback_exc:
                logger.error(
                    "Password consistency rollback failed for %s: %s",
                    agent_id,
                    rollback_exc,
                    exc_info=True,
                )

            if attempt < retries:
                await asyncio.sleep(backoff * (2 ** (attempt - 1)))

    return False
