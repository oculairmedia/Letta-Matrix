# Deployment Checklist

## Prerequisites

- [x] `real-a2a` binary installed at `/usr/local/bin/real-a2a`
- [x] Bun 1.2+ installed
- [x] Dependencies installed (`bun install` completed)
- [x] P2P connectivity tested (test-p2p.ts passed)

## Configuration

- [ ] Matrix bot user created
- [ ] Matrix access token obtained
- [ ] Matrix room ID identified
- [ ] Configuration file created (run `./setup.sh`)
- [ ] Bridge bot invited to Matrix room

## Deployment Options

### Option 1: Quick Test (Foreground)

```bash
# From /opt/stacks/matrix-synapse-deployment/real-a2a-bridge
bun run index.ts
```

**Use when:** Testing configuration, debugging issues, first-time setup

### Option 2: Production (Systemd)

```bash
# From /opt/stacks/matrix-synapse-deployment/real-a2a-bridge
./deploy.sh
```

**Use when:** Ready for production, want auto-restart, need logs in journald

## Post-Deployment Verification

### 1. Check Bridge Started
```bash
# Foreground: Look for these lines in output
[Bridge] P2P client started
[Bridge] Matrix client started
[Bridge] ✅ Bridge is running!
[Bridge] P2P Ticket: <long-ticket-string>

# Systemd: Check service status
systemctl status real-a2a-bridge
journalctl -u real-a2a-bridge -n 50
```

### 2. Verify P2P Ticket Generated
The bridge should print a ticket. Save this for sharing with agents:
```
[Bridge] P2P Ticket: ryvxsdkf4aar6wrw...
[Bridge] Share this ticket for others to join the network
```

### 3. Test Matrix → P2P
- Send message in Matrix room
- Check bridge logs for forwarding:
  ```
  [Bridge Matrix→P2P] YourName: test message
  ```

### 4. Test P2P → Matrix
```bash
# Join P2P network from another terminal
real-a2a daemon --identity test-agent --join <ticket>

# In another terminal, send message
real-a2a send --identity test-agent "Hello from P2P!"

# Check Matrix room for message: [P2P:test-agent] Hello from P2P!
```

### 5. Verify Loop Prevention
Send multiple messages and check logs for no duplicate warnings:
```
# Should NOT see:
[Bridge] Skipping duplicate P2P message: ...
[Bridge] Skipping duplicate Matrix message: ...

# Unless you're actually testing deduplication
```

## Troubleshooting

### Bridge won't start

**Check configuration:**
```bash
cat config/bridge-config.json
# Verify all fields are filled
# Verify accessToken is not expired
```

**Check permissions:**
```bash
# Ensure bridge can execute real-a2a
which real-a2a
real-a2a list
```

**Check Matrix credentials:**
```bash
# Test with curl
curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://matrix.oculair.ca/_matrix/client/v3/account/whoami
```

### No P2P ticket generated

**Check real-a2a daemon logs:**
```bash
# In foreground mode, look for:
[P2P RAW] Ticket: ...

# In systemd, check:
journalctl -u real-a2a-bridge -f | grep -i ticket
```

**Possible causes:**
- Daemon failed to start (check logs)
- Network connectivity issues (check firewall)
- Iroh relay connection failed (wait longer, it can take 5-10 seconds)

### Messages not forwarding

**Matrix → P2P:**
- Verify bridge bot is in the Matrix room
- Check bridge has permission to read messages
- Check logs for "isDuplicate" warnings

**P2P → Matrix:**
- Verify P2P agents are connected (check for "peer connected" in logs)
- Verify agents are sending to correct identity
- Check bridge daemon is receiving messages

### Service keeps restarting

```bash
# Check recent logs
journalctl -u real-a2a-bridge -n 100

# Common issues:
# - Configuration file missing/invalid
# - Matrix credentials expired
# - real-a2a binary not found
```

## Monitoring

### Health Checks

**Service running:**
```bash
systemctl is-active real-a2a-bridge
```

**Recent errors:**
```bash
journalctl -u real-a2a-bridge -p err -n 50
```

**Message statistics:**
```bash
journalctl -u real-a2a-bridge | grep "Stats:"
# Should show: [Main] Stats: N messages processed
```

### Log Monitoring

**Follow all logs:**
```bash
journalctl -u real-a2a-bridge -f
```

**Filter for errors:**
```bash
journalctl -u real-a2a-bridge -p err -f
```

**Filter for message flow:**
```bash
journalctl -u real-a2a-bridge | grep -E "\[Bridge (Matrix→P2P|P2P→Matrix)\]"
```

## Maintenance

### Restart Bridge
```bash
systemctl restart real-a2a-bridge
```

### Update Configuration
```bash
# Edit config
nano config/bridge-config.json

# Restart bridge to apply
systemctl restart real-a2a-bridge
```

### View P2P Identities
```bash
real-a2a list
# Should show: matrix-bridge [running]
```

### Clean Old Identities
```bash
# List data directory
ls ~/.local/share/real-a2a/identities/

# Remove unused identities
rm ~/.local/share/real-a2a/identities/old-identity.json
```

## Success Criteria

- [x] Bridge service starts without errors
- [ ] P2P ticket generated and displayed
- [ ] Matrix messages forward to P2P
- [ ] P2P messages appear in Matrix room
- [ ] No duplicate message warnings in logs
- [ ] Service survives restarts (if using systemd)
- [ ] Logs accessible and readable

## Next Steps After Deployment

1. **Share P2P ticket** with agents (OpenCode, Claude Code, etc.)
2. **Monitor logs** for first few messages to verify bidirectional flow
3. **Test with real agents** (not just test daemons)
4. **Document ticket** in secure location for future agent onboarding
5. **Set up monitoring/alerts** (optional, for production use)

## Emergency Procedures

### Stop Bridge Immediately
```bash
systemctl stop real-a2a-bridge
# or
pkill -f "bun run index.ts"
```

### Disable Auto-Start
```bash
systemctl disable real-a2a-bridge
```

### Rollback
```bash
# Stop service
systemctl stop real-a2a-bridge
systemctl disable real-a2a-bridge

# Remove service file
rm /etc/systemd/system/real-a2a-bridge.service
systemctl daemon-reload

# Bridge code remains at /opt/stacks/matrix-synapse-deployment/real-a2a-bridge
# Can be restarted manually if needed
```
