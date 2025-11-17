# Troubleshooting Guide

Comprehensive troubleshooting guide for the Letta-Matrix integration.

## Quick Diagnostics

### Service Health Check

```bash
# Check all services are running
docker-compose ps

# Expected: All services "Up" and "healthy"
# matrix-client      Up (healthy)
# matrix-api         Up
# mcp-server         Up
# element            Up
# nginx              Up
```

### API Health Endpoints

```bash
# Matrix API
curl http://localhost:8004/health
# Expected: {"status": "healthy"}

# MCP Server HTTP
curl http://localhost:8016/health
# Expected: {"status": "ok"}

# Letta Agent MCP
curl http://localhost:8017/health
# Expected: 200 OK
```

### Log Inspection

```bash
# View recent logs
docker-compose logs --tail=50 matrix-client

# Follow live logs
docker-compose logs -f matrix-client

# Search for errors
docker-compose logs matrix-client 2>&1 | grep -i error

# Check specific service
docker-compose logs matrix-api
```

## Common Issues by Category

### Agent Communication Issues

#### Issue: Agents Not Responding to Messages

**Symptoms**:
- Messages sent to agent rooms get no response
- Logs show no message processing
- Agent appears offline

**Diagnostic Steps**:
```bash
# 1. Check matrix-client is running
docker-compose ps matrix-client

# 2. Check for errors in logs
docker-compose logs matrix-client 2>&1 | grep -i error | tail -20

# 3. Verify agent sync is working
docker-compose logs matrix-client 2>&1 | grep "agent sync" | tail -10

# 4. Check agent mappings exist
jq '.' matrix_client_data/agent_user_mappings.json

# 5. Verify room ID matches
jq '.[] | select(.agent_name == "Meridian") | .room_id' \
  matrix_client_data/agent_user_mappings.json
```

**Common Causes**:

1. **Wrong Letta API Endpoint**
   ```bash
   # Check current endpoint
   docker exec matrix-client grep "agents_endpoint" /app/agent_user_manager.py

   # Should be: http://192.168.50.90:1416/v1/models
   # NOT: http://192.168.50.90:8283/v1/agents/
   ```

   **Fix**: Update endpoint in agent_user_manager.py and restart

2. **Stale Agent Mappings**
   ```bash
   # Check for deleted agents still in mappings
   jq 'keys' matrix_client_data/agent_user_mappings.json

   # Compare with active agents
   curl http://192.168.50.90:1416/v1/models | jq '.data[].id'
   ```

   **Fix**: Remove stale entries:
   ```bash
   # Backup first
   cp matrix_client_data/agent_user_mappings.json \
      matrix_client_data/agent_user_mappings.json.backup

   # Edit to remove deleted agents
   docker exec -it matrix-client python3 << 'EOF'
   import json
   with open('/app/data/agent_user_mappings.json', 'r') as f:
       mappings = json.load(f)
   del mappings['agent-old-id']  # Remove stale agent
   with open('/app/data/agent_user_mappings.json', 'w') as f:
       json.dump(mappings, f, indent=2)
   EOF

   docker-compose restart matrix-client
   ```

3. **Invitation Retry Loops**
   ```bash
   # Check for invitation loops
   docker-compose logs matrix-client 2>&1 | grep "invite" | tail -20
   ```

   **Fix**: Temporary - invitation management disabled. See MATRIX_FIXES_2025_01_07.md

#### Issue: Wrong Agent Responding

**Symptoms**:
- Message to "Meridian" room gets response from "Personal Site"
- Logs show incorrect agent routing
- First agent in list always responds

**Diagnostic Steps**:
```bash
# 1. Check routing logs
docker logs matrix-client 2>&1 | grep "AGENT ROUTING" | tail -10

# Expected: Room !8I9YBvbr4KpXNedbph -> Agent agent-597b5756...
# Problem: Shows different agent ID

# 2. Verify room-to-agent mapping
jq '.[] | {agent_name, room_id}' matrix_client_data/agent_user_mappings.json

# 3. Run routing tests
pytest test_agent_routing.py -v

# 4. Check for SDK imports (should find nothing)
grep -r "from letta import\|import letta" custom_matrix_client.py
```

