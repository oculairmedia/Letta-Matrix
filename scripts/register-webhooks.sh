#!/bin/bash
# Register Letta webhooks for all Matrix-connected agents
# Phase 2: MXSYN-94

LETTA_API="http://192.168.50.90:8283"
LETTA_TOKEN="lettaSecurePass123"
WEBHOOK_URL="http://192.168.50.90:8004/webhooks/letta/agent-response"
WEBHOOK_SECRET="${LETTA_WEBHOOK_SECRET:-}"

# Get agent IDs from matrix-api data
AGENT_IDS=$(docker exec matrix-synapse-deployment-matrix-api-1 cat /app/data/agent_user_mappings.json 2>/dev/null | jq -r '.[] | .agent_id')

if [ -z "$AGENT_IDS" ]; then
    echo "ERROR: Could not retrieve agent IDs from matrix-api"
    exit 1
fi

TOTAL=$(echo "$AGENT_IDS" | wc -l)
SUCCESS=0
FAILED=0
SKIPPED=0

echo "=============================================="
echo "Letta Webhook Registration - Phase 2"
echo "=============================================="
echo "Webhook URL: $WEBHOOK_URL"
echo "Total agents to process: $TOTAL"
echo "Secret configured: $([ -n "$WEBHOOK_SECRET" ] && echo "Yes" || echo "No")"
echo "=============================================="
echo ""

for AGENT_ID in $AGENT_IDS; do
    # Get agent name for logging
    AGENT_NAME=$(docker exec matrix-synapse-deployment-matrix-api-1 cat /app/data/agent_user_mappings.json 2>/dev/null | jq -r ".[] | select(.agent_id == \"$AGENT_ID\") | .display_name // .agent_name // \"unknown\"")
    
    # Check current webhook config
    CURRENT=$(curl -s "${LETTA_API}/v1/agents/${AGENT_ID}/webhook" \
        -H "Authorization: Bearer ${LETTA_TOKEN}" 2>/dev/null)
    
    CURRENT_URL=$(echo "$CURRENT" | jq -r '.url // empty')
    CURRENT_ENABLED=$(echo "$CURRENT" | jq -r '.enabled // false')
    
    if [ "$CURRENT_URL" = "$WEBHOOK_URL" ] && [ "$CURRENT_ENABLED" = "true" ]; then
        echo "⏭️  SKIP: $AGENT_NAME - webhook already configured"
        ((SKIPPED++))
        continue
    fi
    
    # Build webhook config payload
    if [ -n "$WEBHOOK_SECRET" ]; then
        PAYLOAD=$(jq -n \
            --arg url "$WEBHOOK_URL" \
            --arg secret "$WEBHOOK_SECRET" \
            '{url: $url, secret: $secret, events: ["agent.run.completed"], enabled: true}')
    else
        PAYLOAD=$(jq -n \
            --arg url "$WEBHOOK_URL" \
            '{url: $url, events: ["agent.run.completed"], enabled: true}')
    fi
    
    # Register webhook
    RESPONSE=$(curl -s -X PUT "${LETTA_API}/v1/agents/${AGENT_ID}/webhook" \
        -H "Authorization: Bearer ${LETTA_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" 2>/dev/null)
    
    if echo "$RESPONSE" | jq -e '.url' > /dev/null 2>&1; then
        echo "✅ OK: $AGENT_NAME"
        ((SUCCESS++))
    else
        ERROR=$(echo "$RESPONSE" | jq -r '.detail // .message // "Unknown error"' 2>/dev/null || echo "$RESPONSE")
        echo "❌ FAIL: $AGENT_NAME - $ERROR"
        ((FAILED++))
    fi
done

echo ""
echo "=============================================="
echo "Registration Complete"
echo "=============================================="
echo "✅ Success: $SUCCESS"
echo "⏭️  Skipped: $SKIPPED"
echo "❌ Failed:  $FAILED"
echo "=============================================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
