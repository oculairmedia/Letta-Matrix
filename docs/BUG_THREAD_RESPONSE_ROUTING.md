# BUG: Matrix Bridge Does Not Route Agent Responses Into Threads

**Status:** Resolved  
**Reported:** 2026-03-29  
**Resolved:** 2026-03-29  
**Component:** Matrix bridge/proxy response routing

## Problem

When a user sends a message from within a Matrix thread, the bridge correctly passes the thread context to the agent (via `Reply Context` / `Reply-To Event` in message metadata). However, the agent's regular text response is always sent to the **main room timeline**, not back into the originating thread.

This breaks threaded conversations — the user writes in a thread but the agent's reply appears in the main room.

## Expected Behavior

The bridge/proxy should detect that an incoming message belongs to a thread (has `Reply-To Event` metadata) and automatically send the agent's response as a threaded reply to that same event, keeping the conversation within the thread.

## Current Workaround

The agent manually calls `matrix_messaging` with `reply_to_event_id` and suppresses the main room response with `<no-reply/>`. This works but is fragile and shouldn't be the agent's responsibility.

## Fix Location

Matrix bridge/proxy — the response routing logic needs to preserve thread context from inbound to outbound messages.

## Reproduction

1. Start a thread in a Matrix room the agent is in
2. Mention the agent in the thread
3. Observe that the agent's response lands in the main room timeline, not in the thread