**Root Cause**: Letta SDK pagination bug (returns only first 50 agents)

**Fix**: Use direct HTTP API calls instead of SDK
```python
# CORRECT - Direct HTTP
async with aiohttp.ClientSession() as session:
    url = f"{LETTA_API_URL}/v1/agents/{agent_id}/messages"
    async with session.post(url, json=payload) as response:
        return await response.json()

# WRONG - SDK (has pagination bug)
from letta import LettaClient
response = client.send_message(agent_id, message)
```

**Verification**:
```bash
# Run regression tests
pytest test_agent_routing.py::test_correct_agent_routing_for_meridian_room -v
pytest test_agent_routing.py::test_no_letta_sdk_imports -v
```

#### Issue: Agent Responds as @letta Instead of Own Identity

**Symptoms**:
- All responses come from @letta user
- Agent identity not preserved
- Logs show `sent_as_agent: false`

**Diagnostic Steps**:
```bash
# Check identity logs
docker logs matrix-client 2>&1 | grep "SEND_AS_AGENT" | tail -10

# Expected: Successfully sent message as Meridian (agent_597b5756...)
# Expected: sent_as_agent: true
# Problem: sent_as_agent: false or missing
```

**Fix**: Ensure using agent credentials for responses
```python
# CORRECT - Send as agent
async def send_as_agent(agent_id, room_id, message):
    mapping = agent_mappings[agent_id]
    # Login as agent
    headers = {
        "Authorization": f"Bearer {agent_token}"
    }
    # Send message as agent user
    await send_message(room_id, message, headers)

# WRONG - Send as @letta
await client.room_send(room_id, "m.room.message", content)
```

**Verification**:
```bash
# Run identity tests
pytest test_agent_response_identity.py -v
```

### Service Startup Issues

#### Issue: Container Fails to Start

**Symptoms**:
- `docker-compose ps` shows "Exit 1" or "Restarting"
- Service keeps crashing

**Diagnostic Steps**:
```bash
# 1. Check exit status
docker-compose ps

# 2. View container logs
docker-compose logs matrix-client

# 3. Check for dependency issues
docker-compose logs matrix-client 2>&1 | grep "waiting for"

# 4. Verify configuration
cat .env | grep -v "^#" | grep MATRIX
```

**Common Causes**:

1. **Missing Dependencies**
   ```bash
   # Check dependency health
   docker-compose ps matrix-api
   docker-compose ps mcp-server

   # Start dependencies first
   docker-compose up -d matrix-api mcp-server
   sleep 10
   docker-compose up -d matrix-client
   ```

2. **Configuration Errors**
   ```bash
   # Validate .env file
   cat .env | grep MATRIX_HOMESERVER_URL
   # Should be: http://tuwunel:6167

   # Check credentials
   cat .env | grep MATRIX_USERNAME
   cat .env | grep MATRIX_PASSWORD
   ```

3. **Port Conflicts**
   ```bash
   # Check if ports are in use
   sudo netstat -tulpn | grep -E "8004|8015|8016|8017"

   # If in use, change ports in docker-compose.yml
   ```

#### Issue: Service Unhealthy After Start

**Symptoms**:
- Container runs but healthcheck fails
- Status shows "unhealthy"

**Diagnostic Steps**:
```bash
# 1. Check healthcheck definition
docker inspect matrix-client | jq '.[0].Config.Healthcheck'

# 2. Run healthcheck manually
docker exec matrix-client python -c "import sys; sys.exit(0)"

# 3. Check service logs
docker-compose logs matrix-client --tail=100
```

**Fix**: Adjust healthcheck timing
```yaml
# docker-compose.yml
healthcheck:
  test: ["CMD", "python", "-c", "import sys; sys.exit(0)"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 60s  # Increase if slow startup
```

### Matrix Homeserver Issues

#### Issue: Cannot Connect to Matrix Homeserver

**Symptoms**:
- "Connection refused" errors
- "Unable to reach homeserver" logs
- Authentication failures

