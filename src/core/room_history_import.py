"""
Room history import — import recent Letta conversation history into Matrix rooms.
"""

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("matrix_client.room_manager")

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


class RoomHistoryImportMixin:
    """History import methods mixed into MatrixRoomManager."""

    async def import_recent_history(
        self,
        agent_id: str,
        agent_username: str,
        agent_password: str,
        room_id: str,
        limit: int = 15
    ):
        """Import recent Letta conversation history for UI continuity

        Args:
            agent_id: The Letta agent ID
            agent_username: Matrix username for the agent
            agent_password: Matrix password for the agent
            room_id: Matrix room ID to import messages into
            limit: Number of recent messages to import (default: 15, like letta-code)
        """
        try:
            # 1. Fetch recent messages from Letta proxy
            messages_url = f"http://192.168.50.90:8289/v1/agents/{agent_id}/messages"

            session = await self._get_session()
            async with session.get(messages_url, timeout=DEFAULT_TIMEOUT) as response:
                if response.status != 200:
                    logger.warning(f"Could not fetch history for agent {agent_id}: {response.status}")
                    return

                data = await response.json()
                # Handle both array and object responses
                if isinstance(data, dict):
                    messages = data.get("items", [])
                else:
                    messages = data

            if not messages:
                logger.info(f"No history to import for agent {agent_id}")
                return

            # 2. Take only last N messages (like letta-code does)
            recent_messages = messages[-limit:] if len(messages) > limit else messages

            # 3. Skip if starts with orphaned tool_return (incomplete turn)
            if recent_messages and recent_messages[0].get("message_type") == "tool_return_message":
                recent_messages = recent_messages[1:]

            if not recent_messages:
                logger.info(f"No valid history to import for agent {agent_id}")
                return

            # 4. Login as the agent to send historical messages
            from nio import AsyncClient, LoginResponse
            agent_client = AsyncClient(self.homeserver_url, agent_username)

            try:
                login_response = await agent_client.login(agent_password)

                if not isinstance(login_response, LoginResponse):
                    logger.error(f"Failed to login as {agent_username} for history import")
                    await agent_client.close()
                    return

                # 5. Send each message with historical flag
                imported_count = 0
                for msg in recent_messages:
                    msg_type = msg.get("message_type")

                    # Only import user and assistant messages (skip tool calls, reasoning, etc.)
                    if msg_type == "user_message":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                            content = " ".join(text_parts)

                        await agent_client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={
                                "msgtype": "m.text",
                                "body": f"[History] {content}",
                                "m.letta_historical": True,
                                "m.relates_to": {
                                    "rel_type": "m.annotation"
                                }
                            }
                        )
                        imported_count += 1

                    elif msg_type == "assistant_message":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                            content = " ".join(text_parts)

                        await agent_client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={
                                "msgtype": "m.text",
                                "body": content,
                                "m.letta_historical": True,
                                "m.relates_to": {
                                    "rel_type": "m.annotation"
                                }
                            }
                        )
                        imported_count += 1

                logger.info(f"Imported {imported_count} historical messages for agent {agent_id}")

            finally:
                await agent_client.close()

        except Exception as e:
            logger.error(f"Error importing history for agent {agent_id}: {e}")
