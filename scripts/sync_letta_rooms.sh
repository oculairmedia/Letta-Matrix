#!/bin/bash
# Sync letta user to all rooms admin is in and enable relay mode for new rooms
# This script should be run periodically via cron or systemd timer

MATRIX_SERVER="http://192.168.50.90:6167"
ADMIN_USER="admin"
ADMIN_PASS="m6kvcVMWiSYzi6v"
LETTA_USER="letta"
LETTA_PASS="letta"

# Appservice tokens for bridge room invites
GMESSAGES_AS_TOKEN="TigM8WUqqeURX22h3QUITJuVFi7j2SySUzFT6zxj1dn0ASecNRrcoLgLXKu1sQmR"
DISCORD_AS_TOKEN="0cc78705-425c-47c7-89c6-26371a7d78af"
META_AS_TOKEN="7efb34256712a0446eb40d531897d5aacf8bb451cb8de9f8d5d3279c6e7a787b"

LOG_FILE="/var/log/matrix-letta-sync.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Get tokens
ADMIN_TOKEN=$(curl -s -X POST "$MATRIX_SERVER/_matrix/client/v3/login" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"$ADMIN_USER\"},\"password\":\"$ADMIN_PASS\"}" | jq -r '.access_token')

LETTA_TOKEN=$(curl -s -X POST "$MATRIX_SERVER/_matrix/client/v3/login" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"m.login.password\",\"identifier\":{\"type\":\"m.id.user\",\"user\":\"$LETTA_USER\"},\"password\":\"$LETTA_PASS\"}" | jq -r '.access_token')

if [ -z "$ADMIN_TOKEN" ] || [ "$ADMIN_TOKEN" == "null" ]; then
    log "ERROR: Failed to get admin token"
    exit 1
fi

if [ -z "$LETTA_TOKEN" ] || [ "$LETTA_TOKEN" == "null" ]; then
    log "ERROR: Failed to get letta token"
    exit 1
fi

# Get room lists
ADMIN_ROOMS=$(curl -s "$MATRIX_SERVER/_matrix/client/v3/joined_rooms" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -r '.joined_rooms[]' | sort)

LETTA_ROOMS=$(curl -s "$MATRIX_SERVER/_matrix/client/v3/joined_rooms" \
  -H "Authorization: Bearer $LETTA_TOKEN" | jq -r '.joined_rooms[]' | sort)

# Find rooms admin has but letta doesn't
MISSING_ROOMS=$(comm -23 <(echo "$ADMIN_ROOMS") <(echo "$LETTA_ROOMS"))

if [ -z "$MISSING_ROOMS" ]; then
    # No output if already in sync (reduce log noise)
    exit 0
fi

MISSING_COUNT=$(echo "$MISSING_ROOMS" | wc -l)
log "INFO: Found $MISSING_COUNT new rooms to sync"

SYNCED=0
FAILED=0
RELAY_ENABLED=0

