from datetime import datetime
from zoneinfo import ZoneInfo


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
    reminder = "\n".join(
        [
            "<system-reminder>",
            "## Message Metadata",
            *metadata_lines,
            "",
            "## Chat Context",
            *context_lines,
            "</system-reminder>",
        ]
    )
    body = text or ""
    return f"{reminder}\n\n{body}" if body else reminder


def format_inter_agent_envelope(sender_agent_name, sender_agent_id, text, chat_id, message_id, timestamp) -> str:
    metadata_lines = [
        "- **Channel**: Matrix",
        f"- **Chat ID**: {chat_id}",
        f"- **Message ID**: {message_id}",
        f"- **Sender**: {sender_agent_name}",
        f"- **Timestamp**: {_format_timestamp(timestamp)}",
    ]
    reminder = "\n".join(
        [
            "<system-reminder>",
            "## Message Metadata",
            *metadata_lines,
            "",
            "## Chat Context",
            "- **Type**: Direct message",
            "- **Hint**: To skip replying, respond with exactly: `<no-reply/>`",
            "",
            "## Inter-Agent Context",
            f"- **Sender Agent**: {sender_agent_name}",
            f"- **Sender Agent ID**: {sender_agent_id}",
            "- **System Note**: Treat this as your MAIN task for this turn; the other agent is trying to collaborate with you.",
            "</system-reminder>",
        ]
    )
    body = text or ""
    return f"{reminder}\n\n{body}" if body else reminder


def format_opencode_envelope(opencode_mxid, text, chat_id, message_id, timestamp) -> str:
    metadata_lines = [
        "- **Channel**: Matrix",
        f"- **Chat ID**: {chat_id}",
        f"- **Message ID**: {message_id}",
        f"- **Sender**: {_extract_localpart(opencode_mxid)}",
        f"- **Timestamp**: {_format_timestamp(timestamp)}",
    ]
    reminder = "\n".join(
        [
            "<system-reminder>",
            "## Message Metadata",
            *metadata_lines,
            "",
            "## Chat Context",
            "- **Type**: Direct message",
            "- **Hint**: To skip replying, respond with exactly: `<no-reply/>`",
            "",
            "## OpenCode Context",
            f"- **OpenCode MXID**: {opencode_mxid}",
            f"- **Response Routing**: Include {opencode_mxid} in your response so the OpenCode bridge can route your reply.",
            "</system-reminder>",
        ]
    )
    body = text or ""
    return f"{reminder}\n\n{body}" if body else reminder
