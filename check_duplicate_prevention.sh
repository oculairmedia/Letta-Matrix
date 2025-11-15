#!/bin/bash
# Diagnostic script to check duplicate message prevention implementation

echo "=== Duplicate Message Prevention Diagnostic ==="
echo ""

# Check if matrix-client container is running
echo "1. Checking matrix-client container status..."
CONTAINER_COUNT=$(docker ps --filter "name=matrix-client" --format "{{.Names}}" | wc -l)
echo "   Matrix-client containers running: $CONTAINER_COUNT"
if [ "$CONTAINER_COUNT" -gt 1 ]; then
    echo "   ⚠️  WARNING: Multiple matrix-client containers detected!"
    docker ps --filter "name=matrix-client"
else
    echo "   ✅ OK: Single container instance"
fi
echo ""

# Check dedupe database
echo "2. Checking event dedupe database..."
if [ -f "matrix_client_data/matrix_event_dedupe.db" ]; then
    DB_SIZE=$(ls -lh matrix_client_data/matrix_event_dedupe.db | awk '{print $5}')
    echo "   ✅ Database exists: $DB_SIZE"
    
    # Count events in database
    EVENT_COUNT=$(sqlite3 matrix_client_data/matrix_event_dedupe.db "SELECT COUNT(*) FROM processed_events;" 2>/dev/null || echo "N/A")
    echo "   Events in database: $EVENT_COUNT"
    
    # Show oldest and newest events
    OLDEST=$(sqlite3 matrix_client_data/matrix_event_dedupe.db "SELECT datetime(MIN(processed_at), 'unixepoch') FROM processed_events;" 2>/dev/null || echo "N/A")
    NEWEST=$(sqlite3 matrix_client_data/matrix_event_dedupe.db "SELECT datetime(MAX(processed_at), 'unixepoch') FROM processed_events;" 2>/dev/null || echo "N/A")
    echo "   Oldest event: $OLDEST"
    echo "   Newest event: $NEWEST"
else
    echo "   ⚠️  WARNING: Dedupe database not found!"
fi
echo ""

# Check for duplicate detection in logs
echo "3. Checking recent logs for duplicate events..."
DUPLICATE_COUNT=$(docker logs matrix-synapse-deployment-matrix-client-1 2>&1 | grep -c "Duplicate Matrix event detected" || echo "0")
echo "   Duplicate events detected (all time): $DUPLICATE_COUNT"

if [ "$DUPLICATE_COUNT" -gt 0 ]; then
    echo "   Recent duplicate detections:"
    docker logs matrix-synapse-deployment-matrix-client-1 2>&1 | grep "Duplicate Matrix event detected" | tail -5
fi
echo ""

# Check for message processing
echo "4. Checking message processing..."
PROCESSED_COUNT=$(docker logs matrix-synapse-deployment-matrix-client-1 2>&1 | grep -c "Received message from user" || echo "0")
echo "   Messages processed (all time): $PROCESSED_COUNT"
echo ""

# Check for errors
echo "5. Checking for errors..."
ERROR_COUNT=$(docker logs matrix-synapse-deployment-matrix-client-1 2>&1 | grep -c "ERROR" || echo "0")
echo "   Errors in logs: $ERROR_COUNT"

if [ "$ERROR_COUNT" -gt 0 ]; then
    echo "   Recent errors:"
    docker logs matrix-synapse-deployment-matrix-client-1 2>&1 | grep "ERROR" | tail -5
fi
echo ""

# Check environment variables
echo "6. Checking configuration..."
TTL=$(docker exec matrix-synapse-deployment-matrix-client-1 printenv MATRIX_EVENT_DEDUPE_TTL 2>/dev/null || echo "3600 (default)")
echo "   Event dedupe TTL: $TTL seconds"
echo ""

# Summary
echo "=== Summary ==="
if [ "$CONTAINER_COUNT" -eq 1 ] && [ -f "matrix_client_data/matrix_event_dedupe.db" ]; then
    echo "✅ Duplicate prevention appears to be properly configured"
    echo ""
    echo "Duplicate detection rate: $DUPLICATE_COUNT / $PROCESSED_COUNT messages"
    if [ "$PROCESSED_COUNT" -gt 0 ]; then
        RATE=$(echo "scale=2; $DUPLICATE_COUNT * 100 / $PROCESSED_COUNT" | bc 2>/dev/null || echo "N/A")
        echo "Percentage: ${RATE}%"
    fi
else
    echo "⚠️  Issues detected - review warnings above"
fi
echo ""
echo "For detailed logs, run:"
echo "  docker logs matrix-synapse-deployment-matrix-client-1 | grep -E 'Duplicate|Received message'"

