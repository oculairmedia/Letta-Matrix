import path from 'path';

export const autoRegisterWithBridge = async (directory: string, roomId?: string): Promise<void> => {
  const bridgeUrl = process.env.OPENCODE_BRIDGE_URL || 'http://127.0.0.1:3201';

  try {
    let port = parseInt(process.env.OPENCODE_API_PORT || '0', 10);

    if (!port) {
      try {
        const fs = await import('fs');
        const runtimeFile = path.join(directory, '.opencode', 'runtime.json');
        if (fs.existsSync(runtimeFile)) {
          const runtime = JSON.parse(fs.readFileSync(runtimeFile, 'utf-8'));
          port = runtime.port || runtime.apiPort || 0;
        }
      } catch {
      }
    }

    if (!port) {
      try {
        const { execSync } = await import('child_process');
        const result = execSync("ss -tlnp | grep opencode | grep -oP ':\\K\\d+' | head -1", { encoding: 'utf-8' }).trim();
        port = parseInt(result, 10) || 0;
      } catch {
      }
    }

    if (!port) {
      console.log('[MatrixMessaging] Could not detect OpenCode port for auto-registration');
      return;
    }

    const rooms: string[] = [];
    if (roomId) rooms.push(roomId);

    const checkResponse = await fetch(`${bridgeUrl}/registrations`);
    if (checkResponse.ok) {
      const data = (await checkResponse.json()) as { registrations: Array<{ directory: string; port: number }> };
      const existing = data.registrations.find((r) => r.directory === directory && r.port === port);
      if (existing) {
        return;
      }
    }

    const response = await fetch(`${bridgeUrl}/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        port,
        hostname: '127.0.0.1',
        sessionId: `opencode-${Date.now()}`,
        directory,
        rooms,
      }),
    });

    if (response.ok) {
      const result = (await response.json()) as { id: string };
      console.log(`[MatrixMessaging] Auto-registered with bridge: ${result.id}`);
    }
  } catch (error) {
    console.error('[MatrixMessaging] Auto-registration failed:', error);
  }
};
