#!/usr/bin/env bun
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { loadConfig } from "../lib/config";
import { MatrixClient, getStatePath } from "../lib/matrix-client";
import { ensureClaudeCodeIdentity, ensureJoinedRooms } from "../lib/identity-bridge";
import type { HookInput, HookOutput, MatrixMessage } from "../lib/types";

const TELEMETRY_PATH =
  process.env.MATRIX_MCP_TELEMETRY_PATH ??
  "/opt/stacks/matrix-synapse-deployment/mcp-servers/matrix-identity-bridge/data/claude-code-telemetry.json";

function formatMessages(messages: MatrixMessage[]): string {
  const lines = messages.map((message) => {
    return `- ${message.sender}: ${message.body}`;
  });

  return `📨 New Matrix messages:\n${lines.join("\n")}`;
}

function deriveDisplayName(directory: string): string {
  const parts = directory.split("/").filter((part) => part.length > 0);
  const projectName = parts[parts.length - 1] || "Unknown";
  const formatted = projectName
    .split(/[-_]/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
  return `Claude Code: ${formatted}`;
}

async function writeTelemetry(input: HookInput, cwd: string): Promise<void> {
  const payload = {
    cwd,
    session_id: input.session_id,
    display_name: deriveDisplayName(cwd),
    updated_at: Date.now(),
    source: "claude-code",
  };

  await mkdir(path.dirname(TELEMETRY_PATH), { recursive: true });
  await writeFile(TELEMETRY_PATH, JSON.stringify(payload), "utf-8");
}

async function readHookInput(): Promise<HookInput | null> {
  const chunks: Buffer[] = [];

  for await (const chunk of process.stdin) {
    chunks.push(chunk as Buffer);
  }

  const inputText = Buffer.concat(chunks).toString("utf-8").trim();
  if (!inputText) {
    return null;
  }

  try {
    return JSON.parse(inputText) as HookInput;
  } catch (error) {
    console.error("Failed to parse hook input", error);
    return null;
  }
}

async function main(): Promise<void> {
  try {
    const input = await readHookInput();
    if (!input) {
      return;
    }

    const cwd = input.cwd ?? process.cwd();
    try {
      await writeTelemetry(input, cwd);
    } catch (error) {
      console.error("Failed to write Claude Code telemetry", error);
    }

    const config = await loadConfig(cwd);
    if (!config) {
      return;
    }

    const derivedIdentity = await ensureClaudeCodeIdentity(config, cwd);
    if (derivedIdentity) {
      config.accessToken = derivedIdentity.access_token;
      config.userId = derivedIdentity.mxid;

      try {
        await ensureJoinedRooms(derivedIdentity.identity_id, config.rooms, config);
      } catch (error) {
        console.error("Failed to join rooms via identity bridge", error);
      }
    }

    const client = new MatrixClient(config, {
      statePath: getStatePath(cwd, config.isGlobal),
      timeoutMs: 10000,
    });

    const messages = await client.getNewMessages();
    if (messages.length === 0) {
      return;
    }

    const output: HookOutput = {
      systemMessage: formatMessages(messages),
    };

    console.log(JSON.stringify(output));
  } catch (error) {
    console.error("Matrix hook error", error);
  }
}

await main();
process.exit(0);
