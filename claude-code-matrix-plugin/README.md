# Matrix Context Plugin for Claude Code

Inject Matrix messages as context into Claude Code conversations via a `UserPromptSubmit` hook.

## How It Works

This plugin registers a hook that runs **before Claude processes your prompt**. It:

1. Polls your Matrix homeserver for new messages since last check
2. Filters messages from configured rooms
3. Injects them as system context visible to Claude

**Example output when Meridian sends "Hello!":**

```
📨 New Matrix messages:
- @meridian:matrix.oculair.ca: Hello!
```

## Prerequisites

- **Bun**: Runtime for executing the TypeScript hook
- **Matrix Access Token**: From your Matrix homeserver
- **Node.js 18+**: For dependencies

## Installation

### Option 1: Local Development

1. **Install dependencies:**
   ```bash
   cd claude-code-matrix-plugin
   bun install
   ```

2. **Create config:**
   ```bash
   mkdir -p .claude
   cp .matrix-context.yaml.example .claude/matrix-context.yaml
   ```

3. **Set access token:**
   ```bash
   export MATRIX_ACCESS_TOKEN="your_token_here"
   ```

4. **Register hook in `.claude/settings.json`:**
   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         {
           "hooks": [
             {
               "type": "command",
               "command": "bun /full/path/to/claude-code-matrix-plugin/hooks/matrix-inject.ts",
               "timeout": 5
             }
           ]
         }
       ]
     }
   }
   ```

   **Important**: Use the **absolute path** to `matrix-inject.ts` since `$CLAUDE_PLUGIN_ROOT` only works when installed via plugin marketplace.

### Option 2: Plugin Marketplace (Future)

Once published:
```bash
/plugin marketplace add oculairmedia/claude-plugins
/plugin install matrix-context@oculairmedia
```

## Configuration

Edit `.claude/matrix-context.yaml`:

```yaml
homeserver: https://matrix.oculair.ca
accessToken: ${MATRIX_ACCESS_TOKEN}  # Uses environment variable
userId: "@your-user:matrix.oculair.ca"
rooms:
  - "!roomId1:matrix.oculair.ca"
  - "!roomId2:matrix.oculair.ca"
filters:
  msgtype: m.text
  senders: []  # Empty = all senders, or ["@user:domain"] for specific users
```

## Commands

- **`/matrix-status`**: Show connection status and last sync
- **`/matrix-config`**: Display current configuration (tokens redacted)

## How Hooks Work

The `UserPromptSubmit` hook runs **every time you submit a prompt**:

1. Claude Code passes hook input via **stdin** as JSON
2. Hook script (`matrix-inject.ts`) reads stdin, polls Matrix
3. If new messages exist, outputs JSON with `systemMessage` field
4. Claude sees the system message before processing your prompt
5. Hook **always exits 0** (never blocks Claude)

**Hook timeout**: 5 seconds (configured in `hooks.json`)

## Troubleshooting

### Hook not running

1. **Check hook registration:**
   ```bash
   cat .claude/settings.json
   ```

2. **Verify path is absolute:**
   ```bash
   which bun  # Should show /usr/local/bin/bun or similar
   ls /full/path/to/claude-code-matrix-plugin/hooks/matrix-inject.ts
   ```

3. **Test hook manually:**
   ```bash
   echo '{"user_prompt":"test","cwd":"'$(pwd)'"}' | bun hooks/matrix-inject.ts
   ```

### No messages appearing

1. **Check config exists:**
   ```bash
   cat .claude/matrix-context.yaml
   ```

2. **Verify token is set:**
   ```bash
   echo $MATRIX_ACCESS_TOKEN
   ```

3. **Check sync state:**
   ```bash
   cat .claude/matrix-context-state.json
   ```

4. **Test Matrix API directly:**
   ```bash
   curl -H "Authorization: Bearer $MATRIX_ACCESS_TOKEN" \
        "https://matrix.oculair.ca/_matrix/client/v3/sync?timeout=0"
   ```

### Hook times out

- Default timeout is 5 seconds
- Reduce `timeoutMs` in `matrix-inject.ts` (currently 4000ms)
- Check Matrix homeserver is reachable

## Development

### File Structure

```
claude-code-matrix-plugin/
├── .claude-plugin/
│   └── plugin.json          # Plugin metadata
├── hooks/
│   ├── hooks.json           # Hook registration
│   └── matrix-inject.ts     # Main hook (Bun executable)
├── lib/
│   ├── config.ts            # Config loader
│   ├── matrix-client.ts     # Matrix sync client
│   └── types.ts             # TypeScript types
├── commands/
│   ├── matrix-status.md     # /matrix-status command
│   └── matrix-config.md     # /matrix-config command
└── .matrix-context.yaml.example
```

### Testing

**Simulate hook input:**
```bash
echo '{
  "user_prompt": "hello",
  "hook_event_name": "UserPromptSubmit",
  "cwd": "'$(pwd)'"
}' | bun hooks/matrix-inject.ts
```

**Expected output (if messages exist):**
```json
{"systemMessage":"📨 New Matrix messages:\n- @user:domain: Hello!"}
```

**Expected output (no messages):**
```
(empty stdout, exit 0)
```

## Security Notes

- **Access token in environment**: Never commit `.claude/matrix-context.yaml` with hardcoded tokens
- **Hook runs with your credentials**: Review hook code before registering
- **Timeout prevents hanging**: Hook aborts after 5 seconds
- **Silent fallback**: Errors don't block Claude (logged to stderr)

## Related Issues

- [#9](../../issues/9): Structure
- [#10](../../issues/10): Client
- [#11](../../issues/11): Config
- [#12](../../issues/12): Hook
- [#13](../../issues/13): Commands
- [#14](../../issues/14): Testing

## License

MIT
