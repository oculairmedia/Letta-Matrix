"""
SQLAlchemy models for Letta Conversations API integration.

This module defines the database schema for tracking room-to-conversation
mappings for the Letta 0.16.2 Conversations API.

Tables:
- room_conversations: Maps Matrix rooms to Letta conversation IDs per agent
- inter_agent_conversations: Tracks conversations between agents for @mention routing
"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column, String, DateTime, Integer, Text,
    Index, UniqueConstraint
)
from sqlalchemy.orm import Session
from .agent_mapping import Base, get_session_maker


class RoomConversation(Base):
    """
    Room-to-conversation mapping table.
    
    Maps each (room_id, agent_id) pair to a Letta conversation_id.
    This enables context isolation: messages in Room A don't appear
    in Room B's agent context.
    
    Strategy field supports future per-user isolation in DMs:
    - 'per-room': One conversation per room (default for group rooms)
    - 'per-user': One conversation per user in DMs
    """
    __tablename__ = 'room_conversations'

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String(255), nullable=False)
    agent_id = Column(String(255), nullable=False)
    conversation_id = Column(String(255), nullable=False)
    strategy = Column(String(50), default='per-room', nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_message_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    
    user_mxid = Column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint('room_id', 'agent_id', 'user_mxid', name='uq_room_agent_user'),
        Index('idx_room_conv_room_id', 'room_id'),
        Index('idx_room_conv_agent_id', 'agent_id'),
        Index('idx_room_conv_last_msg', 'last_message_at'),
        Index('idx_room_conv_conversation_id', 'conversation_id'),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary format."""
        return {
            "id": self.id,
            "room_id": self.room_id,
            "agent_id": self.agent_id,
            "conversation_id": self.conversation_id,
            "strategy": self.strategy,
            "user_mxid": self.user_mxid,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
        }

    def __repr__(self):
        return (
            f"<RoomConversation(room_id={self.room_id}, agent_id={self.agent_id}, "
            f"conversation_id={self.conversation_id}, strategy={self.strategy})>"
        )


