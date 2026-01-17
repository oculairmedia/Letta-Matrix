"""
Conversation service for Letta 0.16.2 Conversations API integration.

Manages room-to-conversation mappings for context isolation.
"""
import logging
from typing import Optional, List, Tuple
from sqlalchemy.exc import IntegrityError

from letta_client import Letta, APIError, NotFoundError

from src.models.conversation import (
    RoomConversationDB,
    InterAgentConversationDB,
    RoomConversation,
    InterAgentConversation,
)
from src.letta.client import get_letta_client

logger = logging.getLogger(__name__)

DEFAULT_ISOLATED_BLOCK_LABELS = ["room_context", "conversation_summary", "active_tasks"]


class ConversationService:
    """
    Service for managing Letta conversations per Matrix room.
    
    Handles:
    - Creating/retrieving conversations for room+agent pairs
    - Strategy detection (per-room vs per-user for DMs)
    - Race condition handling via DB constraints
    - Letta API error recovery
    """

    def __init__(
        self,
        letta_client: Optional[Letta] = None,
        isolated_block_labels: Optional[List[str]] = None,
    ):
        self.letta = letta_client or get_letta_client()
        self.isolated_block_labels = isolated_block_labels or DEFAULT_ISOLATED_BLOCK_LABELS
        self.room_conv_db = RoomConversationDB()
        self.inter_agent_db = InterAgentConversationDB()

    async def get_conversation_strategy(
        self,
        room_id: str,
        room_member_count: int,
    ) -> str:
        """
        Determine conversation strategy based on room membership.
        
        Args:
            room_id: Matrix room ID
            room_member_count: Number of members in the room
            
        Returns:
            'per-user' for DMs (2 members), 'per-room' otherwise
        """
        if room_member_count == 2:
            logger.debug(f"Room {room_id} has 2 members, using per-user strategy")
            return "per-user"
        
        logger.debug(f"Room {room_id} has {room_member_count} members, using per-room strategy")
        return "per-room"

    def _create_letta_conversation(
        self,
        agent_id: str,
        summary: Optional[str] = None,
    ) -> str:
        """
        Create a new conversation in Letta.
        
        Args:
            agent_id: Letta agent ID
            summary: Optional conversation summary
            
        Returns:
            Letta conversation ID
            
        Raises:
            APIError: If Letta API call fails
        """
        logger.info(f"Creating Letta conversation for agent {agent_id}")
        
        conversation = self.letta.conversations.create(
            agent_id=agent_id,
            isolated_block_labels=self.isolated_block_labels,
            summary=summary,
        )
        
        logger.info(f"Created Letta conversation {conversation.id} for agent {agent_id}")
        return conversation.id

    def _verify_letta_conversation(self, conversation_id: str) -> bool:
        """
        Verify a conversation still exists in Letta.
        
        Args:
            conversation_id: Letta conversation ID
            
        Returns:
            True if conversation exists, False otherwise
        """
        try:
            self.letta.conversations.retrieve(conversation_id)
            return True
        except NotFoundError:
            logger.warning(f"Letta conversation {conversation_id} not found")
            return False
        except APIError:
            raise

    async def get_or_create_room_conversation(
        self,
        room_id: str,
        agent_id: str,
        room_member_count: int = 3,
        user_mxid: Optional[str] = None,
        room_name: Optional[str] = None,
    ) -> Tuple[str, bool]:
        """
        Get or create a conversation for a room+agent pair.
        
        Handles:
        - DB lookup for existing mapping
        - Strategy detection based on room membership
        - Letta conversation creation if needed
        - Race condition handling via unique constraint
        - Stale conversation recovery (DB record exists but Letta deleted)
        
        Args:
            room_id: Matrix room ID
            agent_id: Letta agent ID
            room_member_count: Number of members in room (for strategy detection)
            user_mxid: User MXID (required for per-user strategy)
            room_name: Optional room name for conversation summary
            
        Returns:
            Tuple of (conversation_id, created: bool)
        """
        strategy = await self.get_conversation_strategy(room_id, room_member_count)
        
        lookup_user = user_mxid if strategy == "per-user" else None
        
        existing = self.room_conv_db.get_by_room_and_agent(
            room_id=room_id,
            agent_id=agent_id,
            user_mxid=lookup_user,
        )
        
        if existing:
            if self._verify_letta_conversation(existing.conversation_id):
                self.room_conv_db.update_last_message(room_id, agent_id, lookup_user)
                return existing.conversation_id, False
            else:
                logger.warning(
                    f"Stale conversation {existing.conversation_id} for room {room_id}, "
                    f"agent {agent_id} - recreating"
                )
                self.room_conv_db.delete(room_id, agent_id, lookup_user)
        
        summary = f"Matrix room: {room_name or room_id}"
        if strategy == "per-user" and user_mxid:
            summary = f"{summary} (user: {user_mxid})"
        
        try:
            conversation_id = self._create_letta_conversation(agent_id, summary)
        except APIError as e:
            logger.error(f"Failed to create Letta conversation: {e}")
            raise
        
        try:
            self.room_conv_db.create(
                room_id=room_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                strategy=strategy,
                user_mxid=lookup_user,
            )
            return conversation_id, True
            
        except IntegrityError:
            logger.info(
                f"Race condition: conversation already created for room {room_id}, "
                f"agent {agent_id} - fetching existing"
            )
            existing = self.room_conv_db.get_by_room_and_agent(
                room_id=room_id,
                agent_id=agent_id,
                user_mxid=lookup_user,
            )
            if existing:
                return existing.conversation_id, False
            raise RuntimeError(
                f"Failed to get or create conversation for room {room_id}, agent {agent_id}"
            )

    async def get_or_create_inter_agent_conversation(
        self,
        source_agent_id: str,
        target_agent_id: str,
        room_id: str,
        user_mxid: Optional[str] = None,
    ) -> Tuple[str, bool]:
        """
        Get or create a conversation for inter-agent communication.
        
        Used when one agent @mentions another agent.
        
        Args:
            source_agent_id: Agent initiating the mention
            target_agent_id: Agent being mentioned
            room_id: Room where mention occurred
            user_mxid: Original user who triggered the mention
            
        Returns:
            Tuple of (conversation_id, created: bool)
        """
        existing = self.inter_agent_db.get(
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            room_id=room_id,
            user_mxid=user_mxid,
        )
        
        if existing:
            if self._verify_letta_conversation(existing.conversation_id):
                self.inter_agent_db.update_last_message(
                    source_agent_id, target_agent_id, room_id, user_mxid
                )
                return existing.conversation_id, False
            else:
                logger.warning(
                    f"Stale inter-agent conversation {existing.conversation_id} - recreating"
                )
                self.inter_agent_db.delete(
                    source_agent_id, target_agent_id, room_id, user_mxid
                )
        
        summary = f"Inter-agent: {source_agent_id} -> {target_agent_id} in {room_id}"
        
        try:
            conversation_id = self._create_letta_conversation(target_agent_id, summary)
        except APIError as e:
            logger.error(f"Failed to create inter-agent conversation: {e}")
            raise
        
        try:
            self.inter_agent_db.create(
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
                room_id=room_id,
                conversation_id=conversation_id,
                user_mxid=user_mxid,
            )
            return conversation_id, True
            
        except IntegrityError:
            logger.info("Race condition: inter-agent conversation already created")
            existing = self.inter_agent_db.get(
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
                room_id=room_id,
                user_mxid=user_mxid,
            )
            if existing:
                return existing.conversation_id, False
            raise RuntimeError("Failed to get or create inter-agent conversation")

    def get_conversation_id(
        self,
        room_id: str,
        agent_id: str,
        user_mxid: Optional[str] = None,
    ) -> Optional[str]:
        """
        Get existing conversation ID without creating.
        
        Args:
            room_id: Matrix room ID
            agent_id: Letta agent ID
            user_mxid: Optional user MXID for per-user lookups
            
        Returns:
            Conversation ID or None if not found
        """
        existing = self.room_conv_db.get_by_room_and_agent(room_id, agent_id, user_mxid)
        return existing.conversation_id if existing else None

    def cleanup_stale_conversations(self, days: int = 30) -> Tuple[int, int]:
        """
        Delete conversations with no activity for N days.
        
        Args:
            days: Number of days of inactivity before deletion
            
        Returns:
            Tuple of (room_conversations_deleted, inter_agent_deleted)
        """
        room_deleted = self.room_conv_db.delete_stale(days)
        inter_deleted = self.inter_agent_db.delete_stale(days)
        
        logger.info(
            f"Cleaned up {room_deleted} room conversations and "
            f"{inter_deleted} inter-agent conversations older than {days} days"
        )
        
        return room_deleted, inter_deleted


_service: Optional[ConversationService] = None


def get_conversation_service(
    letta_client: Optional[Letta] = None,
    isolated_block_labels: Optional[List[str]] = None,
) -> ConversationService:
    """Get or create the ConversationService singleton."""
    global _service
    
    if _service is None or letta_client is not None:
        _service = ConversationService(letta_client, isolated_block_labels)
    
    return _service


def reset_conversation_service() -> None:
    """Reset the service singleton (useful for testing)."""
    global _service
    _service = None
