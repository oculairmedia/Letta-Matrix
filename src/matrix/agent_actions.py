"""
Agent-as-user Matrix API operations — re-export hub.

All public names are re-exported from focused modules for backward
compatibility. New code should import directly from:
  - agent_message_content: _build_message_content, _build_edit_content
  - agent_room_cache: _get_agent_mapping_for_room
  - agent_send: send_as_agent, send_as_agent_with_event_id
  - agent_edit_delete: delete_message_as_agent, edit_message_as_agent
  - agent_reactions: send_reaction_as_agent, send_read_receipt_as_agent
  - agent_typing: TypingIndicatorManager, set_typing_as_agent
  - agent_media: upload_and_send_audio, fetch_and_send_image, etc.
"""

from src.matrix.agent_message_content import (  # noqa: F401
    _build_message_content,
    _build_edit_content,
    _is_simple_message,
    _might_contain_mentions,
)
from src.matrix.agent_room_cache import (  # noqa: F401
    _get_agent_mapping_for_room,
    _ROOM_AGENT_MAPPING_CACHE,
)
from src.matrix.agent_send import (  # noqa: F401
    send_as_agent,
    send_as_agent_with_event_id,
    _session_scope,
)
from src.matrix.agent_edit_delete import (  # noqa: F401
    delete_message_as_agent,
    edit_message_as_agent,
)
from src.matrix.agent_reactions import (  # noqa: F401
    send_reaction_as_agent,
    send_read_receipt_as_agent,
)
from src.matrix.agent_typing import (  # noqa: F401
    TypingIndicatorManager,
    set_typing_as_agent,
    _get_agent_typing_context,
    _put_typing,
)
from src.matrix.agent_media import (  # noqa: F401
    fetch_and_send_file,
    fetch_and_send_image,
    fetch_and_send_video,
    upload_and_send_audio,
)
from src.matrix.agent_auth import (  # noqa: F401
    get_agent_token,
    repair_agent_password,
)
from src.matrix.config import Config  # noqa: F401
