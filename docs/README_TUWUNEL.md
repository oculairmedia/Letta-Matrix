# Tuwunel Migration - Quick Reference

## Branch Info
- **Branch**: `feature/tuwunel-migration`
- **Status**: Ready for deployment
- **Created**: November 13, 2025

## What This Branch Does

Replaces Synapse (Python) with Tuwunel (Rust) while keeping all Letta integration intact.

## Quick Deploy

```bash
./deploy-tuwunel.sh
```

## What's Included

✅ **Tuwunel** - Rust-based Matrix homeserver  
✅ **Element Web** - Matrix web client  
✅ **Nginx** - Reverse proxy (HTTP/Federation)  
✅ **Matrix Client** - Agent sync & responses  
✅ **Matrix API** - REST API service  
✅ **MCP Servers** - Matrix tools integration  
✅ **Letta Agent MCP** - Inter-agent comms  

## Key Benefits

- 🚀 **10x faster** than Synapse
- 💾 **10x less memory** usage
- 📦 **No PostgreSQL** needed
- ⚡ **5 second startup** vs 30 seconds
- 🦀 **Rust performance** and reliability

## Files

| File | Purpose |
|------|---------|
| `docker-compose.tuwunel.yml` | Full stack definition |
| `.env.tuwunel` | Environment config |
| `nginx_tuwunel_proxy.conf` | Nginx reverse proxy |
| `deploy-tuwunel.sh` | Automated deployment |
| `TUWUNEL_MIGRATION.md` | Full migration guide |
| `BRANCH_SUMMARY.md` | Branch details |

## Access Points

- Element Web: http://matrix.oculair.ca
- Matrix API: http://192.168.50.90:8004
- MCP Server: http://192.168.50.90:8016
- Letta Agent MCP: http://192.168.50.90:8017

## Common Commands

```bash
# Deploy
./deploy-tuwunel.sh

# View logs
docker-compose -f docker-compose.tuwunel.yml logs -f

# Check status
docker-compose -f docker-compose.tuwunel.yml ps

# Stop services
docker-compose -f docker-compose.tuwunel.yml down

# Restart
docker-compose -f docker-compose.tuwunel.yml restart
```

## First Steps After Deploy

1. Access Element at http://matrix.oculair.ca
2. Register admin user (first registration = admin)
3. Check agent sync logs
4. Verify agent discovery working
5. Test agent responses

## Documentation

- **Full Guide**: TUWUNEL_MIGRATION.md
- **Tuwunel Docs**: https://matrix-construct.github.io/tuwunel/
- **Matrix Spec**: https://spec.matrix.org/

## Support

- Issues: https://github.com/matrix-construct/tuwunel/issues
- Community: #tuwunel:matrix.org
