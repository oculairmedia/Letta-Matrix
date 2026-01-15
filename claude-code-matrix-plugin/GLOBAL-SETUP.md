# Global Matrix Context Hook Setup

## Overview

The Matrix Context Plugin now supports **global configuration** that works across all Claude Code projects without per-project setup.

## How It Works

1. **Config Fallback**: Hook checks for local `.claude/matrix-context.yaml` first, then falls back to `~/.claude/matrix-context.yaml`
2. **Global State**: When using global config, state is stored in `~/.claude/matrix-context-state.json` (not per-project)
3. **Environment Variable**: Access token resolved from `MATRIX_ACCESS_TOKEN` environment variable

## Setup Instructions

### 1. Ensure Global Hook is Registered

Check `~/.claude/settings.json` contains:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bun /opt/stacks/matrix-synapse-deployment/claude-code-matrix-plugin/hooks/matrix-inject.ts",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

### 2. Create Global Config

Create `~/.claude/matrix-context.yaml`:

```yaml
homeserver: https://matrix.oculair.ca
accessToken: ${MATRIX_ACCESS_TOKEN}
userId: "@opencode:matrix.oculair.ca"
rooms:
  - "!MtuqLUCwvKypRU6gll:matrix.oculair.ca"  # Project room
filters:
  msgtype: m.text
  senders: []  # Empty = all senders
```

### 3. Set Environment Variable

Add to `~/.bashrc` or `~/.profile`:

```bash
export MATRIX_ACCESS_TOKEN="your_token_here"
```

Then reload:
```bash
source ~/.bashrc
```

### 4. Verify Setup

Test the hook manually:
```bash
cd /tmp  # Use any directory
echo '{"user_prompt":"test","cwd":"/tmp"}' | \
  bun /opt/stacks/matrix-synapse-deployment/claude-code-matrix-plugin/hooks/matrix-inject.ts
```

You should see Matrix messages in the output if there are any new messages.

Check state file was created:
```bash
cat ~/.claude/matrix-context-state.json
```

Should show:
```json
{"nextBatch":"12345678"}
```

## How It Works

### Config Resolution

1. Hook receives working directory from Claude Code
2. Checks for `{cwd}/.claude/matrix-context.yaml`
3. If not found, uses `~/.claude/matrix-context.yaml`
4. Sets `isGlobal: true` flag in config

### State Storage

- **Local config**: State stored in `{cwd}/.claude/matrix-context-state.json`
- **Global config**: State stored in `~/.claude/matrix-context-state.json`

This ensures:
- Different projects can have different Matrix rooms (if using local config)
- Global config works seamlessly across all projects
- Sync state is preserved between Claude Code sessions

## Testing

### Test Global Config Works

```bash
cd /tmp
echo '{"user_prompt":"test","cwd":"/tmp"}' | \
  MATRIX_ACCESS_TOKEN="your_token" \
  bun /opt/stacks/matrix-synapse-deployment/claude-code-matrix-plugin/hooks/matrix-inject.ts
```

### Test Local Override

Create project-specific config:
```bash
cd /path/to/project
mkdir -p .claude
cp ~/.claude/matrix-context.yaml .claude/

# Edit to add project-specific rooms
vim .claude/matrix-context.yaml
```

The hook will use local config for that project, global for all others.

## Troubleshooting

### Hook Not Running

1. Verify hook is registered: `cat ~/.claude/settings.json`
2. Check Bun is installed: `which bun`
3. Verify path is correct: `ls -la /opt/stacks/matrix-synapse-deployment/claude-code-matrix-plugin/hooks/matrix-inject.ts`

### No Messages Appearing

1. Check global config exists: `cat ~/.claude/matrix-context.yaml`
2. Verify token is set: `echo $MATRIX_ACCESS_TOKEN`
3. Check state file: `cat ~/.claude/matrix-context-state.json`
4. Test Matrix API directly:
   ```bash
   curl -H "Authorization: Bearer $MATRIX_ACCESS_TOKEN" \
        "https://matrix.oculair.ca/_matrix/client/v3/sync?timeout=0"
   ```

### Wrong Rooms

1. Check room IDs in config: `cat ~/.claude/matrix-context.yaml`
2. Verify you're in those rooms via Element or other Matrix client
3. Send a test message to the room and wait for next Claude prompt

## Architecture

```
Claude Code Session
└─> UserPromptSubmit Hook
    └─> matrix-inject.ts
        ├─> Load config (local first, then global)
        ├─> Create MatrixClient with appropriate state path
        ├─> Poll Matrix /sync for new messages
        ├─> Filter by rooms and msgtype
        ├─> Format as system message
        └─> Output to Claude Code
```

## File Locations

| File | Purpose | Location |
|------|---------|----------|
| Config | Matrix settings | `~/.claude/matrix-context.yaml` (global) or `{project}/.claude/matrix-context.yaml` (local) |
| State | Sync token | `~/.claude/matrix-context-state.json` (global) or `{project}/.claude/matrix-context-state.json` (local) |
| Hook | Main script | `/opt/stacks/matrix-synapse-deployment/claude-code-matrix-plugin/hooks/matrix-inject.ts` |
| Settings | Hook registration | `~/.claude/settings.json` |

## Benefits of Global Setup

1. **No per-project setup**: Works immediately in any directory
2. **Consistent experience**: Same Matrix rooms across all projects
3. **Single source of truth**: One config file to maintain
4. **Persistent state**: Sync token preserved globally

## When to Use Local Config

Use project-specific config (`.claude/matrix-context.yaml` in project root) when:
- Different projects need different Matrix rooms
- Project team has dedicated Matrix channels
- Testing new room configurations without affecting global setup
