from datetime import datetime
from zoneinfo import ZoneInfo

from typing import Optional


def _format_timestamp(timestamp: int | float | None) -> str:
    if timestamp is None:
        timestamp = datetime.now(tz=ZoneInfo("UTC")).timestamp() * 1000
    dt = datetime.fromtimestamp(float(timestamp) / 1000.0, tz=ZoneInfo("UTC")).astimezone(ZoneInfo("America/Toronto"))
    return f"{dt.strftime('%A')}, {dt.strftime('%b')} {dt.day}, {dt.strftime('%I:%M %p').lstrip('0')} {dt.strftime('%Z')}"


def _extract_localpart(mxid: str) -> str:
    value = mxid or ""
    if value.startswith("@"):
        value = value[1:]
    return value.split(":", 1)[0]

def _build_reply_context_lines(reply_to_event_id: Optional[str] = None, reply_to_sender: Optional[str] = None) -> list[str]:
    """Build reply context lines for the system-reminder envelope."""
    if not reply_to_event_id:
        return []
    lines = [
        "## Reply Context",
        f"- **Reply-To Event**: {reply_to_event_id}",
    ]
    if reply_to_sender:
        sender_localpart = _extract_localpart(reply_to_sender)
        lines.append(f"- **Reply-To Sender**: {sender_localpart}")
    return lines

def format_message_envelope(
    channel,
    chat_id,
    message_id,
    sender,
    sender_name,
    timestamp,
    text,
    is_group=False,
    group_name=None,
    is_mentioned=False,
    reply_to_event_id: Optional[str] = None,
    reply_to_sender: Optional[str] = None,
) -> str:
    sender_value = _extract_localpart(sender) if sender else (sender_name or "")
    context_lines = [f"- **Type**: {'Group chat' if is_group else 'Direct message'}"]
    if is_group and group_name:
        context_lines.append(f"- **Group**: {group_name}")
    if is_mentioned:
        context_lines.append("- **Mentioned**: yes")
    context_lines.append("- **Hint**: To skip replying, respond with exactly: `<no-reply/>`")
    metadata_lines = [
        "- **Channel**: Matrix",
        f"- **Chat ID**: {chat_id}",
        f"- **Message ID**: {message_id}",
        f"- **Sender**: {sender_value}",
        f"- **Timestamp**: {_format_timestamp(timestamp)}",
    ]
    sections = [
        "<system-reminder>",
        "## Message Metadata",
        *metadata_lines,
        "",
        "## Chat Context",
        *context_lines,
    ]
    reply_lines = _build_reply_context_lines(reply_to_event_id, reply_to_sender)
    if reply_lines:
        sections.append("")
        sections.extend(reply_lines)
    sections.append("</system-reminder>")
    reminder = "\n".join(sections)
    body = text or ""
    return f"{reminder}\n\n{body}" if body else reminder


def format_inter_agent_envelope(sender_agent_name, sender_agent_id, text, chat_id, message_id, timestamp, reply_to_event_id: Optional[str] = None, reply_to_sender: Optional[str] = None) -> str:
    metadata_lines = [
        "- **Channel**: Matrix",
        f"- **Chat ID**: {chat_id}",
        f"- **Message ID**: {message_id}",
        f"- **Sender**: {sender_agent_name}",
        f"- **Timestamp**: {_format_timestamp(timestamp)}",
    ]
    sections = [
        "<system-reminder>",
        "## Message Metadata",
        *metadata_lines,
        "",
        "## Chat Context",
        "- **Type**: Direct message",
        "- **Hint**: To skip replying, respond with exactly: `<no-reply/>`",
    ]
    reply_lines = _build_reply_context_lines(reply_to_event_id, reply_to_sender)
    if reply_lines:
        sections.append("")
        sections.extend(reply_lines)
    sections.extend([
        "",
        "## Inter-Agent Context",
        f"- **Sender Agent**: {sender_agent_name}",
        f"- **Sender Agent ID**: {sender_agent_id}",
        "- **System Note**: Treat this as your MAIN task for this turn; the other agent is trying to collaborate with you.",
        "</system-reminder>",
    ])
    reminder = "\n".join(sections)
    body = text or ""
    return f"{reminder}\n\n{body}" if body else reminder


def format_opencode_envelope(opencode_mxid, text, chat_id, message_id, timestamp, reply_to_event_id: Optional[str] = None, reply_to_sender: Optional[str] = None) -> str:
    metadata_lines = [
        "- **Channel**: Matrix",
        f"- **Chat ID**: {chat_id}",
        f"- **Message ID**: {message_id}",
        f"- **Sender**: {_extract_localpart(opencode_mxid)}",
        f"- **Timestamp**: {_format_timestamp(timestamp)}",
    ]
    sections = [
        "<system-reminder>",
        "## Message Metadata",
        *metadata_lines,
        "",
        "## Chat Context",
        "- **Type**: Direct message",
        "- **Hint**: To skip replying, respond with exactly: `<no-reply/>`",
    ]
    reply_lines = _build_reply_context_lines(reply_to_event_id, reply_to_sender)
    if reply_lines:
        sections.append("")
        sections.extend(reply_lines)
    sections.extend([
        "",
        "## OpenCode Context",
        f"- **OpenCode MXID**: {opencode_mxid}",
        f"- **Response Routing**: Include {opencode_mxid} in your response so the OpenCode bridge can route your reply.",
        "</system-reminder>",
    ])
    reminder = "\n".join(sections)
    body = text or ""
    return f"{reminder}\n\n{body}" if body else reminder

def is_no_reply(text: str) -> bool:
    """Check if an agent response is the <no-reply/> directive.

    Agents use this to signal they choose not to reply. The bridge should
    silently discard the message instead of posting it to Matrix.
    """
    stripped = (text or "").strip()
    return stripped in ("<no-reply/>", "<no-reply />")

def wrap_opencode_routing(content: str, opencode_mxid: str) -> str:
    """
    Wrap any message content with OpenCode routing instructions.

    Used for file uploads, /fs-run commands, and other non-text-message paths
    where the content doesn't go through format_opencode_envelope().

    Args:
        content: The message content to wrap (image text, document text, prompt, etc.)
        opencode_mxid: The full Matrix ID of the OpenCode user

    Returns:
        Content wrapped with OpenCode routing instructions
    """
    return (
        f"[MESSAGE FROM OPENCODE USER]\n"
        f"\n"
        f"{content}\n"
        f"\n"
        f"---\n"
        f"RESPONSE INSTRUCTION (OPENCODE BRIDGE):\n"
        f"This message is from an OpenCode user: {opencode_mxid}\n"
        f"When you respond to this message, you MUST include their @mention ({opencode_mxid}) \n"
        f"in your response so the OpenCode bridge can route your reply to them.\n"
        f"\n"
        f'Example: "{opencode_mxid} Here is my response..."\n'
    )