for ROOM_ID in $MISSING_ROOMS; do
    # Try to get bridge info to determine which appservice to use
    BRIDGE_INFO=$(curl -s "$MATRIX_SERVER/_matrix/client/v3/rooms/$ROOM_ID/state" \
      -H "Authorization: Bearer $ADMIN_TOKEN" 2>/dev/null | jq -r '.[] | select(.type == "uk.half-shot.bridge") | .content.protocol.id // empty' 2>/dev/null | head -1)
    
    INVITED=false
    
    # Try appservice invite based on bridge type
    if [[ "$BRIDGE_INFO" == *"gmessages"* ]]; then
        curl -s -X POST "$MATRIX_SERVER/_matrix/client/v3/rooms/$ROOM_ID/invite?user_id=@gmessagesbot:matrix.oculair.ca" \
          -H "Authorization: Bearer $GMESSAGES_AS_TOKEN" \
          -H "Content-Type: application/json" \
          -d '{"user_id":"@letta:matrix.oculair.ca"}' > /dev/null 2>&1
        INVITED=true
    elif [[ "$BRIDGE_INFO" == *"discord"* ]]; then
        curl -s -X POST "$MATRIX_SERVER/_matrix/client/v3/rooms/$ROOM_ID/invite?user_id=@_discord_bot:matrix.oculair.ca" \
          -H "Authorization: Bearer $DISCORD_AS_TOKEN" \
          -H "Content-Type: application/json" \
          -d '{"user_id":"@letta:matrix.oculair.ca"}' > /dev/null 2>&1
        INVITED=true
    elif [[ "$BRIDGE_INFO" == *"meta"* ]] || [[ "$BRIDGE_INFO" == *"instagram"* ]] || [[ "$BRIDGE_INFO" == *"facebook"* ]]; then
        curl -s -X POST "$MATRIX_SERVER/_matrix/client/v3/rooms/$ROOM_ID/invite?user_id=@metabot:matrix.oculair.ca" \
          -H "Authorization: Bearer $META_AS_TOKEN" \
          -H "Content-Type: application/json" \
          -d '{"user_id":"@letta:matrix.oculair.ca"}' > /dev/null 2>&1
        INVITED=true
    fi
    
    # If no appservice invite, try admin invite
    if [ "$INVITED" = false ]; then
        curl -s -X POST "$MATRIX_SERVER/_matrix/client/v3/rooms/$ROOM_ID/invite" \
          -H "Authorization: Bearer $ADMIN_TOKEN" \
          -H "Content-Type: application/json" \
          -d '{"user_id":"@letta:matrix.oculair.ca"}' > /dev/null 2>&1
    fi
    
    # Letta joins
    JOIN_RESULT=$(curl -s -X POST "$MATRIX_SERVER/_matrix/client/v3/rooms/$ROOM_ID/join" \
      -H "Authorization: Bearer $LETTA_TOKEN" \
      -H "Content-Type: application/json")
    
    if echo "$JOIN_RESULT" | grep -q "errcode"; then
        ((FAILED++))
    else
        ((SYNCED++))
        
        # Enable relay for bridges that support it (gmessages, meta/instagram)
        # Discord does NOT support relay mode
        if [ -n "$BRIDGE_INFO" ]; then
            sleep 0.5
            TXN_ID=$(date +%s%N)
            ENCODED_ROOM=$(echo "$ROOM_ID" | sed 's/!/%21/g' | sed 's/:/%3A/g')
            if [[ "$BRIDGE_INFO" == *"gmessages"* ]]; then
                curl -s -X PUT "$MATRIX_SERVER/_matrix/client/v3/rooms/${ENCODED_ROOM}/send/m.room.message/$TXN_ID" \
                  -H "Authorization: Bearer $ADMIN_TOKEN" \
                  -H "Content-Type: application/json" \
                  -d '{"msgtype":"m.text","body":"!gm set-relay"}' > /dev/null
                ((RELAY_ENABLED++))
            elif [[ "$BRIDGE_INFO" == *"meta"* ]] || [[ "$BRIDGE_INFO" == *"instagram"* ]] || [[ "$BRIDGE_INFO" == *"facebook"* ]]; then
                # Use appservice token to impersonate admin for meta bridge
                curl -s -X PUT "$MATRIX_SERVER/_matrix/client/v3/rooms/${ENCODED_ROOM}/send/m.room.message/${TXN_ID}?user_id=%40admin%3Amatrix.oculair.ca" \
                  -H "Authorization: Bearer $META_AS_TOKEN" \
                  -H "Content-Type: application/json" \
                  -d '{"msgtype":"m.text","body":"!meta set-relay"}' > /dev/null
                ((RELAY_ENABLED++))
            fi
            # Note: Discord does NOT support relay mode, so skip it
        fi
    fi
done

log "INFO: Synced $SYNCED rooms, failed $FAILED, relay enabled in $RELAY_ENABLED bridge rooms"

# Final count
FINAL_ADMIN=$(curl -s "$MATRIX_SERVER/_matrix/client/v3/joined_rooms" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.joined_rooms | length')
FINAL_LETTA=$(curl -s "$MATRIX_SERVER/_matrix/client/v3/joined_rooms" \
  -H "Authorization: Bearer $LETTA_TOKEN" | jq '.joined_rooms | length')

log "INFO: Final state - Admin: $FINAL_ADMIN, Letta: $FINAL_LETTA"
