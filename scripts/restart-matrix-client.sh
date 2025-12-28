#!/bin/bash
# Restart matrix-client container to pick up code changes
# Run this after modifying src/ files

set -e

CONTAINER="matrix-synapse-deployment-matrix-client-1"

echo "Restarting $CONTAINER to pick up code changes..."
docker restart "$CONTAINER"

echo "Waiting for container to be healthy..."
for i in {1..30}; do
    STATUS=$(docker inspect "$CONTAINER" --format '{{.State.Health.Status}}' 2>/dev/null || echo "unknown")
    if [ "$STATUS" = "healthy" ]; then
        echo "Container is healthy!"
        exit 0
    fi
    echo "  Status: $STATUS (attempt $i/30)"
    sleep 2
done

echo "Warning: Container did not become healthy within 60 seconds"
docker logs "$CONTAINER" --tail 20
exit 1
