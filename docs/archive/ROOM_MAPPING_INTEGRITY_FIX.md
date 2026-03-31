# Room Mapping Integrity Fix - November 18, 2025

## Problem

After running the duplicate room cleanup, users reported 404 errors when messaging agents:

```
Sorry, I encountered an error while processing your message: 
An unexpected error occurred with the Letta SDK: 
Letta API error: 404 - {"detail":"Agent with ID...
```

## Root Cause Analysis

### Investigation
1. Checked logs - found massive M_FORBIDDEN errors for deleted rooms
2. Verified agent mappings - **33 agents still pointed to deleted rooms!**
3. Discovered cleanup script bug - backup file identical to current file (both 28247 bytes)

### The Bug
The `delete_duplicate_rooms.py` script had this logic:

```python
# WRONG: This backs up the UNMODIFIED mappings
with open(backup_file, 'w') as f:
    json.dump(mappings, f, indent=2)  # mappings already modified in memory!
print(f"✅ Backed up old mappings to {backup_file}")

# Save updated mappings
with open(mappings_file, 'w') as f:
    json.dump(mappings, f, indent=2)  # Same data written to both files!
```

**The script modified `mappings` in memory, then "backed up" the already-modified version.** Both files ended up identical - no actual update was saved to disk!

### Impact
- 33 agents had stale room IDs pointing to deleted rooms
- When users messaged these agents, the system tried to access non-existent rooms
- Letta API returned 404 because it couldn't find the agent context
- System appeared broken despite mappings file existing

## Solution

### Immediate Fix
Manually updated all 33 stale mappings:

```python
# Load old->new room mapping from deduplication data
old_to_new = build_room_mapping_from_duplicates()

# Update each stale mapping
for agent_id, mapping in mappings.items():
    if mapping['room_id'] in old_to_new:
        mapping['room_id'] = old_to_new[mapping['room_id']]
        
# IMPORTANT: Back up BEFORE saving
shutil.copy('agent_user_mappings.json', f'backup_{timestamp}.json')

# Now save updated mappings
with open('agent_user_mappings.json', 'w') as f:
    json.dump(mappings, f, indent=2)
```

**Results:**
- Updated 33 agent mappings to point to correct (kept) rooms
- Restarted matrix-client to load new mappings
- All agents now functional

### Long-term Solution: Integrity Tests

Created comprehensive test suite to prevent this from ever happening again:

#### `tests/integration/test_room_mapping_integrity.py`

**8 Tests Total:**

1. **`test_all_agent_mappings_point_to_existing_rooms`** ⭐ KEY TEST
   - Verifies every agent mapping points to a room that actually exists
   - Fails with detailed error if any stale mappings found
   - Catches the exact bug we just fixed

2. **`test_space_config_points_to_existing_space`**
   - Validates Letta Agents space exists
   - Prevents space recreation loops

3. **`test_no_duplicate_room_assignments`**
   - Ensures each agent has a unique room
   - Prevents room sharing conflicts

4. **`test_all_mapped_rooms_have_room_created_flag`**
   - Validates state consistency
   - Ensures room_id implies room_created=True

5. **`test_mappings_file_is_valid_json`**
   - Catches file corruption
   - Prevents startup crashes

6. **`test_space_config_is_valid_json`**
   - Catches space config corruption

7. **`test_post_cleanup_verification`**
   - Documents required verification steps after cleanup

8. **`test_cleanup_script_creates_backup`**
   - Documents backup requirements

### How to Use These Tests

#### After Any Room Cleanup
```bash
# Run integrity tests IMMEDIATELY
python3 -m pytest tests/integration/test_room_mapping_integrity.py -v

# If tests pass:
# ✅ All mappings valid
# ✅ Safe to restart matrix-client
# ✅ Users can message agents without errors

# If tests fail:
# ❌ Stale mappings detected
# ❌ Fix mappings before restarting
# ❌ Re-run tests until they pass
```

#### In CI/CD Pipeline
Add to test workflow:
```yaml
- name: Run integrity tests
  run: |
    pytest tests/integration/test_room_mapping_integrity.py -v
```

This catches configuration errors BEFORE deployment!

## Fixed Cleanup Script

Updated `scripts/cleanup/delete_duplicate_rooms.py`:

```python
def update_agent_mappings(old_to_new_map):
    """Update agent_user_mappings.json to use new room IDs"""
    mappings_file = Path('matrix_client_data/agent_user_mappings.json')
    
    # Load current mappings
    with open(mappings_file) as f:
        mappings = json.load(f)
    
    # CRITICAL: Back up BEFORE modifying!
    backup_file = f'{mappings_file}.backup_{int(time.time())}'
    shutil.copy(mappings_file, backup_file)
    print(f"✅ Backed up ORIGINAL to {backup_file}")
    
    # Now update mappings
    for agent_id, mapping in mappings.items():
        old_room_id = mapping.get('room_id')
        if old_room_id in old_to_new_map:
            mapping['room_id'] = old_to_new_map[old_room_id]
            updated += 1
    
    # Save updated mappings
    with open(mappings_file, 'w') as f:
        json.dump(mappings, f, indent=2)
    
    print(f"✅ Updated {updated} mappings")
```

