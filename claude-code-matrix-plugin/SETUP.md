# Matrix Context Plugin - Local Setup

## Quick Start for This Project

The plugin is ready to use! Here's how to activate it:

### 1. Create Config

```bash
mkdir -p .claude
cp claude-code-matrix-plugin/.matrix-context.yaml.example .claude/matrix-context.yaml
```

### 2. Set Access Token

```bash
export MATRIX_ACCESS_TOKEN="your_matrix_access_token"
```

Or add to your shell profile (`~/.bashrc`, `~/.zshrc`):
```bash
echo 'export MATRIX_ACCESS_TOKEN="your_token"' >> ~/.bashrc
source ~/.bashrc
```

### 3. Register Hook

**Option A: Use `/hooks` command in Claude Code**

1. Run `/hooks` in Claude Code
2. Select `UserPromptSubmit`
3. Add hook with command:
   ```
   bun /opt/stacks/matrix-synapse-deployment/claude-code-matrix-plugin/hooks/matrix-inject.ts
   ```
4. Save to "Project settings" (stores in `.claude/settings.json`)

**Option B: Edit `.claude/settings.json` manually**

Create or edit `.claude/settings.json`:

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

### 4. Edit Config

Edit `.claude/matrix-context.yaml` with your settings:

```yaml
homeserver: https://matrix.oculair.ca
accessToken: ${MATRIX_ACCESS_TOKEN}
userId: "@your-user:matrix.oculair.ca"
rooms:
  - "!MtuqLUCwvKypRU6gll:matrix.oculair.ca"  # Project room
  - "!O8cbkBGCMB8Ujlaret:matrix.oculair.ca"  # Meridian's room
filters:
  msgtype: m.text
  senders: []
```

### 5. Test It

**Manual test:**
```bash
echo '{"user_prompt":"test","hook_event_name":"UserPromptSubmit","cwd":"'$(pwd)'"}' | \
  bun claude-code-matrix-plugin/hooks/matrix-inject.ts
```

**In Claude Code:**
1. Have someone send a message to one of your configured rooms
2. Submit any prompt in Claude Code
3. You should see: `📨 New Matrix messages: ...`

## Verification

**Check hook is registered:**
```bash
cat .claude/settings.json | jq .hooks.UserPromptSubmit
```

**Check config exists:**
```bash
cat .claude/matrix-context.yaml
```

**Check sync state (after first run):**
```bash
cat .claude/matrix-context-state.json
```

## Troubleshooting

**"Failed to load Matrix config"**
- Verify `.claude/matrix-context.yaml` exists
- Check `MATRIX_ACCESS_TOKEN` is set: `echo $MATRIX_ACCESS_TOKEN`

**No messages appearing**
- Check sync state file exists (created after first successful sync)
- Verify room IDs are correct in config
- Test Matrix API manually:
  ```bash
  curl -H "Authorization: Bearer $MATRIX_ACCESS_TOKEN" \
       "https://matrix.oculair.ca/_matrix/client/v3/sync?timeout=0"
  ```

**Hook not running**
- Verify absolute path in `.claude/settings.json`
- Check hook is executable: `ls -la claude-code-matrix-plugin/hooks/matrix-inject.ts`
- Test hook manually (see above)

## Files Created

- `.claude/settings.json` - Hook registration
- `.claude/matrix-context.yaml` - Your config (gitignored)
- `.claude/matrix-context-state.json` - Sync state (gitignored)

## See Also

- [Plugin README](claude-code-matrix-plugin/README.md) - Full documentation
- [GitHub Issues #9-14](https://github.com/oculairmedia/Letta-Matrix/issues?q=is%3Aissue+label%3Aenhancement) - Implementation tracking
