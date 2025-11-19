"""
SQLAlchemy models for agent mappings database storage.
"""
from datetime import datetime
from typing import Optional, Dict, List
from sqlalchemy import create_engine, Column, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session, joinedload
from sqlalchemy.pool import StaticPool
import os

Base = declarative_base()


class AgentMapping(Base):
    """Main agent mapping table - stores Matrix user and room info for each Letta agent"""
    __tablename__ = 'agent_mappings'

    agent_id = Column(String, primary_key=True)
    agent_name = Column(String, nullable=False)
    matrix_user_id = Column(String, unique=True, nullable=False)
    matrix_password = Column(String, nullable=False)
    room_id = Column(String, unique=True, nullable=True)
    room_created = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship to invitation status
    invitations = relationship("InvitationStatus", back_populates="agent", cascade="all, delete-orphan")

    # Indexes for common queries
    __table_args__ = (
        Index('idx_room_id', 'room_id'),
        Index('idx_agent_name', 'agent_name'),
        Index('idx_matrix_user', 'matrix_user_id'),
    )

    def to_dict(self) -> Dict:
        """Convert to dictionary format (compatible with old JSON structure)"""
        invitation_status = {inv.invitee: inv.status for inv in self.invitations}
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "matrix_user_id": self.matrix_user_id,
            "matrix_password": self.matrix_password,
            "room_id": self.room_id,
            "room_created": self.room_created,
            "created": True,  # Backward compatibility
            "invitation_status": invitation_status
        }


class InvitationStatus(Base):
    """Tracks invitation status for each agent-invitee pair"""
    __tablename__ = 'invitation_status'

    agent_id = Column(String, ForeignKey('agent_mappings.agent_id', ondelete='CASCADE'), primary_key=True)
    invitee = Column(String, primary_key=True)
    status = Column(String, nullable=False)  # 'joined', 'failed', 'pending'

    # Relationship back to agent
    agent = relationship("AgentMapping", back_populates="invitations")


# Database connection setup
_engine = None  # Singleton engine instance

def get_database_url() -> str:
    """Get database URL from environment or use default"""
    return os.environ.get(
        'DATABASE_URL',
        'postgresql://postgres:your-super-secret-and-long-postgres-password@synapse-db:5432/matrix_letta'
    )


def get_engine():
    """Get SQLAlchemy engine with SQLite or PostgreSQL support (singleton)"""
    global _engine
    
    if _engine is None:
        url = get_database_url()

        # SQLite configuration (for testing)
        if url.startswith('sqlite'):
            _engine = create_engine(
                url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
                echo=False  # Set to True for SQL debugging
            )
        else:
            # PostgreSQL configuration (for production)
            # Use pool_pre_ping to verify connections are alive
            # Disable query caching with expire_on_commit=False in sessionmaker
            _engine = create_engine(
                url, 
                pool_pre_ping=True, 
                pool_size=10, 
                max_overflow=20,
                pool_recycle=3600,  # Recycle connections after 1 hour
                isolation_level="READ COMMITTED"  # Ensure we see committed changes
            )
    
    return _engine


def get_session_maker():
    """Get session maker for creating database sessions"""
    engine = get_engine()
    # expire_on_commit=False prevents stale data after commit
    # autoflush=True ensures changes are flushed before queries
    return sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=True,
        autocommit=False
    )


def get_db_session() -> Session:
    """Get a new database session"""
    SessionLocal = get_session_maker()
    return SessionLocal()


def init_database():
    """Initialize database tables"""
    engine = get_engine()
    Base.metadata.create_all(engine)