class InterAgentConversation(Base):
    """
    Inter-agent conversation tracking table.
    
    When Agent A mentions @AgentB, we create a separate conversation
    for that A->B communication context. This prevents inter-agent
    chatter from polluting room conversations.
    
    The user_mxid field tracks the original human user who triggered
    the inter-agent conversation (for context and attribution).
    """
    __tablename__ = 'inter_agent_conversations'

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_agent_id = Column(String(255), nullable=False)
    target_agent_id = Column(String(255), nullable=False)
    room_id = Column(String(255), nullable=False)
    conversation_id = Column(String(255), nullable=False)
    user_mxid = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_message_at = Column(DateTime, default=datetime.utcnow, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            'source_agent_id', 'target_agent_id', 'room_id', 'user_mxid',
            name='uq_inter_agent_conv'
        ),
        Index('idx_inter_agent_source', 'source_agent_id'),
        Index('idx_inter_agent_target', 'target_agent_id'),
        Index('idx_inter_agent_room', 'room_id'),
        Index('idx_inter_agent_last_msg', 'last_message_at'),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary format."""
        return {
            "id": self.id,
            "source_agent_id": self.source_agent_id,
            "target_agent_id": self.target_agent_id,
            "room_id": self.room_id,
            "conversation_id": self.conversation_id,
            "user_mxid": self.user_mxid,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
        }

    def __repr__(self):
        return (
            f"<InterAgentConversation(source={self.source_agent_id}, "
            f"target={self.target_agent_id}, room={self.room_id})>"
        )


class RoomConversationDB:
    """Helper class for database operations on room conversations."""

    def __init__(self):
        self.Session = get_session_maker()

    def get_by_room_and_agent(
        self,
        room_id: str,
        agent_id: str,
        user_mxid: Optional[str] = None
    ) -> Optional[RoomConversation]:
        """
        Get conversation for a room/agent pair.
        
        Args:
            room_id: Matrix room ID
            agent_id: Letta agent ID
            user_mxid: Optional user MXID for per-user strategy
            
        Returns:
            RoomConversation or None if not found
        """
        session = self.Session()
        try:
            session.expire_all()
            conv = session.query(RoomConversation).filter_by(
                room_id=room_id,
                agent_id=agent_id,
                user_mxid=user_mxid
            ).first()
            if conv:
                session.expunge(conv)
            return conv
        finally:
            session.close()

    def get_by_conversation_id(self, conversation_id: str) -> Optional[RoomConversation]:
        """Get by Letta conversation ID."""
        session = self.Session()
        try:
            session.expire_all()
            conv = session.query(RoomConversation).filter_by(
                conversation_id=conversation_id
            ).first()
            if conv:
                session.expunge(conv)
            return conv
        finally:
            session.close()

    def get_all_for_agent(self, agent_id: str) -> List[RoomConversation]:
        """Get all conversations for an agent."""
        session = self.Session()
        try:
            session.expire_all()
            convs = session.query(RoomConversation).filter_by(
                agent_id=agent_id
            ).all()
            for conv in convs:
                session.expunge(conv)
            return convs
        finally:
            session.close()

    def get_all_for_room(self, room_id: str) -> List[RoomConversation]:
        """Get all conversations for a room (across all agents)."""
        session = self.Session()
        try:
            session.expire_all()
            convs = session.query(RoomConversation).filter_by(
                room_id=room_id
            ).all()
            for conv in convs:
                session.expunge(conv)
            return convs
        finally:
            session.close()

    def create(
        self,
        room_id: str,
        agent_id: str,
        conversation_id: str,
        strategy: str = 'per-room',
        user_mxid: Optional[str] = None
    ) -> RoomConversation:
        """
        Create a new room conversation mapping.
        
        Args:
            room_id: Matrix room ID
            agent_id: Letta agent ID
            conversation_id: Letta conversation ID
            strategy: 'per-room' or 'per-user'
            user_mxid: User MXID (required for per-user strategy)
            
        Returns:
            Created RoomConversation
        """
        session = self.Session()
        try:
            conv = RoomConversation(
                room_id=room_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                strategy=strategy,
                user_mxid=user_mxid,
                last_message_at=datetime.utcnow()
            )
            session.add(conv)
            session.commit()
            session.refresh(conv)
            session.expunge(conv)
            return conv
        finally:
            session.close()

    def get_or_create(
        self,
        room_id: str,
        agent_id: str,
        conversation_id: str,
        strategy: str = 'per-room',
        user_mxid: Optional[str] = None
    ) -> tuple[RoomConversation, bool]:
        """
        Get existing or create new room conversation mapping.
        
        Args:
            room_id: Matrix room ID
            agent_id: Letta agent ID
            conversation_id: Letta conversation ID (used only for creation)
            strategy: 'per-room' or 'per-user'
            user_mxid: User MXID (required for per-user strategy)
            
        Returns:
            Tuple of (RoomConversation, created: bool)
        """
        existing = self.get_by_room_and_agent(room_id, agent_id, user_mxid)
        if existing:
            return existing, False
        
        created = self.create(
            room_id=room_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            strategy=strategy,
            user_mxid=user_mxid
        )
        return created, True

    def update_last_message(
        self,
        room_id: str,
        agent_id: str,
        user_mxid: Optional[str] = None
    ) -> Optional[RoomConversation]:
        """Update last_message_at timestamp."""
        session = self.Session()
        try:
            conv = session.query(RoomConversation).filter_by(
                room_id=room_id,
                agent_id=agent_id,
                user_mxid=user_mxid
            ).first()
            if not conv:
                return None
            
            conv.last_message_at = datetime.utcnow()
            session.commit()
            session.refresh(conv)
            session.expunge(conv)
            return conv
        finally:
            session.close()

    def delete(
        self,
        room_id: str,
        agent_id: str,
        user_mxid: Optional[str] = None
    ) -> bool:
        """Delete a room conversation mapping."""
        session = self.Session()
        try:
            conv = session.query(RoomConversation).filter_by(
                room_id=room_id,
                agent_id=agent_id,
                user_mxid=user_mxid
            ).first()
            if not conv:
                return False
            
            session.delete(conv)
            session.commit()
            return True
        finally:
            session.close()

    def delete_stale(self, days: int = 30) -> int:
        """
        Delete conversations with no activity for N days.
        
        Args:
            days: Number of days of inactivity before deletion
            
        Returns:
            Number of deleted records
        """
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        session = self.Session()
        try:
            count = session.query(RoomConversation).filter(
                RoomConversation.last_message_at < cutoff
            ).delete()
            session.commit()
            return count
        finally:
            session.close()


class InterAgentConversationDB:
    """Helper class for database operations on inter-agent conversations."""

    def __init__(self):
        self.Session = get_session_maker()

    def get(
        self,
        source_agent_id: str,
        target_agent_id: str,
        room_id: str,
        user_mxid: Optional[str] = None
    ) -> Optional[InterAgentConversation]:
        """
        Get inter-agent conversation.
        
        Args:
            source_agent_id: Agent initiating the conversation
            target_agent_id: Agent being mentioned/contacted
            room_id: Room where the mention occurred
            user_mxid: Original user who triggered the conversation
            
        Returns:
            InterAgentConversation or None
        """
        session = self.Session()
        try:
            session.expire_all()
            conv = session.query(InterAgentConversation).filter_by(
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
                room_id=room_id,
                user_mxid=user_mxid
            ).first()
            if conv:
                session.expunge(conv)
            return conv
        finally:
            session.close()

    def get_by_conversation_id(self, conversation_id: str) -> Optional[InterAgentConversation]:
        """Get by Letta conversation ID."""
        session = self.Session()
        try:
            session.expire_all()
            conv = session.query(InterAgentConversation).filter_by(
                conversation_id=conversation_id
            ).first()
            if conv:
                session.expunge(conv)
            return conv
        finally:
            session.close()

    def get_all_for_agent(self, agent_id: str) -> List[InterAgentConversation]:
        """Get all inter-agent conversations where agent is source or target."""
        session = self.Session()
        try:
            session.expire_all()
            convs = session.query(InterAgentConversation).filter(
                (InterAgentConversation.source_agent_id == agent_id) |
                (InterAgentConversation.target_agent_id == agent_id)
            ).all()
            for conv in convs:
                session.expunge(conv)
            return convs
        finally:
            session.close()

    def create(
        self,
        source_agent_id: str,
        target_agent_id: str,
        room_id: str,
        conversation_id: str,
        user_mxid: Optional[str] = None
    ) -> InterAgentConversation:
        """Create a new inter-agent conversation mapping."""
        session = self.Session()
        try:
            conv = InterAgentConversation(
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
                room_id=room_id,
                conversation_id=conversation_id,
                user_mxid=user_mxid,
                last_message_at=datetime.utcnow()
            )
            session.add(conv)
            session.commit()
            session.refresh(conv)
            session.expunge(conv)
            return conv
        finally:
            session.close()

    def get_or_create(
        self,
        source_agent_id: str,
        target_agent_id: str,
        room_id: str,
        conversation_id: str,
        user_mxid: Optional[str] = None
    ) -> tuple[InterAgentConversation, bool]:
        """
        Get existing or create new inter-agent conversation.
        
        Returns:
            Tuple of (InterAgentConversation, created: bool)
        """
        existing = self.get(source_agent_id, target_agent_id, room_id, user_mxid)
        if existing:
            return existing, False
        
        created = self.create(
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            room_id=room_id,
            conversation_id=conversation_id,
            user_mxid=user_mxid
        )
        return created, True

    def update_last_message(
        self,
        source_agent_id: str,
        target_agent_id: str,
        room_id: str,
        user_mxid: Optional[str] = None
    ) -> Optional[InterAgentConversation]:
        """Update last_message_at timestamp."""
        session = self.Session()
        try:
            conv = session.query(InterAgentConversation).filter_by(
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
                room_id=room_id,
                user_mxid=user_mxid
            ).first()
            if not conv:
                return None
            
            conv.last_message_at = datetime.utcnow()
            session.commit()
            session.refresh(conv)
            session.expunge(conv)
            return conv
        finally:
            session.close()

    def delete(
        self,
        source_agent_id: str,
        target_agent_id: str,
        room_id: str,
        user_mxid: Optional[str] = None
    ) -> bool:
        """Delete an inter-agent conversation mapping."""
        session = self.Session()
        try:
            conv = session.query(InterAgentConversation).filter_by(
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
                room_id=room_id,
                user_mxid=user_mxid
            ).first()
            if not conv:
                return False
            
            session.delete(conv)
            session.commit()
            return True
        finally:
            session.close()

    def delete_stale(self, days: int = 30) -> int:
        """
        Delete inter-agent conversations with no activity for N days.
        
        Args:
            days: Number of days of inactivity before deletion
            
        Returns:
            Number of deleted records
        """
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        session = self.Session()
        try:
            count = session.query(InterAgentConversation).filter(
                InterAgentConversation.last_message_at < cutoff
            ).delete()
            session.commit()
            return count
        finally:
            session.close()
