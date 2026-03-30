# Matrix Client - Agent Text Reply Send Path Analysis

## EXECUTIVE SUMMARY

**Exact send path for agent text replies:**
```
Letta Model Response 
  → streaming.py:StreamingMessageHandler._send_final() OR streaming.py:LiveEditStreamingHandler._send_final()
    → letta_bridge.py:send_final_message()
      → poll_handler.py:process_agent_response() [poll command detection]
        → agent_actions.py:send_as_agent_with_event_id()
          → agent_actions.py:_build_message_content() [constructs message_data dict]
            → Matrix API PUT /_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}
```

**For non-streaming responses:**
```
Letta Model Response
  → message_processor.py:_process_letta_message() [line 373-424]
    → poll_handler.py:process_agent_response() [poll command detection]
      → agent_actions.py:send_as_agent()
        → agent_actions.py:send_as_agent_with_event_id()
          → agent_actions.py:_build_message_content() [constructs message_data dict]
            → Matrix API PUT /_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}
```

---

## CRITICAL INJECTION POINT: `_build_message_content()`

**File:** `/opt/stacks/matrix-tuwunel-deploy/src/matrix/agent_actions.py`  
**Function:** `_build_message_content()` (lines 128-204)  
**Parameters:** `thread_event_id`, `thread_latest_event_id`, `reply_to_event_id`

### Current Threading Logic (lines 166-177)

```python
if thread_event_id:
    fallback_event_id = thread_latest_event_id or reply_to_event_id or thread_event_id
    message_data["m.relates_to"] = {
        "rel_type": "m.thread",
        "event_id": thread_event_id,
        "is_falling_back": True,
        "m.in_reply_to": {"event_id": fallback_event_id},
    }
elif reply_to_event_id:
    message_data["m.relates_to"] = {
        "m.in_reply_to": {"event_id": reply_to_event_id}
    }
```

**Behavior:**
- **Thread mode:** If `thread_event_id` is provided, constructs full thread relation with fallback
- **Reply mode:** If only `reply_to_event_id` is provided, constructs simple reply (NOT threaded)
- **Top-level:** If neither is provided, message has no relation metadata

---

## STREAMING PATH DETAIL

### 1. StreamingMessageHandler (discrete send/delete pattern)

**File:** `/opt/stacks/matrix-tuwunel-deploy/src/matrix/streaming.py` (lines 427-643)

**Initialization (line 444):**
```python
def __init__(
    self,
    send_message: Callable[..., Any],
    delete_message: Callable[[str, str], Any],
    room_id: str,
    delete_progress: bool = False,
    send_final_message: Optional[Callable[..., Any]] = None,
    thread_root_event_id: Optional[str] = None,  # <-- STORED HERE
):
```

**Final message send (line 507-522):**
```python
async def _send_final(self, content: str) -> str:
    threaded = bool(self.thread_root_event_id)
    if threaded:
        try:
            event_id = await self.send_final_message(
                self.room_id,
                content,
                thread_event_id=self.thread_root_event_id,  # <-- PASSED HERE
                thread_latest_event_id=self._latest_thread_event_id,
            )
        except TypeError:
            event_id = await self.send_final_message(self.room_id, content)
        if event_id:
            self._latest_thread_event_id = event_id
        return event_id
    return await self.send_final_message(self.room_id, content)
```

### 2. LiveEditStreamingHandler (in-place edit pattern)

**File:** `/opt/stacks/matrix-tuwunel-deploy/src/matrix/streaming.py` (lines 645-922)

**Initialization (line 661):**
```python
def __init__(
    self,
    send_message: Callable[..., Any],
    edit_message: Callable[..., Any],
    room_id: str,
    send_final_message: Optional[Callable[..., Any]] = None,
    delete_message: Optional[Callable[[str, str], Any]] = None,
    thread_root_event_id: Optional[str] = None,  # <-- STORED HERE
):
```

