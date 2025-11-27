#!/bin/bash

echo "=========================================="
echo "COMPLETE ROUTING DIAGNOSTIC FOR BMO"
echo "=========================================="
echo ""

echo "1. BMO's expected room:"
echo "   !tfSmwhqAWH3xZhN623:matrix.oculair.ca"
echo ""

echo "2. Default agent (fallback):"
echo "   letta-cli-agent (agent-1f239533-81c1-40b2-95b5-8687e11bd9f6)"
echo ""

echo "3. Testing ORM lookup:"
docker exec matrix-synapse-deployment-matrix-client-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.models.agent_mapping import AgentMappingDB
db = AgentMappingDB()
mapping = db.get_by_room_id('!tfSmwhqAWH3xZhN623:matrix.oculair.ca')
if mapping:
    print(f'   ✅ ORM finds: {mapping.agent_name}')
else:
    print('   ❌ ORM returns None!')
"
echo ""

echo "4. Recent routing events:"
docker logs matrix-synapse-deployment-matrix-client-1 2>&1 --tail 200 | grep "\[DEBUG\] AGENT ROUTING" | tail -5
echo ""

echo "5. Recent messages to any BMO-related room:"
docker logs matrix-synapse-deployment-matrix-client-1 2>&1 --tail 500 | grep -i "bmo\|tfSmwhqAWH3xZhN623" | tail -10
echo ""

echo "=========================================="
echo "INSTRUCTIONS:"
echo "=========================================="
echo ""
echo "Now send a test message to BMO's room in Element"
echo "Then run this to see what happened:"
echo ""
echo "docker logs matrix-synapse-deployment-matrix-client-1 --tail 50 | grep -A 5 -B 5 'Received message'"
echo ""
echo "Look for:"
echo " - Which room_id is in the logs"
echo " - What [DEBUG] AGENT ROUTING says"
echo " - Which agent_id it routes to"
echo ""