**Diagnostic Steps**:
```bash
# 1. Check homeserver accessibility
curl http://tuwunel:6167/_matrix/client/versions
# Expected: {"versions": [...]}

# 2. From inside container
docker exec matrix-client curl http://tuwunel:6167/_matrix/client/versions

# 3. Check network configuration
docker network ls
docker network inspect letta-matrix_matrix-internal
```

**Common Causes**:

1. **Homeserver Not Running**
   ```bash
   # Check Tuwunel server
   ssh user@tuwunel "systemctl status matrix-synapse"
   ```

2. **Network Misconfiguration**
   ```yaml
   # docker-compose.yml - Ensure services on same network
   networks:
     matrix-internal:
       driver: bridge
   ```

3. **Wrong URL**
   ```bash
   # Check environment
   docker exec matrix-client env | grep MATRIX_HOMESERVER_URL
   # Should be: http://tuwunel:6167
   ```

#### Issue: Authentication Failures

**Symptoms**:
- "Invalid username or password"
- "M_FORBIDDEN" errors
- Cannot login as @letta or @matrixadmin

**Diagnostic Steps**:
```bash
# 1. Test authentication manually
curl -X POST "http://tuwunel:6167/_matrix/client/r0/login" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "m.login.password",
    "user": "letta",
    "password": "letta"
  }'

# Expected: {"access_token": "...", "user_id": "@letta:matrix.oculair.ca"}

# 2. Check credentials in .env
cat .env | grep -E "MATRIX_USERNAME|MATRIX_PASSWORD|MATRIX_ADMIN"

# 3. Verify user exists on homeserver
curl -X GET "http://tuwunel:6167/_synapse/admin/v2/users/@letta:matrix.oculair.ca" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Fix**: Reset user password on homeserver
```bash
# On Tuwunel server
ssh tuwunel
docker exec -it synapse register_new_matrix_user \
  -u letta -p letta -a \
  -c /data/homeserver.yaml \
  http://localhost:8008
```

### Data Persistence Issues

#### Issue: Agent Mappings Lost After Restart

**Symptoms**:
- All agents recreated on restart
- Room IDs change
- Duplicate rooms created

**Diagnostic Steps**:
```bash
# 1. Check volume mounts
docker-compose config | grep -A 5 "matrix_client_data"

# 2. Verify data directory exists
ls -la matrix_client_data/

# 3. Check file permissions
ls -la matrix_client_data/agent_user_mappings.json

# 4. Check file content
jq '.' matrix_client_data/agent_user_mappings.json
```

**Fix**: Ensure correct volume mapping
```yaml
# docker-compose.yml
services:
  matrix-client:
    volumes:
      - ./matrix_client_data:/app/data  # Correct path
```

**Restore from Backup**:
```bash
# If mappings lost, restore from backup
cp matrix_client_data/agent_user_mappings.json.backup \
   matrix_client_data/agent_user_mappings.json

docker-compose restart matrix-client
```

#### Issue: Space Configuration Lost

**Symptoms**:
- "Letta Agents" space recreated on every restart
- Multiple duplicate spaces
- Rooms not appearing in space

**Diagnostic Steps**:
```bash
# 1. Check space config exists
cat matrix_client_data/letta_space_config.json

# 2. Verify space ID is valid
jq '.space_id' matrix_client_data/letta_space_config.json

# 3. Check space exists on homeserver
# (Use Matrix API to query space)
```

**Fix**: Ensure space config persists
```bash
# Create config if missing
echo '{
  "space_id": "!actual_space_id:matrix.oculair.ca",
  "created_at": '$(date +%s)',
  "name": "Letta Agents"
}' > matrix_client_data/letta_space_config.json

docker-compose restart matrix-client
```

### Performance Issues

#### Issue: Slow Agent Synchronization

**Symptoms**:
- New agents take >30 seconds to appear
- Sync logs show delays
- High CPU usage

**Diagnostic Steps**:
```bash
# 1. Check sync interval
docker exec matrix-client grep "interval=" /app/custom_matrix_client.py

# 2. Monitor sync performance
docker logs matrix-client 2>&1 | grep "agent sync" | tail -20