**Final message send (line 831-854):**
```python
async def _send_final(self, content: str) -> str:
    # Replace progress message with final response (avoids redacted blocks)
    if self._event_id:
        await self._edit_with_msgtype(self._event_id, content, "m.text")
        eid = self._event_id
        self._event_id = None
        self._lines.clear()
        return eid

    # No progress message existed — send fresh
    if self.thread_root_event_id:
        try:
            eid = await self.send_final_message(
                self.room_id,
                content,
                thread_event_id=self.thread_root_event_id,  # <-- PASSED HERE
                thread_latest_event_id=self._latest_thread_event_id,
            )
        except TypeError:
            eid = await self.send_final_message(self.room_id, content)
        self._latest_thread_event_id = eid
        return eid
    eid = await self.send_final_message(self.room_id, content)
    return eid
```

### 3. send_final_message callback

**File:** `/opt/stacks/matrix-tuwunel-deploy/src/matrix/letta_bridge.py` (lines 178-226)

```python
async def send_final_message(
    rid: str,
    content: str,
    thread_event_id: Optional[str] = None,  # <-- RECEIVED FROM HANDLER
    thread_latest_event_id: Optional[str] = None,
) -> str:
    final_content = content

    # [OpenCode @mention injection logic...]

    # [Poll command processing...]
    poll_handled, remaining_text, poll_event_id = await process_agent_response(
        room_id=rid,
        response_text=final_content,
        config=config,
        logger_instance=logger,
        reply_to_event_id=reply_to_event_id,  # <-- FROM MESSAGE_PROCESSOR
        reply_to_sender=reply_to_sender,      # <-- FROM MESSAGE_PROCESSOR
    )

    # [... poll handling ...]

    event_id = await send_as_agent_with_event_id(
        rid,
        final_content,
        config,
        logger,
        reply_to_event_id=reply_to_event_id,      # <-- PASSED TO send_as_agent_with_event_id
        reply_to_sender=reply_to_sender,
        thread_event_id=thread_event_id,          # <-- PASSED TO send_as_agent_with_event_id
        thread_latest_event_id=thread_latest_event_id,
    )
    return event_id or ""
```

**NOTE:** `reply_to_event_id` and `reply_to_sender` come from `letta_bridge.py` scope (lines 72-73, captured from `message_processor.py`).

---

## NON-STREAMING PATH DETAIL

**File:** `/opt/stacks/matrix-tuwunel-deploy/src/matrix/message_processor.py` (lines 373-424)

```python
# ── Non-streaming path ────────────────────────────────────
else:
    letta_response = await send_to_letta_api(
        message_to_send, event_sender, config, logger, room_id
    )
    if silent_mode:
        logger.info(
            f"[GROUP_GATING] Silent mode: suppressing direct-API response in {room_id}"
        )
        return

    if matrix_formatter.is_no_reply(letta_response):
        logger.info(
            f"[DIRECT-API] Agent chose not to reply (no-reply marker) in {room_id}"
        )
    else:
        # [OpenCode @mention injection...]

        sent_as_agent = False

        poll_handled, remaining_text, poll_event_id = (
            await process_agent_response(
                room_id=room_id,
                response_text=letta_response,
                config=config,
                logger_instance=logger,
                reply_to_event_id=None,  # <-- NO REPLY RELATION
                reply_to_sender=None,    # <-- NO REPLY RELATION
            )
        )

        # [... poll handling ...]

        if not poll_handled or remaining_text:
            sent_as_agent = await send_as_agent(
                room_id,
                letta_response,
                config,
                logger,
                reply_to_event_id=None,  # <-- NO REPLY RELATION
                reply_to_sender=None,    # <-- NO REPLY RELATION
            )
```

**Critical observation:** Non-streaming responses are **always sent as top-level messages** because `reply_to_event_id=None` is hardcoded.

---

## THREADING PARAMETER FLOW

### Where `thread_root_event_id` Originates

**File:** `/opt/stacks/matrix-tuwunel-deploy/src/matrix/letta_bridge.py` (line 56)

```python
async def send_to_letta_api_streaming(
    message_body: Union[str, list],
    sender_id: str,
    config: Config,
    logger: logging.Logger,
    room_id: str,
    room_member_count: int = 3,
    opencode_sender: Optional[str] = None,
    reply_to_event_id: Optional[str] = None,  # <-- FROM message_processor.py
    reply_to_sender: Optional[str] = None,
    thread_root_event_id: Optional[str] = None,  # <-- FROM message_processor.py
) -> str:
```

**Upstream caller:** `message_processor.py:_process_letta_message()` (line 345)

