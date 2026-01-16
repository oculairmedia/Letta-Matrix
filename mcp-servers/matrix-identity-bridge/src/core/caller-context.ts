import fs from 'fs/promises';
import type { MatrixIdentity } from '../types/index.js';
import type { ToolContext } from './tool-context.js';

export type CallerContext = {
  directory?: string;
  name?: string;
  source?: 'input' | 'opencode' | 'claude-code' | 'pwd' | 'unknown';
  sourceOverride?: 'opencode' | 'claude-code';
};

export type CallerContextInput = {
  caller_directory?: string;
  caller_name?: string;
  caller_source?: 'opencode' | 'claude-code';
};

export const getCallerContext = async (input: CallerContextInput): Promise<CallerContext> => {
  if (input.caller_directory) {
    return {
      directory: input.caller_directory,
      name: input.caller_name,
      source: 'input',
      sourceOverride: input.caller_source,
    };
  }

  const envDir = process.env.OPENCODE_PROJECT_DIR;
  if (envDir) {
    return {
      directory: envDir,
      name: input.caller_name,
      source: 'opencode',
      sourceOverride: input.caller_source,
    };
  }

  const telemetry = await readClaudeCodeTelemetry();
  if (telemetry?.cwd) {
    return {
      directory: telemetry.cwd,
      name: input.caller_name || telemetry.display_name,
      source: 'claude-code',
      sourceOverride: input.caller_source,
    };
  }

  const pwd = process.env.PWD;
  if (pwd) {
    return {
      directory: pwd,
      name: input.caller_name,
      source: 'pwd',
      sourceOverride: input.caller_source,
    };
  }

  return { name: input.caller_name, source: 'unknown', sourceOverride: input.caller_source };
};

const readClaudeCodeTelemetry = async (): Promise<{ cwd?: string; display_name?: string; updated_at?: number } | null> => {
  const candidates = [
    process.env.MATRIX_MCP_TELEMETRY_PATH,
    '/app/data/claude-code-telemetry.json',
    '/opt/stacks/matrix-synapse-deployment/mcp-servers/matrix-identity-bridge/data/claude-code-telemetry.json',
  ].filter((value): value is string => Boolean(value));

  for (const telemetryPath of candidates) {
    try {
      const raw = await fs.readFile(telemetryPath, 'utf-8');
      const data = JSON.parse(raw) as { cwd?: string; display_name?: string; updated_at?: number };
      if (!data || typeof data !== 'object') {
        return null;
      }
      return data;
    } catch (error: any) {
      if (error?.code === 'ENOENT') {
        continue;
      }
      console.error('[MatrixMessaging] Failed to read Claude Code telemetry:', error);
      return null;
    }
  }

  return null;
};

const getOrCreateClaudeCodeIdentity = async (
  ctx: ToolContext,
  directory: string,
  callerName?: string
): Promise<MatrixIdentity> => {
  const encoded = Buffer.from(directory)
    .toString('base64')
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  const identityId = `claude_code_${encoded}`;
  const projectName = directory.split('/').filter(Boolean).pop() || 'project';
  const localpart = `cc_${projectName.toLowerCase().replace(/[^a-z0-9_]/g, '_')}`;

  return await ctx.identityManager.getOrCreateIdentity({
    id: identityId,
    localpart,
    displayName: callerName || `Claude Code: ${projectName}`,
    type: 'custom',
  });
};

export const resolveCallerIdentity = async (
  ctx: ToolContext,
  callerDirectory: string | undefined,
  callerName: string | undefined,
  effectiveSource: CallerContext['source'],
  identityId?: string
): Promise<MatrixIdentity> => {
  if (identityId) {
    const identity = await ctx.storage.getIdentityAsync(identityId);
    if (!identity) {
      throw new Error(`Identity not found: ${identityId}`);
    }
    return identity;
  }

  if (!callerDirectory) {
    throw new Error('No caller directory available to derive identity');
  }

  if (effectiveSource === 'claude-code') {
    return await getOrCreateClaudeCodeIdentity(ctx, callerDirectory, callerName);
  }

  return await ctx.openCodeService.getOrCreateIdentity(callerDirectory, callerName);
};

export const resolveCallerIdentityId = async (
  ctx: ToolContext,
  callerDirectory: string | undefined,
  callerName: string | undefined,
  effectiveSource: CallerContext['source'],
  identityId?: string
): Promise<string> => {
  const identity = await resolveCallerIdentity(
    ctx,
    callerDirectory,
    callerName,
    effectiveSource,
    identityId
  );
  return identity.id;
};