# 3. Check agent count
jq '. | length' matrix_client_data/agent_user_mappings.json

# 4. Monitor resource usage
docker stats matrix-client
```

**Optimization**:
```python
# Reduce sync interval (custom_matrix_client.py)
async def periodic_agent_sync(config, logger, interval=0.5):  # Was 60
    while True:
        await asyncio.sleep(interval)
        await run_agent_sync(config)
```

**Warning**: Too frequent sync can overload Letta API

#### Issue: High Memory Usage

**Symptoms**:
- Container using >1GB RAM
- Out of memory errors
- System slowdown

**Diagnostic Steps**:
```bash
# 1. Check memory usage
docker stats --no-stream matrix-client

# 2. Check for memory leaks
docker logs matrix-client 2>&1 | grep -i "memory\|oom"

# 3. Inspect process memory
docker exec matrix-client ps aux
```

**Fix**: Set memory limits
```yaml
# docker-compose.yml
services:
  matrix-client:
    deploy:
      resources:
        limits:
          memory: 512M
        reservations:
          memory: 256M
```

### Network Issues

#### Issue: Cannot Access Services from Host

**Symptoms**:
- Cannot reach http://localhost:8004
- curl fails with "Connection refused"
- Services work inside containers

**Diagnostic Steps**:
```bash
# 1. Check port bindings
docker-compose ps

# 2. Verify ports are exposed
docker-compose config | grep -A 10 "ports:"

# 3. Check firewall
sudo iptables -L -n | grep 8004

# 4. Test from inside container
docker exec matrix-client curl http://localhost:8000/health
```

**Fix**: Ensure correct port mapping
```yaml
# docker-compose.yml
services:
  matrix-api:
    ports:
      - "8004:8000"  # host:container
```

#### Issue: Services Cannot Communicate

**Symptoms**:
- matrix-client cannot reach matrix-api
- "Host not found" errors
- Connection timeouts

**Diagnostic Steps**:
```bash
# 1. Check all services on same network
docker network inspect letta-matrix_matrix-internal

# 2. Test connectivity
docker exec matrix-client ping matrix-api
docker exec matrix-client curl http://matrix-api:8000/health

# 3. Check DNS resolution
docker exec matrix-client nslookup matrix-api
```

**Fix**: Ensure network configuration
```yaml
# docker-compose.yml
networks:
  matrix-internal:
    driver: bridge

services:
  matrix-client:
    networks:
      - matrix-internal
  matrix-api:
    networks:
      - matrix-internal
```

## Debug Techniques

### Enable Debug Logging

```python
# In custom_matrix_client.py or agent_user_manager.py
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
```

### Interactive Python Shell

```bash
# Access running container
docker exec -it matrix-client python3

# Import and test components
>>> from agent_user_manager import AgentUserManager
>>> import json
>>> with open('/app/data/agent_user_mappings.json') as f:
...     mappings = json.load(f)
>>> print(mappings.keys())
```

### Network Traffic Inspection

```bash
# Install tcpdump in container
docker exec -it matrix-client apt-get update && apt-get install -y tcpdump

# Capture traffic
docker exec matrix-client tcpdump -i any -w /tmp/capture.pcap

# Copy and analyze
docker cp matrix-client:/tmp/capture.pcap .
wireshark capture.pcap
```

### Performance Profiling

```bash
# Add profiling to Python code
import cProfile
import pstats

profiler = cProfile.Profile()
profiler.enable()

# ... code to profile ...

profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats('cumulative')
stats.print_stats(20)
```

## Service-Specific Issues

### Matrix API Service

#### Health Check Fails

```bash
# Check if FastAPI is running
docker exec matrix-api ps aux | grep uvicorn

# Test endpoint directly
docker exec matrix-api curl http://localhost:8000/health

# Check logs
docker-compose logs matrix-api | tail -50
```

### MCP Server

#### WebSocket Connection Issues

```bash
# Check WebSocket port
curl -i -N \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: test" \
  http://localhost:8015/