```python
letta_response = await send_to_letta_api_streaming(
    message_to_send,
    event_sender,
    config,
    logger,
    room_id,
    room_member_count=room_member_count,
    opencode_sender=opencode_mxid,
    reply_to_event_id=original_event_id,  # <-- FROM MESSAGE EVENT
    reply_to_sender=event_sender,
    thread_root_event_id=None,  # <-- HARDCODED TO None
)
```

**PROBLEM:** `thread_root_event_id` is always `None` in current code. It is never extracted from the incoming message event.

---

## CURRENT BEHAVIOR SUMMARY

### Top-level message (no thread relation)
- **Condition:** Message has no `m.relates_to` field
- **Agent reply:** Top-level message (no thread or reply relation)
- **Reason:** `thread_root_event_id=None` and `reply_to_event_id=None` → no relation metadata

### Reply message (m.in_reply_to only)
- **Condition:** Message has `m.relates_to: {m.in_reply_to: {event_id: ...}}`
- **Agent reply:** Top-level message (no thread or reply relation)
- **Reason:** `reply_to_event_id` is extracted but not used for reply relation in streaming path

### Threaded message (m.thread)
- **Condition:** Message has `m.relates_to: {rel_type: "m.thread", event_id: ..., m.in_reply_to: {...}}`
- **Agent reply:** Top-level message (no thread or reply relation)
- **Reason:** `thread_root_event_id` is never extracted from incoming message

---

## REQUIRED EXTRACTION LOGIC

To fix threading, the following must be extracted from the **incoming message event** in `message_processor.py`:

```python
# Extract thread relation from incoming message
relates_to = event.source.get("content", {}).get("m.relates_to", {})
thread_root_event_id = None
reply_to_event_id = None

if relates_to.get("rel_type") == "m.thread":
    # Message is in a thread
    thread_root_event_id = relates_to.get("event_id")
    # Extract the immediate parent (fallback reply)
    reply_to_event_id = relates_to.get("m.in_reply_to", {}).get("event_id")
elif "m.in_reply_to" in relates_to:
    # Simple reply (not threaded)
    reply_to_event_id = relates_to.get("m.in_reply_to", {}).get("event_id")
```

Then pass both parameters through the entire call chain:
1. `message_processor.py` → `send_to_letta_api_streaming()`
2. `letta_bridge.py` → handler initialization
3. handler → `send_final_message()` callback
4. `send_final_message()` → `send_as_agent_with_event_id()`
5. `send_as_agent_with_event_id()` → `_build_message_content()`

---

## FILES REQUIRING MODIFICATION

1. **`src/matrix/message_processor.py`**
   - Extract `thread_root_event_id` from incoming message
   - Pass to `send_to_letta_api_streaming()` and `send_to_letta_api()`

2. **`src/matrix/letta_bridge.py`**
   - Update `send_to_letta_api()` signature to accept `thread_root_event_id`
   - Construct reply parameters for non-streaming path

3. **`src/matrix/agent_actions.py`**
   - No changes needed (already supports all parameters)

---

## EXISTING PARAMETERS IN `_build_message_content()`

Already supports all necessary threading/reply parameters:
- `reply_to_event_id` (line 131)
- `reply_to_sender` (line 132)
- `reply_to_body` (line 133)
- `thread_event_id` (line 135)
- `thread_latest_event_id` (line 136)

**No changes needed in `agent_actions.py`** — the infrastructure is complete.

---

## SUMMARY

**Call chain for streaming final message:**
```
model response 
  → StreamingMessageHandler._send_final() [streaming.py:507]
    → send_final_message() [letta_bridge.py:178]
      → send_as_agent_with_event_id() [agent_actions.py:256]
        → _build_message_content() [agent_actions.py:128]
          → Matrix API
```

**Call chain for non-streaming message:**
```
model response
  → send_to_letta_api() returns text [letta_bridge.py:489]
    → send_as_agent() [message_processor.py:417]
      → send_as_agent_with_event_id() [agent_actions.py:382]
        → _build_message_content() [agent_actions.py:128]
          → Matrix API
```

**Key injection point:** `_build_message_content()` (agent_actions.py:128)  
**Required extraction:** `message_processor.py` must extract `thread_root_event_id` from incoming message  
**Current bug:** `thread_root_event_id=None` hardcoded in both streaming and non-streaming paths
