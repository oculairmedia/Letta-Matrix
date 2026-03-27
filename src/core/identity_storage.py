"""
Identity Storage Service - centralized identity management for Matrix identities.

Provides CRUD operations for Matrix identities and DM room mappings,
replacing the TypeScript JSON-based storage with PostgreSQL.
"""
from datetime import datetime
from typing import Optional, List, Dict
import os
import time
from src.models.identity import Identity, DMRoom, IdentityDB, DMRoomDB
from src.models.agent_mapping import Base, get_engine
import logging

logger = logging.getLogger(__name__)


class IdentityStorageService:
    _instance: Optional['IdentityStorageService'] = None
    _initialized: bool = False
    
    def __new__(cls) -> 'IdentityStorageService':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._db = IdentityDB()
        self._cache: Dict[str, Identity] = {}
        self._mxid_cache: Dict[str, str] = {}
        self._cache_timestamps: Dict[str, float] = {}
        self._mxid_cache_timestamps: Dict[str, float] = {}
        self._cache_ttl_seconds = int(os.getenv("IDENTITY_CACHE_TTL_SECONDS", "300"))
        self._max_cache_entries = int(os.getenv("IDENTITY_CACHE_MAX_ENTRIES", "5000"))
        self._initialized = True
        logger.info("IdentityStorageService initialized")

    def _now(self) -> float:
        return time.time()

    def _is_fresh(self, timestamp: float) -> bool:
        return (self._now() - timestamp) <= self._cache_ttl_seconds

    def _remove_cache_entry(self, identity_id: str) -> None:
        cached = self._cache.pop(identity_id, None)
        self._cache_timestamps.pop(identity_id, None)
        if cached is not None:
            mxid = str(cached.mxid)
            self._mxid_cache.pop(mxid, None)
            self._mxid_cache_timestamps.pop(mxid, None)

    def _set_cache_entry(self, identity: Identity) -> None:
        identity_id = str(identity.id)
        mxid = str(identity.mxid)
        now = self._now()
        self._cache[identity_id] = identity
        self._cache_timestamps[identity_id] = now
        self._mxid_cache[mxid] = identity_id
        self._mxid_cache_timestamps[mxid] = now
        self._prune_cache()

    def _prune_cache(self) -> None:
        now = self._now()
        expired_ids = [
            identity_id
            for identity_id, timestamp in self._cache_timestamps.items()
            if (now - timestamp) > self._cache_ttl_seconds
        ]
        for identity_id in expired_ids:
            self._remove_cache_entry(identity_id)

        if len(self._cache) <= self._max_cache_entries:
            return

        ordered = sorted(self._cache_timestamps.items(), key=lambda item: item[1])
        excess = len(self._cache) - self._max_cache_entries
        for identity_id, _ in ordered[:excess]:
            self._remove_cache_entry(identity_id)
    
    def initialize_tables(self) -> None:
        engine = get_engine()
        Base.metadata.create_all(engine)
        logger.info("Identity tables created/verified")
    
    def get(self, identity_id: str) -> Optional[Identity]:
        if identity_id in self._cache:
            timestamp = self._cache_timestamps.get(identity_id)
            if timestamp is not None and self._is_fresh(timestamp):
                return self._cache[identity_id]
            self._remove_cache_entry(identity_id)
        identity = self._db.get_by_id(identity_id)
        if identity:
            self._set_cache_entry(identity)
        return identity
    
    def get_by_mxid(self, mxid: str) -> Optional[Identity]:
        if mxid in self._mxid_cache:
            timestamp = self._mxid_cache_timestamps.get(mxid)
            if timestamp is not None and self._is_fresh(timestamp):
                return self.get(self._mxid_cache[mxid])
            stale_identity_id = self._mxid_cache.get(mxid)
            if stale_identity_id is not None:
                self._remove_cache_entry(stale_identity_id)
        identity = self._db.get_by_mxid(mxid)
        if identity:
            self._set_cache_entry(identity)
        return identity
    
    def get_by_agent_id(self, agent_id: str) -> Optional[Identity]:
        identity_id = f"letta_{agent_id}"
        return self.get(identity_id)
    
    def get_by_type(self, identity_type: str) -> List[Identity]:
        return self._db.get_by_type(identity_type)
    
    def get_all(self, active_only: bool = True) -> List[Identity]:
        return self._db.get_all(active_only=active_only)
    
    def create(
        self,
        identity_id: str,
        identity_type: str,
        mxid: str,
        access_token: str,
        display_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        password_hash: Optional[str] = None,
        device_id: Optional[str] = None
    ) -> Identity:
        identity = self._db.create(
            identity_id=identity_id,
            identity_type=identity_type,
            mxid=mxid,
            access_token=access_token,
            display_name=display_name,
            avatar_url=avatar_url,
            password_hash=password_hash,
            device_id=device_id
        )
        self._set_cache_entry(identity)
        logger.info(f"Created identity: {identity_id} -> {mxid}")
        return identity
    
    def update(self, identity_id: str, **kwargs) -> Optional[Identity]:
        identity = self._db.update(identity_id, **kwargs)
        if identity:
            self._set_cache_entry(identity)
            logger.info(f"Updated identity: {identity_id}")
        return identity
    
    def update_access_token(self, identity_id: str, access_token: str) -> Optional[Identity]:
        return self.update(identity_id, access_token=access_token, last_used_at=datetime.utcnow())
    
    def mark_used(self, identity_id: str) -> Optional[Identity]:
        identity = self._db.update_last_used(identity_id)
        if identity:
            self._set_cache_entry(identity)
        return identity
    
    def deactivate(self, identity_id: str) -> bool:
        identity = self._cache.get(identity_id) or self._db.get_by_id(identity_id)
        result = self._db.deactivate(identity_id)
        if result:
            self._remove_cache_entry(identity_id)
            if identity is not None:
                self._mxid_cache.pop(str(identity.mxid), None)
                self._mxid_cache_timestamps.pop(str(identity.mxid), None)
        return result
    
    def delete(self, identity_id: str) -> bool:
        identity = self._cache.get(identity_id) or self._db.get_by_id(identity_id)
        result = self._db.delete(identity_id)
        if result:
            self._remove_cache_entry(identity_id)
            if identity:
                self._mxid_cache.pop(str(identity.mxid), None)
                self._mxid_cache_timestamps.pop(str(identity.mxid), None)
            logger.info(f"Deleted identity: {identity_id}")
        return result
    
    def clear_cache(self) -> None:
        self._cache.clear()
        self._mxid_cache.clear()
        self._cache_timestamps.clear()
        self._mxid_cache_timestamps.clear()
    
    def export_all(self) -> Dict:
        return self._db.export_to_dict()


