# Core User Auto-Creation

## Overview
The Matrix client now automatically creates required core users on startup if they don't exist. This eliminates the need for manual user creation after deploying a fresh Tuwunel instance.

## Implementation

### Core Users
The system ensures the following users exist before syncing agents:

1. **Letta Bot** (`@letta:matrix.oculair.ca`)
   - Main bot user for Matrix operations
   - Credentials from `MATRIX_USERNAME` and `MATRIX_PASSWORD` env vars

2. **Admin User** (configurable, defaults to `@admin:matrix.oculair.ca`)
   - Administrative user for privileged operations
   - Credentials from `MATRIX_ADMIN_USERNAME` and `MATRIX_ADMIN_PASSWORD` env vars

### How It Works

1. **Startup Check**: On each startup, `run_agent_sync()` calls `ensure_core_users_exist()`
2. **User Verification**: For each core user, the system checks if it exists using the registration API
3. **Auto-Creation**: If a user doesn't exist, it's created automatically with the configured password
4. **Idempotent**: Safe to run multiple times - existing users are detected and skipped

### Code Location
- User creation logic: `src/core/user_manager.py::ensure_core_users_exist()`
- Startup integration: `src/core/agent_user_manager.py::run_agent_sync()`

### Registration API
Uses Tuwunel's standard Matrix registration endpoint:
```
POST /_matrix/client/v3/register
{
  "username": "username",
  "password": "password",
  "auth": {"type": "m.login.dummy"}
}
```

## Configuration

### Environment Variables
```bash
# Main bot user (required)
MATRIX_USERNAME=@letta:matrix.oculair.ca
MATRIX_PASSWORD=letta

# Admin user (optional, defaults to main user)
MATRIX_ADMIN_USERNAME=@admin:matrix.oculair.ca
MATRIX_ADMIN_PASSWORD=your_secure_password
```

## Benefits

1. **Zero Manual Setup**: Fresh Tuwunel instances work immediately
2. **Disaster Recovery**: Easy recovery from database loss
3. **Consistent Deployment**: Same behavior across all environments
4. **Development Friendly**: Quick setup for testing environments

## Migration from Synapse to Tuwunel

When migrating to a fresh Tuwunel instance:

1. Update docker-compose to use Tuwunel
2. Start the stack (with fresh database)
3. Core users are auto-created on first matrix-client startup
4. Agent users are synced from Letta API
5. Rooms are created automatically

No manual user creation needed!

## Troubleshooting

### Users Not Created
Check logs for:
```bash
docker-compose -f docker-compose.tuwunel.yml logs matrix-client | grep "Ensuring core"
```

### Authentication Failures
Verify environment variables:
```bash
docker-compose -f docker-compose.tuwunel.yml exec matrix-client env | grep MATRIX
```

### Permission Issues
Ensure Tuwunel has registration enabled:
```yaml
TUWUNEL_ALLOW_REGISTRATION=true
```

## Related Files
- `src/core/user_manager.py` - User management logic
- `src/core/agent_user_manager.py` - Agent sync and user creation
- `docker-compose.tuwunel.yml` - Tuwunel stack configuration
- `.env` - Environment configuration

## Changes Made (2025-11-16)

### Added
- Automatic core user creation in `run_agent_sync()`
- User existence check via registration API
- Idempotent user provisioning

### Updated
- Docker compose files to use GitHub Container Registry images
- Admin user from `@matrixadmin` to `@admin`
- Documentation for bootstrap process

### Fixed
- Fresh Tuwunel deployments requiring manual user setup
- Authentication failures after database resets
