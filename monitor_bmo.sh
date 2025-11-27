#!/bin/bash
echo "Monitoring for BMO messages and routing..."
echo "Send a message to BMO's room now..."
echo ""
docker logs matrix-synapse-deployment-matrix-client-1 -f 2>&1 | grep --line-buffered -E "(tfSmwhqAWH3xZhN623|BMO|AGENT ROUTING.*agent-f2fdf2aa|Received message.*BMO)"
