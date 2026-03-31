# Matrix API Deployment Instructions

## Files to Transfer to Production Server

Transfer these files to `root@192.168.50.90:/opt/stacks/matrix-synapse-deployment/`:

1. `matrix_api.py` - Matrix API source code with recent messages endpoint
2. `Dockerfile.matrix-api` - Docker build configuration for Matrix API  
3. `docker-compose.yml` - Updated with matrix-api service

## Transfer Commands

```bash
# From local development machine:
scp matrix_api.py root@192.168.50.90:/opt/stacks/matrix-synapse-deployment/
scp Dockerfile.matrix-api root@192.168.50.90:/opt/stacks/matrix-synapse-deployment/
scp docker-compose.yml root@192.168.50.90:/opt/stacks/matrix-synapse-deployment/
```

## Deployment Commands

```bash
# On production server:
ssh root@192.168.50.90
cd /opt/stacks/matrix-synapse-deployment

# Build and start the Matrix API service
docker-compose build matrix-api
docker-compose up -d matrix-api

# Verify deployment
docker-compose ps
curl http://localhost:8001/health
```

## New Matrix API Endpoints

The Matrix API will be available on port 8001 with these endpoints:

- `GET /health` - Health check
- `GET /login/auto` - Auto-login with environment credentials
- `POST /login` - Manual login
- `GET /rooms/list` - List joined rooms
- `POST /messages/send` - Send messages
- `POST /messages/get` - Get messages from specific room
- **`GET /messages/recent`** - **NEW: Get 10 most recent messages across all rooms**
- `GET /docs` - Interactive API documentation

## Recent Messages Endpoint Usage

```bash
# Get 10 most recent messages across all rooms
curl "http://localhost:8001/messages/recent?homeserver=https://matrix.oculair.ca&access_token=TOKEN&limit=10"

# Get 5 most recent messages
curl "http://localhost:8001/messages/recent?homeserver=https://matrix.oculair.ca&access_token=TOKEN&limit=5"
```

## Environment Variables

The Matrix API uses the same `.env` file as the existing services. No additional configuration needed.

## Verification

After deployment, verify the API is working:

```bash
# Test health endpoint
curl http://localhost:8001/health

# Test auto-login
curl http://localhost:8001/login/auto

# Test recent messages (after getting token)
curl "http://localhost:8001/messages/recent?homeserver=https://matrix.oculair.ca&access_token=YOUR_TOKEN&limit=10"