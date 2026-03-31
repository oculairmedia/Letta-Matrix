# Sprint 4 Plan: Extract MatrixRoomManager

**Goal**: Extract room management logic from `AgentUserManager` into a new `MatrixRoomManager` class

**Estimated Reduction**: ~250-300 lines from `agent_user_manager.py`

## Summary

Sprint 4 will extract all room-related operations from `AgentUserManager` into a dedicated `MatrixRoomManager` class. This includes room creation, discovery, invitation management, and history import functionality.

## Methods to Extract (~387 lines total)

### 1. Room Name Management
- `update_room_name()` - Lines 510-542 (~33 lines)

### 2. Room Discovery  
- `find_existing_agent_room()` - Lines 548-594 (~47 lines)

### 3. Room Creation
- `create_or_update_agent_room()` - Lines 596-736 (~141 lines)

### 4. Invitation Management
- `auto_accept_invitations_with_tracking()` - Lines 855-903+ (~50+ lines)

### 5. History Import
- `import_recent_history()` - Lines 738-853 (~116 lines)

## Expected Results

**Before Sprint 4**: 942 lines  
**After Sprint 4**: ~555 lines  
**Reduction**: ~387 lines (-41%)

**New File**: `src/core/room_manager.py` (~387 lines)

**Cumulative Progress**:
- Original: 1,346 lines
- After Sprint 4: ~555 lines
- **Total Reduction**: ~791 lines (-58.8%)

## Implementation Steps

1. Create `src/core/room_manager.py` with `MatrixRoomManager` class
2. Extract room management methods
3. Update `AgentUserManager` to delegate to `room_manager`
4. Update `src/core/__init__.py` exports
5. Fix test mocks for delegated methods
6. Verify all 184 tests pass
7. Merge to main

## Success Criteria

- [ ] MatrixRoomManager created
- [ ] ~387 lines extracted
- [ ] All tests passing (184/184)
- [ ] Merged to main
- [ ] Documentation updated
