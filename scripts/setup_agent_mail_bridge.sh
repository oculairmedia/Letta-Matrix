#!/bin/bash
#
# Register Agent Mail Bridge user in Matrix and get access token
#
# This script:
# 1. Registers @agent_mail_bridge:matrix.oculair.ca user
# 2. Gets access token
# 3. Adds token to .env file
# 4. Joins bridge user to all agent rooms

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Agent Mail Bridge Setup${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo

# Load environment
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
else
    echo -e "${RED}✗ .env file not found${NC}"
    exit 1
fi

HOMESERVER_URL="http://127.0.0.1:6167"
SERVER_NAME="${MATRIX_SERVER_NAME:-matrix.oculair.ca}"
BRIDGE_USER="agent_mail_bridge"
BRIDGE_USER_ID="@${BRIDGE_USER}:${SERVER_NAME}"
REGISTRATION_TOKEN="${MATRIX_REGISTRATION_TOKEN}"

if [ -z "$REGISTRATION_TOKEN" ]; then
    echo -e "${RED}✗ MATRIX_REGISTRATION_TOKEN not set in .env${NC}"
    exit 1
fi

echo -e "${YELLOW}Configuration:${NC}"
echo "  Homeserver: $HOMESERVER_URL"
echo "  Server Name: $SERVER_NAME"
echo "  Bridge User: $BRIDGE_USER_ID"
echo

# Check if already registered
echo -e "${BLUE}→ Checking if bridge user exists...${NC}"
if grep -q "AGENT_MAIL_BRIDGE_ACCESS_TOKEN=" "$PROJECT_ROOT/.env"; then
    echo -e "${GREEN}✓ Bridge user already configured${NC}"
    BRIDGE_TOKEN=$(grep "AGENT_MAIL_BRIDGE_ACCESS_TOKEN=" "$PROJECT_ROOT/.env" | cut -d= -f2)
    
    # Test token
    RESPONSE=$(curl -s -X GET "$HOMESERVER_URL/_matrix/client/v3/account/whoami" \
        -H "Authorization: Bearer $BRIDGE_TOKEN" 2>/dev/null || echo "error")
    
    if echo "$RESPONSE" | grep -q "$BRIDGE_USER_ID"; then
        echo -e "${GREEN}✓ Token is valid${NC}"
        echo
        echo -e "${BLUE}→ Joining agent rooms...${NC}"
        # Join all rooms
        ROOMS=$(jq -r '.[].room_id | select(. != null)' "$PROJECT_ROOT/matrix_client_data/agent_user_mappings.json")
        JOINED=0
        ALREADY_JOINED=0
        
        for ROOM_ID in $ROOMS; do
            RESULT=$(curl -s -X POST "$HOMESERVER_URL/_matrix/client/v3/rooms/${ROOM_ID}/join" \
                -H "Authorization: Bearer $BRIDGE_TOKEN" \
                -H "Content-Type: application/json" \
                -d '{}')
            
            if echo "$RESULT" | grep -q "room_id"; then
                echo -e "  ${GREEN}✓${NC} Joined $ROOM_ID"
                ((JOINED++))
            elif echo "$RESULT" | grep -q "already in the room"; then
                ((ALREADY_JOINED++))
            else
                echo -e "  ${YELLOW}⚠${NC} Could not join $ROOM_ID: $RESULT"
            fi
        done
        
        echo
        echo -e "${GREEN}✓ Room joining complete${NC}"
        echo "  Joined: $JOINED rooms"
        echo "  Already joined: $ALREADY_JOINED rooms"
        exit 0
    else
        echo -e "${YELLOW}⚠ Token is invalid, re-registering...${NC}"
    fi
fi

# Generate password
echo -e "${BLUE}→ Generating password...${NC}"
BRIDGE_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)
echo -e "${GREEN}✓ Password generated${NC}"

# Register user
echo -e "${BLUE}→ Registering bridge user with registration token...${NC}"

# First try to register
REGISTER_RESPONSE=$(curl -s -X POST "$HOMESERVER_URL/_matrix/client/v3/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"username\": \"$BRIDGE_USER\",
        \"password\": \"$BRIDGE_PASSWORD\",
        \"auth\": {
            \"type\": \"m.login.registration_token\",
            \"token\": \"$REGISTRATION_TOKEN\"
        },
        \"initial_device_display_name\": \"Agent Mail Bridge\"
    }" 2>/dev/null)

