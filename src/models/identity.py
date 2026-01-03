"""
SQLAlchemy models for identity management database storage.

This module defines the database schema for Matrix identities and DM rooms,
replacing the TypeScript JSON-based storage.
"""
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, Text,
    Index, UniqueConstraint, ForeignKey
)
from sqlalchemy.orm import relationship, Session
from .agent_mapping import Base, get_db_session, get_session_maker


class Identity(Base):
    """
    Matrix identity table - stores user credentials and metadata.
    
    Maps to TypeScript MatrixIdentity interface.
    """
    __tablename__ = 'identities'

    id = Column(String(255), primary_key=True)
    identity_type = Column(String(50), nullable=False)
    mxid = Column(String(255), unique=True, nullable=False)
    display_name = Column(String(255), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    access_token = Column(Text, nullable=False)
    password_hash = Column(Text, nullable=True)
    device_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    __table_args__ = (
        Index('idx_identities_type', 'identity_type'),
        Index('idx_identities_mxid', 'mxid'),
        Index('idx_identities_active', 'is_active'),
    )

    def to_dict(self) -> Dict:
        """
        Convert to dictionary format compatible with TypeScript MatrixIdentity.
        
        Returns timestamps as milliseconds since epoch for compatibility.
        """
        return {
            "id": self.id,
            "mxid": self.mxid,
            "displayName": self.display_name,
            "avatarUrl": self.avatar_url,
            "accessToken": self.access_token,
            "type": self.identity_type,
            "createdAt": int(self.created_at.timestamp() * 1000) if self.created_at else None,
            "lastUsedAt": int(self.last_used_at.timestamp() * 1000) if self.last_used_at else None,
            "isActive": self.is_active
        }

    def __repr__(self):
        return f"<Identity(id={self.id}, mxid={self.mxid}, type={self.identity_type})>"


class DMRoom(Base):
    """
    DM room mapping table - stores direct message room associations.
    
    Maps to TypeScript DMRoomMapping interface.
    Participants are stored alphabetically sorted for consistent lookups.
    """
    __tablename__ = 'dm_rooms'

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String(255), unique=True, nullable=False)
    participant_1 = Column(String(255), nullable=False)
    participant_2 = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_activity_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('participant_1', 'participant_2', name='uq_participants'),
        Index('idx_dm_participant_1', 'participant_1'),
        Index('idx_dm_participant_2', 'participant_2'),
        Index('idx_dm_room_id', 'room_id'),
    )

    @staticmethod
    def create_key(mxid1: str, mxid2: str) -> str:
        """
        Create consistent key from two MXIDs (alphabetically sorted).
        Compatible with TypeScript implementation: "mxid1<->mxid2"
        """
        sorted_mxids = sorted([mxid1, mxid2])
        return f"{sorted_mxids[0]}<->{sorted_mxids[1]}"

    @staticmethod
    def sort_participants(mxid1: str, mxid2: str) -> Tuple[str, str]:
        """
        Sort two MXIDs alphabetically.
        Returns (participant_1, participant_2) for database storage.
        """
        sorted_mxids = sorted([mxid1, mxid2])
        return sorted_mxids[0], sorted_mxids[1]

    def to_dict(self) -> Dict:
        """
        Convert to dictionary format compatible with TypeScript DMRoomMapping.
        
        Returns timestamps as milliseconds since epoch for compatibility.
        """
        return {
            "key": self.create_key(self.participant_1, self.participant_2),
            "roomId": self.room_id,
            "participants": [self.participant_1, self.participant_2],
            "createdAt": int(self.created_at.timestamp() * 1000) if self.created_at else None,
            "lastActivityAt": int(self.last_activity_at.timestamp() * 1000) if self.last_activity_at else None
        }

    def __repr__(self):
        return f"<DMRoom(room_id={self.room_id}, participants={self.participant_1}, {self.participant_2})>"


