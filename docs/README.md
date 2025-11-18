# Letta-Matrix Integration

A comprehensive Matrix deployment with Letta AI bot integration and MCP (Model Context Protocol) server support.

## ⚠️ CRITICAL: We Use Tuwunel, NOT Synapse! ⚠️

**THIS DEPLOYMENT USES TUWUNEL AS THE MATRIX HOMESERVER, NOT SYNAPSE!**

- **Homeserver**: Tuwunel (lightweight embedded Matrix server)
- **NO PostgreSQL**: Tuwunel uses RocksDB (embedded database)
- **NO Synapse Admin APIs**: Standard Synapse admin endpoints DO NOT work
- **Admin Tools**: Use Matrix client APIs or Tuwunel-specific tools only

**Common Mistakes to Avoid:**
- ❌ Trying to use `/_synapse/admin/v1/*` endpoints (they don't exist)
- ❌ Looking for PostgreSQL database or connection strings
- ❌ Following Synapse-specific documentation
- ✅ Use Matrix client API (`/_matrix/client/v3/*`) instead
- ✅ Remember: Tuwunel is a different server implementation!

See [TUWUNEL_MIGRATION.md](./TUWUNEL_MIGRATION.md) for full details.

---

## Latest Updates (2025-01-04)
- **Stable Agent Usernames**: Matrix usernames are now based on agent IDs instead of agent names, ensuring stability even when agents are renamed
- **Fixed Session Management**: Resolved session scope issues in invitation handling  
- **Improved Agent Sync**: All agent rooms are properly monitored and agents respond with their own Matrix identities

A complete, self-contained Matrix deployment with Element web client and Matrix bot integration.

## Quick Start

1. **Prerequisites**: Docker and Docker Compose installed on your system

2. **Deploy**: Simply run:
   ```bash
   docker-compose up -d
   ```

3. **Access**: 
   - Element Web Client: http://localhost:8008
   - Matrix Server: http://localhost:8008/_matrix/

## What's Included

- **Tuwunel**: Lightweight Matrix homeserver (NOT Synapse!)
- **RocksDB**: Embedded database (NO PostgreSQL)
- **Element Web**: Modern Matrix web client
- **Nginx**: Reverse proxy for routing
- **Matrix Client**: Custom bot integration with Letta agents
- **MCP Server**: Model Context Protocol server for tool integration
- **Letta Agent MCP**: Letta-specific MCP tools and agent management

## Configuration

The deployment is pre-configured with sensible defaults in `.env`. Key settings:

- **Server Name**: `matrix.oculair.ca` (change this to your domain)
- **Database**: RocksDB embedded in Tuwunel (NO separate database service)
- **Ports**: Exposed on port 8008 (via nginx proxy)
- **Registration**: Enabled without verification for easy setup
- **Tuwunel Data**: Stored in `./tuwunel-data/` directory

## Customization

### Change Server Name
Edit `.env` and update:
```
SYNAPSE_SERVER_NAME=your-domain.com
```

Also update `nginx_matrix_proxy.conf`:
```
server_name your-domain.com;
```

### Data Persistence
All data is stored in local directories:
- `./tuwunel-data/`: Tuwunel's RocksDB database (ALL homeserver data)
- `./synapse-data/`: Legacy configuration files (registration configs for bridges)
- `./matrix_store/`: Matrix client session data
- `./matrix_client_data/`: Agent mappings and space configuration

## Security Notes

- Default passwords are set for development/testing
- Change all passwords in `.env` for production use
- Configure proper SSL/TLS termination for production
- Review and adjust registration settings as needed

## Troubleshooting

### First Time Setup
The system automatically generates configuration files on first run. This may take a few minutes.

### Logs
View service logs:
```bash
docker-compose logs synapse
docker-compose logs db
docker-compose logs nginx
```

### Registration Issues
If user registration fails, ensure `enable_registration_without_verification: true` is set in the generated `synapse-data/homeserver.yaml`.

## Services

- **synapse**: Matrix homeserver (port 8008)
- **db**: PostgreSQL database
- **element**: Element web client
- **nginx**: Reverse proxy
- **matrix-client**: Custom Matrix bot (optional)

Stop services:
```bash
docker-compose down
```

Remove all data (destructive):
```bash
docker-compose down -v
rm -rf synapse-data postgres-data matrix_store
```

## Files Overview

- `docker-compose.yml` - Service definitions
- `.env` - Configuration variables
- `synapse_entrypoint.sh` - Synapse initialization script
- `nginx_matrix_proxy.conf` - Nginx routing configuration
- `element-config.json` - Element web client config
- `Dockerfile.matrix-client` - Matrix bot container
- `requirements.txt` - Python dependencies
- `custom_matrix_client.py` - Matrix bot implementation
- `matrix_auth.py` - Authentication utilities