# Check if user already exists
if echo "$REGISTER_RESPONSE" | grep -q "User ID already taken\|already in use"; then
    echo -e "${YELLOW}⚠ User already exists, logging in instead...${NC}"
elif echo "$REGISTER_RESPONSE" | grep -q "access_token"; then
    echo -e "${GREEN}✓ User registered successfully${NC}"
    # Extract token from registration response
    REG_TOKEN=$(echo "$REGISTER_RESPONSE" | jq -r '.access_token // empty')
    if [ -n "$REG_TOKEN" ] && [ "$REG_TOKEN" != "null" ]; then
        ACCESS_TOKEN="$REG_TOKEN"
        echo -e "${GREEN}✓ Access token obtained from registration${NC}"
        # Skip login step
        SKIP_LOGIN=true
    fi
else
    echo -e "${YELLOW}⚠ Registration response: $REGISTER_RESPONSE${NC}"
fi

# Login to get access token (if not already obtained from registration)
if [ "$SKIP_LOGIN" != "true" ]; then
    echo -e "${BLUE}→ Logging in to get access token...${NC}"
    LOGIN_RESPONSE=$(curl -s -X POST "$HOMESERVER_URL/_matrix/client/v3/login" \
        -H "Content-Type: application/json" \
        -d "{
            \"type\": \"m.login.password\",
            \"identifier\": {
                \"type\": \"m.id.user\",
                \"user\": \"$BRIDGE_USER_ID\"
            },
            \"password\": \"$BRIDGE_PASSWORD\"
        }")

    ACCESS_TOKEN=$(echo "$LOGIN_RESPONSE" | jq -r '.access_token // empty')

    if [ -z "$ACCESS_TOKEN" ] || [ "$ACCESS_TOKEN" = "null" ]; then
        echo -e "${RED}✗ Failed to get access token${NC}"
        echo "Response: $LOGIN_RESPONSE"
        exit 1
    fi

    echo -e "${GREEN}✓ Access token obtained${NC}"
fi

# Save to .env
echo -e "${BLUE}→ Saving to .env...${NC}"
if grep -q "AGENT_MAIL_BRIDGE_USER_ID=" "$PROJECT_ROOT/.env"; then
    sed -i "s|^AGENT_MAIL_BRIDGE_USER_ID=.*|AGENT_MAIL_BRIDGE_USER_ID=$BRIDGE_USER_ID|" "$PROJECT_ROOT/.env"
else
    echo "AGENT_MAIL_BRIDGE_USER_ID=$BRIDGE_USER_ID" >> "$PROJECT_ROOT/.env"
fi

if grep -q "AGENT_MAIL_BRIDGE_ACCESS_TOKEN=" "$PROJECT_ROOT/.env"; then
    sed -i "s|^AGENT_MAIL_BRIDGE_ACCESS_TOKEN=.*|AGENT_MAIL_BRIDGE_ACCESS_TOKEN=$ACCESS_TOKEN|" "$PROJECT_ROOT/.env"
else
    echo "AGENT_MAIL_BRIDGE_ACCESS_TOKEN=$ACCESS_TOKEN" >> "$PROJECT_ROOT/.env"
fi

echo -e "${GREEN}✓ Saved to .env${NC}"

# Join all agent rooms
echo
echo -e "${BLUE}→ Joining all agent rooms...${NC}"
ROOMS=$(jq -r '.[].room_id | select(. != null)' "$PROJECT_ROOT/matrix_client_data/agent_user_mappings.json")
JOINED=0
FAILED=0

for ROOM_ID in $ROOMS; do
    RESULT=$(curl -s -X POST "$HOMESERVER_URL/_matrix/client/v3/rooms/${ROOM_ID}/join" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{}')
    
    if echo "$RESULT" | grep -q "room_id"; then
        echo -e "  ${GREEN}✓${NC} Joined $ROOM_ID"
        ((JOINED++))
    else
        echo -e "  ${RED}✗${NC} Failed to join $ROOM_ID"
        ((FAILED++))
    fi
    sleep 0.1
done

echo
echo -e "${GREEN}✓ Bridge user setup complete!${NC}"
echo
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}Summary:${NC}"
echo "  User ID: $BRIDGE_USER_ID"
echo "  Rooms joined: $JOINED"
echo "  Failed: $FAILED"
echo
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Review configuration in .env"
echo "  2. Start bridge service:"
echo "     ${BLUE}docker compose up -d agent-mail-bridge${NC}"
echo "  3. Check logs:"
echo "     ${BLUE}docker logs -f agent-mail-bridge${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