class IdentityDB:

    def __init__(self):
        self.Session = get_session_maker()

    def get_by_id(self, identity_id: str) -> Optional[Identity]:
        session = self.Session()
        try:
            session.expire_all()
            identity = session.query(Identity).filter_by(id=identity_id).first()
            if identity:
                session.expunge(identity)
            return identity
        finally:
            session.close()

    def get_by_mxid(self, mxid: str) -> Optional[Identity]:
        session = self.Session()
        try:
            session.expire_all()
            identity = session.query(Identity).filter_by(mxid=mxid).first()
            if identity:
                session.expunge(identity)
            return identity
        finally:
            session.close()

    def get_by_type(self, identity_type: str) -> List[Identity]:
        session = self.Session()
        try:
            session.expire_all()
            identities = session.query(Identity).filter_by(identity_type=identity_type).all()
            for identity in identities:
                session.expunge(identity)
            return identities
        finally:
            session.close()

    def get_all(self, active_only: bool = False) -> List[Identity]:
        session = self.Session()
        try:
            session.expire_all()
            query = session.query(Identity)
            if active_only:
                query = query.filter_by(is_active=True)
            identities = query.all()
            for identity in identities:
                session.expunge(identity)
            return identities
        finally:
            session.close()

    def create(self, identity_id: str, identity_type: str, mxid: str,
               access_token: str, display_name: Optional[str] = None,
               avatar_url: Optional[str] = None, password_hash: Optional[str] = None,
               device_id: Optional[str] = None) -> Identity:
        session = self.Session()
        try:
            identity = Identity(
                id=identity_id,
                identity_type=identity_type,
                mxid=mxid,
                display_name=display_name,
                avatar_url=avatar_url,
                access_token=access_token,
                password_hash=password_hash,
                device_id=device_id,
                last_used_at=datetime.utcnow()
            )
            session.add(identity)
            session.commit()
            session.refresh(identity)
            session.expunge(identity)
            return identity
        finally:
            session.close()

    def update(self, identity_id: str, **kwargs) -> Optional[Identity]:
        session = self.Session()
        try:
            identity = session.query(Identity).filter_by(id=identity_id).first()
            if not identity:
                return None

            for key, value in kwargs.items():
                if hasattr(identity, key):
                    setattr(identity, key, value)

            identity.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(identity)
            session.expunge(identity)
            return identity
        finally:
            session.close()

    def update_last_used(self, identity_id: str) -> Optional[Identity]:
        return self.update(identity_id, last_used_at=datetime.utcnow())

    def deactivate(self, identity_id: str) -> bool:
        result = self.update(identity_id, is_active=False)
        return result is not None

    def delete(self, identity_id: str) -> bool:
        session = self.Session()
        try:
            identity = session.query(Identity).filter_by(id=identity_id).first()
            if not identity:
                return False

            session.delete(identity)
            session.commit()
            return True
        finally:
            session.close()

    def export_to_dict(self) -> Dict:
        identities = self.get_all()
        return {
            identity.id: identity.to_dict()
            for identity in identities
        }


class DMRoomDB:

    def __init__(self):
        self.Session = get_session_maker()

    def get_by_room_id(self, room_id: str) -> Optional[DMRoom]:
        session = self.Session()
        try:
            session.expire_all()
            dm_room = session.query(DMRoom).filter_by(room_id=room_id).first()
            if dm_room:
                session.expunge(dm_room)
            return dm_room
        finally:
            session.close()

    def get_by_participants(self, mxid1: str, mxid2: str) -> Optional[DMRoom]:
        participant_1, participant_2 = DMRoom.sort_participants(mxid1, mxid2)
        session = self.Session()
        try:
            session.expire_all()
            dm_room = session.query(DMRoom).filter_by(
                participant_1=participant_1,
                participant_2=participant_2
            ).first()
            if dm_room:
                session.expunge(dm_room)
            return dm_room
        finally:
            session.close()

    def get_for_user(self, mxid: str) -> List[DMRoom]:
        session = self.Session()
        try:
            session.expire_all()
            dm_rooms = session.query(DMRoom).filter(
                (DMRoom.participant_1 == mxid) | (DMRoom.participant_2 == mxid)
            ).all()
            for dm_room in dm_rooms:
                session.expunge(dm_room)
            return dm_rooms
        finally:
            session.close()

    def get_all(self) -> List[DMRoom]:
        session = self.Session()
        try:
            session.expire_all()
            dm_rooms = session.query(DMRoom).all()
            for dm_room in dm_rooms:
                session.expunge(dm_room)
            return dm_rooms
        finally:
            session.close()

    def create(self, room_id: str, mxid1: str, mxid2: str) -> DMRoom:
        participant_1, participant_2 = DMRoom.sort_participants(mxid1, mxid2)
        session = self.Session()
        try:
            dm_room = DMRoom(
                room_id=room_id,
                participant_1=participant_1,
                participant_2=participant_2
            )
            session.add(dm_room)
            session.commit()
            session.refresh(dm_room)
            session.expunge(dm_room)
            return dm_room
        finally:
            session.close()

    def update_activity(self, mxid1: str, mxid2: str) -> Optional[DMRoom]:
        participant_1, participant_2 = DMRoom.sort_participants(mxid1, mxid2)
        session = self.Session()
        try:
            dm_room = session.query(DMRoom).filter_by(
                participant_1=participant_1,
                participant_2=participant_2
            ).first()
            if not dm_room:
                return None

            dm_room.last_activity_at = datetime.utcnow()
            session.commit()
            session.refresh(dm_room)
            session.expunge(dm_room)
            return dm_room
        finally:
            session.close()

    def delete(self, mxid1: str, mxid2: str) -> bool:
        participant_1, participant_2 = DMRoom.sort_participants(mxid1, mxid2)
        session = self.Session()
        try:
            dm_room = session.query(DMRoom).filter_by(
                participant_1=participant_1,
                participant_2=participant_2
            ).first()
            if not dm_room:
                return False

            session.delete(dm_room)
            session.commit()
            return True
        finally:
            session.close()

    def export_to_dict(self) -> Dict:
        dm_rooms = self.get_all()
        return {
            dm_room.create_key(dm_room.participant_1, dm_room.participant_2): dm_room.to_dict()
            for dm_room in dm_rooms
        }
