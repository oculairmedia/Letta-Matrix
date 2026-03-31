# Matrix Synapse Permission Fix Summary

## Problem Description

The Matrix Synapse deployment was experiencing M_FORBIDDEN (403) errors when trying to invite users to rooms. The specific error was:

```
403 - @matrixadmin:matrix.oculair.ca not in room !uVDZegkxMnvWCbwXmW:matrix.oculair.ca
```

This was occurring because the system was trying to invite `@admin:matrix.oculair.ca` to rooms where the inviting user (`@matrixadmin:matrix.oculair.ca`) was not actually a member.

## Root Cause

The `_invite_user_with_retry` function in `agent_user_manager.py` was attempting to invite users to rooms without first checking if the inviting user had the necessary permissions (i.e., was actually in the room).

In Matrix, only users who are members of a room can invite other users to that room. If the inviting user is not in the room, the server returns a 403 M_FORBIDDEN error.

## Solution Implemented

### 1. Enhanced `_invite_user_with_retry` Function

The function now includes a pre-check to ensure the inviting user is in the room before attempting to invite others:

```python
# First, check if the inviting user (admin) is in the room
inviting_user_id = self.admin_username
is_admin_in_room = await self._check_user_in_room(room_id, inviting_user_id, admin_token)

if not is_admin_in_room:
    logger.info(f"Admin user {inviting_user_id} not in room {room_id}, attempting to join first")
    join_success = await self._join_room_as_admin(room_id, admin_token)
    if not join_success:
        logger.error(f"Failed to join room {room_id} as admin, cannot invite {user_id}")
        return False
```

### 2. Added Helper Functions

#### `_check_user_in_room(room_id, user_id, admin_token)`
- Checks if a specific user is a member of a room
- Uses the Matrix client API `/rooms/{room_id}/members`
- Returns `True` if the user is in the room with `join` membership status

#### `_join_room_as_admin(room_id, admin_token)`
- Attempts to join a room as the admin user
- Handles different error cases (403 forbidden, invite-only rooms, etc.)
- Returns `True` if successful, `False` otherwise

### 3. Improved Error Handling

Enhanced the error handling to specifically deal with 403 errors and provide better logging:

```python
elif response.status == 403:  # Forbidden
    error_text = await response.text()
    logger.error(f"Forbidden error inviting {user_id} to room {room_id}: {error_text}")
    
    # Check if user is already in the room
    if "already in the room" in error_text or "already joined" in error_text:
        logger.info(f"User {user_id} is already in room {room_id}")
        return True
    elif "not in room" in error_text or "not joined" in error_text:
        logger.error(f"Admin user {inviting_user_id} lost access to room {room_id}, cannot invite")
        return False
    else:
        logger.error(f"Permission denied inviting {user_id} to room {room_id}")
        return False
```

### 4. Enhanced Auto-Accept Invitations

Improved the `auto_accept_invitations` method to better handle cases where users are already in rooms:

```python
elif response.status == 403:
    error_text = await response.text()
    if "already in the room" in error_text or "already joined" in error_text:
        logger.info(f"User {username} is already in room {room_id}")
    else:
        logger.warning(f"User {username} forbidden from joining room {room_id}: {error_text}")
```

## Files Modified

- `/opt/stacks/matrix-synapse-deployment/agent_user_manager.py` - Main fix implementation
- `/opt/stacks/matrix-synapse-deployment/test_permission_fix.py` - Test script to verify the fix

## Testing

A test script (`test_permission_fix.py`) was created to verify that the fix works correctly. This script:
1. Loads existing agent mappings
2. Identifies a problematic room from the logs
3. Tests the fixed invitation function
4. Reports success/failure

## Expected Behavior After Fix

1. **Room Membership Check**: Before attempting to invite any user, the system will check if the inviting user is in the room
2. **Auto-Join**: If the inviting user is not in the room, the system will attempt to join the room first
3. **Graceful Failure**: If the inviting user cannot join the room (e.g., invite-only rooms), the system will log the error and fail gracefully
4. **Better Logging**: More detailed logging for debugging permission issues

## Impact

This fix resolves the M_FORBIDDEN errors that were preventing proper room invitations and ensures that:
- Users can be successfully invited to rooms
- The system handles edge cases where admin users lose room membership
- Better error reporting for troubleshooting
- Reduced log noise from repeated failed invitation attempts

## Deployment Notes

- The fix is backward compatible and doesn't require any database migrations
- Existing room configurations will continue to work
- The fix only adds new functionality without breaking existing behavior
- Monitor logs after deployment to ensure the fix is working as expected