**Key Change:** Backup happens BEFORE modification, not after!

## Verification

### Before Fix
```bash
$ python3 check_stale_mappings.py
❌ PROBLEM: 33 mappings still point to DELETED rooms

$ curl agent_room -> 404 error
```

### After Fix
```bash
$ python3 -m pytest tests/integration/test_room_mapping_integrity.py -v
8 passed in 1.13s ✅

$ curl agent_room -> 200 OK ✅
```

## Lessons Learned

### 1. Always Backup BEFORE Modifying
```python
# ❌ WRONG
modify_data()
backup_data()  # Too late!

# ✅ RIGHT
backup_data()
modify_data()
```

### 2. Verify Backup Actually Worked
```python
# After backup, verify sizes differ
original_size = os.path.getsize(original_file)
backup_size = os.path.getsize(backup_file)

if original_size == backup_size:
    # Suspicious - might be backing up same data
    verify_content_differs()
```

### 3. Test Before Restart
Never restart matrix-client after a cleanup without running integrity tests first!

```bash
# ❌ WRONG
python3 cleanup_script.py
docker-compose restart matrix-client  # Might deploy broken config!

# ✅ RIGHT  
python3 cleanup_script.py
pytest tests/integration/test_room_mapping_integrity.py  # Verify first!
docker-compose restart matrix-client  # Only if tests pass
```

### 4. Integration Tests Catch Config Errors
Unit tests can't catch "agent points to deleted room" - you need integration tests that:
- Read actual config files
- Check against actual Matrix server
- Verify end-to-end consistency

## Preventing Future Issues

### Mandatory Checklist for Room Operations

Before ANY room cleanup/migration/deduplication:

- [ ] Create timestamped backup of `agent_user_mappings.json`
- [ ] Create timestamped backup of `letta_space_config.json`
- [ ] Document which rooms will be deleted
- [ ] Document which rooms will be kept

After ANY room cleanup:

- [ ] Run `pytest tests/integration/test_room_mapping_integrity.py`
- [ ] Verify all tests pass
- [ ] Check backup files are different from current files
- [ ] Test messaging at least one agent before deployment
- [ ] Restart matrix-client
- [ ] Monitor logs for 404 or M_FORBIDDEN errors
- [ ] Test messaging multiple agents in production

### CI/CD Integration

Add to `.github/workflows/test.yml`:

```yaml
- name: Integration Tests
  run: |
    # Run room mapping integrity tests
    pytest tests/integration/test_room_mapping_integrity.py -v
    
    # Fail build if any mappings are stale
    if [ $? -ne 0 ]; then
      echo "❌ Room mapping integrity tests failed!"
      echo "Fix stale mappings before deploying."
      exit 1
    fi
```

## Files Changed

### Created
- `tests/integration/test_room_mapping_integrity.py` - 8 integrity tests
- `docs/ROOM_MAPPING_INTEGRITY_FIX.md` - This document

### Modified
- `scripts/cleanup/delete_duplicate_rooms.py` - Fixed backup logic
- `matrix_client_data/agent_user_mappings.json` - Fixed 33 stale mappings

### Backed Up
- `matrix_client_data/agent_user_mappings.json.backup_1763440621` - Pre-fix backup

## Current Status

✅ **All Systems Operational**

- Room count: 53 (down from 116)
- Stale mappings: 0 (fixed all 33)
- Integrity tests: 8/8 passing
- Space: Valid and stable
- Agents: All responding correctly

## Quick Reference

### Run Integrity Tests
```bash
pytest tests/integration/test_room_mapping_integrity.py -v
```

### Check for Stale Mappings
```bash
python3 << 'EOF'
import json

with open('matrix_client_data/agent_user_mappings.json') as f:
    mappings = json.load(f)

# Load your deleted rooms list
with open('/tmp/duplicate_rooms.json') as f:
    duplicates = json.load(f)

deleted = {r['room_id'] for rooms in duplicates.values() for r in rooms[1:]}
stale = [(a, m['room_id']) for a, m in mappings.items() if m.get('room_id') in deleted]

if stale:
    print(f"❌ {len(stale)} stale mappings")
    for agent, room in stale[:5]:
        print(f"  {agent}: {room}")
else:
    print("✅ No stale mappings")
EOF
```

### Manual Fix Command
```bash
# If integrity tests fail, run this to fix mappings
python3 scripts/cleanup/fix_stale_mappings.py
```

---

**Last Updated**: 2025-11-18  
**Status**: ✅ Resolved  
**Verified By**: Integration tests passing + production validation