# Expected: HTTP/1.1 101 Switching Protocols
```

#### HTTP Streaming Issues

```bash
# Test HTTP endpoint
curl http://localhost:8016/health

# Check for streaming capability
curl -N http://localhost:8016/stream/test
```

### Letta Agent MCP

#### Cannot Connect to Letta API

```bash
# Check Letta API accessibility
curl http://192.168.50.90:8289/health

# From container
docker exec letta-agent-mcp curl http://192.168.50.90:8289/health

# Check environment
docker exec letta-agent-mcp env | grep LETTA_API_URL
```

## Recovery Procedures

### Complete System Reset

```bash
# 1. Stop all services
docker-compose down

# 2. Backup data
tar -czf backup-$(date +%Y%m%d_%H%M%S).tar.gz \
  matrix_client_data/ matrix_store/ mcp_data/

# 3. Clear all data
rm -rf matrix_client_data/* matrix_store/* mcp_data/*

# 4. Initialize fresh
echo '{}' > matrix_client_data/agent_user_mappings.json

# 5. Restart
docker-compose up -d

# 6. Monitor initialization
docker-compose logs -f matrix-client
```

### Rollback to Previous Version

```bash
# 1. Check available images
docker images | grep letta-matrix

# 2. Use specific version
IMAGE_TAG=v1.0.0 docker-compose up -d

# Or edit docker-compose.yml
# image: ghcr.io/oculairmedia/letta-matrix-client:v1.0.0
```

### Restore from Backup

```bash
# 1. Stop services
docker-compose down

# 2. Extract backup
tar -xzf backup-20250117_120000.tar.gz

# 3. Verify files
ls -la matrix_client_data/

# 4. Restart
docker-compose up -d
```

## Monitoring and Alerts

### Set Up Log Monitoring

```bash
# Install log monitoring tool
pip install loguru

# Monitor for errors
tail -f $(docker inspect --format='{{.LogPath}}' matrix-client) | \
  grep -i error
```

### Health Check Script

```bash
#!/bin/bash
# health_check.sh

# Check services
for service in matrix-client matrix-api mcp-server; do
  status=$(docker-compose ps -q $service)
  if [ -z "$status" ]; then
    echo "ERROR: $service is not running"
    exit 1
  fi
done

# Check API health
curl -f http://localhost:8004/health || {
  echo "ERROR: matrix-api health check failed"
  exit 1
}

curl -f http://localhost:8016/health || {
  echo "ERROR: mcp-server health check failed"
  exit 1
}

echo "All services healthy"
exit 0
```

### Automated Alerts

```bash
# crontab entry
*/5 * * * * /path/to/health_check.sh || mail -s "Letta-Matrix Alert" admin@example.com
```

## Getting Help

### Information to Collect

When reporting issues, include:

1. **Service Status**
   ```bash
   docker-compose ps > status.txt
   ```

2. **Logs**
   ```bash
   docker-compose logs --tail=200 matrix-client > logs.txt
   ```

3. **Configuration**
   ```bash
   # Redact sensitive info
   cat .env | sed 's/PASSWORD=.*/PASSWORD=REDACTED/' > config.txt
   ```

4. **Agent Mappings**
   ```bash
   jq '.' matrix_client_data/agent_user_mappings.json > mappings.txt
   ```

5. **System Info**
   ```bash
   docker version > system.txt
   docker-compose version >> system.txt
   uname -a >> system.txt
   ```

### Support Channels

- **GitHub Issues**: Include collected information above
- **Documentation**: Review docs/operations/ guides
- **Testing**: Run `./run_tests.sh all` and include results
- **Logs**: Share relevant error messages (redact sensitive data)

## Related Documentation

- **Deployment Guide**: docs/operations/DEPLOYMENT.md
- **Testing Guide**: docs/operations/TESTING.md
- **CI/CD Guide**: docs/operations/CI_CD.md
- **Architecture Overview**: docs/architecture/OVERVIEW.md
- **Matrix Fixes**: docs/MATRIX_FIXES_2025_01_07.md

---

**Last Updated**: 2025-01-17
**Version**: 1.0
**Maintainers**: OculairMedia Development Team
