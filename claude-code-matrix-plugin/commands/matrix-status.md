---
description: Show Matrix context injector status
allowed-tools: [Read]
---

# Matrix Status

Report current Matrix integration status based on local configuration and sync state.

## Instructions

1. Read `.claude/matrix-context.yaml` if it exists.
2. Read `.claude/matrix-context-state.json` if it exists.
3. Summarize:
   - Homeserver
   - User ID
   - Room count
   - Last sync token (if any)
   - Whether config is valid

If the config file is missing, explain how to create it and point to `.matrix-context.yaml.example` in this plugin.