class DMRoomStorageService:
    _instance: Optional['DMRoomStorageService'] = None
    _initialized: bool = False
    
    def __new__(cls) -> 'DMRoomStorageService':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._db = DMRoomDB()
        self._cache: Dict[str, DMRoom] = {}
        self._initialized = True
        logger.info("DMRoomStorageService initialized")
    
    def _cache_key(self, mxid1: str, mxid2: str) -> str:
        return DMRoom.create_key(mxid1, mxid2)
    
    def get(self, mxid1: str, mxid2: str) -> Optional[DMRoom]:
        key = self._cache_key(mxid1, mxid2)
        if key in self._cache:
            return self._cache[key]
        dm_room = self._db.get_by_participants(mxid1, mxid2)
        if dm_room:
            self._cache[key] = dm_room
        return dm_room
    
    def get_by_room_id(self, room_id: str) -> Optional[DMRoom]:
        return self._db.get_by_room_id(room_id)
    
    def get_for_user(self, mxid: str) -> List[DMRoom]:
        return self._db.get_for_user(mxid)
    
    def get_all(self) -> List[DMRoom]:
        return self._db.get_all()
    
    def create(self, room_id: str, mxid1: str, mxid2: str) -> DMRoom:
        dm_room = self._db.create(room_id, mxid1, mxid2)
        key = self._cache_key(mxid1, mxid2)
        self._cache[key] = dm_room
        logger.info(f"Created DM room: {room_id} for {mxid1} <-> {mxid2}")
        return dm_room
    
    def get_or_create(self, room_id: str, mxid1: str, mxid2: str) -> DMRoom:
        existing = self.get(mxid1, mxid2)
        if existing:
            return existing
        return self.create(room_id, mxid1, mxid2)
    
    def update_activity(self, mxid1: str, mxid2: str) -> Optional[DMRoom]:
        dm_room = self._db.update_activity(mxid1, mxid2)
        if dm_room:
            key = self._cache_key(mxid1, mxid2)
            self._cache[key] = dm_room
        return dm_room
    
    def delete(self, mxid1: str, mxid2: str) -> bool:
        result = self._db.delete(mxid1, mxid2)
        if result:
            key = self._cache_key(mxid1, mxid2)
            self._cache.pop(key, None)
            logger.info(f"Deleted DM room for {mxid1} <-> {mxid2}")
        return result
    
    def clear_cache(self) -> None:
        self._cache.clear()
    
    def export_all(self) -> Dict:
        return self._db.export_to_dict()


_identity_service: Optional[IdentityStorageService] = None
_dm_room_service: Optional[DMRoomStorageService] = None


def get_identity_service() -> IdentityStorageService:
    global _identity_service
    if _identity_service is None:
        _identity_service = IdentityStorageService()
    return _identity_service


def get_dm_room_service() -> DMRoomStorageService:
    global _dm_room_service
    if _dm_room_service is None:
        _dm_room_service = DMRoomStorageService()
    return _dm_room_service
