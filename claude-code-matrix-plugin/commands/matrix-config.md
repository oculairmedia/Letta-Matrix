---
description: Show Matrix context injector configuration
allowed-tools: [Read]
---

# Matrix Config

Display the current Matrix context injector configuration.

## Instructions

1. Read `.claude/matrix-context.yaml` if it exists.
2. Redact any access tokens or secrets from the output.
3. Show:
   - homeserver
   - userId
   - rooms
   - filters (msgtype, senders)

If the config file is missing, explain how to copy `.matrix-context.yaml.example` into `.claude/matrix-context.yaml` and set `MATRIX_ACCESS_TOKEN`.