# Database operations helper class
class AgentMappingDB:
    """Helper class for database operations on agent mappings"""

    def __init__(self):
        self.Session = get_session_maker()

    def get_by_agent_id(self, agent_id: str) -> Optional[AgentMapping]:
        """Get mapping by agent ID"""
        session = self.Session()
        try:
            # Expire all cached data to ensure fresh read
            session.expire_all()
            mapping = session.query(AgentMapping).options(
                joinedload(AgentMapping.invitations)
            ).filter_by(agent_id=agent_id).first()
            if mapping:
                session.expunge(mapping)
            return mapping
        finally:
            session.close()

    def get_by_room_id(self, room_id: str) -> Optional[AgentMapping]:
        """Get mapping by room ID - used for routing messages"""
        session = self.Session()
        try:
            # Expire all cached data to ensure fresh read
            session.expire_all()
            mapping = session.query(AgentMapping).options(
                joinedload(AgentMapping.invitations)
            ).filter_by(room_id=room_id).first()
            if mapping:
                session.expunge(mapping)
            return mapping
        finally:
            session.close()

    def get_by_matrix_user(self, matrix_user_id: str) -> Optional[AgentMapping]:
        """Get mapping by Matrix user ID"""
        session = self.Session()
        try:
            # Expire all cached data to ensure fresh read
            session.expire_all()
            mapping = session.query(AgentMapping).options(
                joinedload(AgentMapping.invitations)
            ).filter_by(matrix_user_id=matrix_user_id).first()
            if mapping:
                session.expunge(mapping)
            return mapping
        finally:
            session.close()

    def get_all(self) -> List[AgentMapping]:
        """Get all mappings"""
        session = self.Session()
        try:
            # Expire all cached data to ensure fresh read
            session.expire_all()
            mappings = session.query(AgentMapping).options(
                joinedload(AgentMapping.invitations)
            ).all()
            # Expunge all mappings so they can be used after session closes
            for mapping in mappings:
                session.expunge(mapping)
            return mappings
        finally:
            session.close()

    def create(self, agent_id: str, agent_name: str, matrix_user_id: str,
               matrix_password: str, room_id: Optional[str] = None,
               room_created: bool = False) -> AgentMapping:
        """Create a new mapping"""
        session = self.Session()
        try:
            mapping = AgentMapping(
                agent_id=agent_id,
                agent_name=agent_name,
                matrix_user_id=matrix_user_id,
                matrix_password=matrix_password,
                room_id=room_id,
                room_created=room_created
            )
            session.add(mapping)
            session.commit()
            session.refresh(mapping)
            session.expunge(mapping)
            return mapping
        finally:
            session.close()

    def update(self, agent_id: str, **kwargs) -> Optional[AgentMapping]:
        """Update a mapping"""
        session = self.Session()
        try:
            mapping = session.query(AgentMapping).options(
                joinedload(AgentMapping.invitations)
            ).filter_by(agent_id=agent_id).first()
            if not mapping:
                return None

            for key, value in kwargs.items():
                if hasattr(mapping, key):
                    setattr(mapping, key, value)

            mapping.updated_at = datetime.utcnow()
            session.commit()
            session.refresh(mapping)
            session.expunge(mapping)
            return mapping
        finally:
            session.close()

    def upsert(self, agent_id: str, agent_name: str, matrix_user_id: str,
               matrix_password: str, room_id: Optional[str] = None,
               room_created: bool = False) -> AgentMapping:
        """Create or update a mapping (upsert operation)"""
        session = self.Session()
        try:
            # Try to find existing mapping
            mapping = session.query(AgentMapping).options(
                joinedload(AgentMapping.invitations)
            ).filter_by(agent_id=agent_id).first()

            if mapping:
                # Update existing mapping
                mapping.agent_name = agent_name
                mapping.matrix_user_id = matrix_user_id
                mapping.matrix_password = matrix_password
                mapping.room_id = room_id
                mapping.room_created = room_created
                mapping.updated_at = datetime.utcnow()
            else:
                # Create new mapping
                mapping = AgentMapping(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    matrix_user_id=matrix_user_id,
                    matrix_password=matrix_password,
                    room_id=room_id,
                    room_created=room_created
                )
                session.add(mapping)

            session.commit()
            session.refresh(mapping)
            session.expunge(mapping)
            return mapping
        finally:
            session.close()

    def delete(self, agent_id: str) -> bool:
        """Delete a mapping"""
        session = self.Session()
        try:
            mapping = session.query(AgentMapping).filter_by(agent_id=agent_id).first()
            if not mapping:
                return False

            session.delete(mapping)
            session.commit()
            return True
        finally:
            session.close()

    def update_invitation_status(self, agent_id: str, invitee: str, status: str):
        """Update or create invitation status"""
        session = self.Session()
        try:
            inv = session.query(InvitationStatus).filter_by(
                agent_id=agent_id, invitee=invitee
            ).first()

            if inv:
                inv.status = status
            else:
                inv = InvitationStatus(agent_id=agent_id, invitee=invitee, status=status)
                session.add(inv)

            session.commit()
        finally:
            session.close()

    def export_to_dict(self) -> Dict:
        """Export all mappings to dictionary format (compatible with old JSON)"""
        mappings = self.get_all()
        return {
            mapping.agent_id: mapping.to_dict()
            for mapping in mappings
        }
