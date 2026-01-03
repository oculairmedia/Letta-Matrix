"""
Identity Storage Service - centralized identity management for Matrix identities.

Provides CRUD operations for Matrix identities and DM room mappings,
replacing the TypeScript JSON-based storage with PostgreSQL.
"""
from datetime import datetime
from typing import Optional, List, Dict
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
        self._initialized = True
        logger.info("IdentityStorageService initialized")
    
    def initialize_tables(self) -> None:
        engine = get_engine()
        Base.metadata.create_all(engine)
        logger.info("Identity tables created/verified")
    
    def get(self, identity_id: str) -> Optional[Identity]:
        if identity_id in self._cache:
            return self._cache[identity_id]
        identity = self._db.get_by_id(identity_id)
        if identity:
            self._cache[identity_id] = identity
            self._mxid_cache[identity.mxid] = identity_id
        return identity
    
    def get_by_mxid(self, mxid: str) -> Optional[Identity]:
        if mxid in self._mxid_cache:
            return self.get(self._mxid_cache[mxid])
        identity = self._db.get_by_mxid(mxid)
        if identity:
            self._cache[identity.id] = identity
            self._mxid_cache[mxid] = identity.id
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
        self._cache[identity_id] = identity
        self._mxid_cache[mxid] = identity_id
        logger.info(f"Created identity: {identity_id} -> {mxid}")
        return identity
    
    def update(self, identity_id: str, **kwargs) -> Optional[Identity]:
        identity = self._db.update(identity_id, **kwargs)
        if identity:
            self._cache[identity_id] = identity
            self._mxid_cache[identity.mxid] = identity_id
            logger.info(f"Updated identity: {identity_id}")
        return identity
    
    def update_access_token(self, identity_id: str, access_token: str) -> Optional[Identity]:
        return self.update(identity_id, access_token=access_token, last_used_at=datetime.utcnow())
    
    def mark_used(self, identity_id: str) -> Optional[Identity]:
        return self._db.update_last_used(identity_id)
    
    def deactivate(self, identity_id: str) -> bool:
        result = self._db.deactivate(identity_id)
        if result and identity_id in self._cache:
            del self._cache[identity_id]
        return result
    
    def delete(self, identity_id: str) -> bool:
        identity = self._cache.get(identity_id) or self._db.get_by_id(identity_id)
        result = self._db.delete(identity_id)
        if result:
            self._cache.pop(identity_id, None)
            if identity:
                self._mxid_cache.pop(identity.mxid, None)
            logger.info(f"Deleted identity: {identity_id}")
        return result
    
    def clear_cache(self) -> None:
        self._cache.clear()
        self._mxid_cache.clear()
    
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
