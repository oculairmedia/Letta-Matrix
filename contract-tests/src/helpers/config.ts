export interface ContractTestConfig {
  matrixHomeserverUrl: string;
  bridgeApiUrl: string;
  lettaApiUrl: string;
  matrixAdminUserId: string;
  matrixAdminPassword: string;
  lettaToken: string;
  webhookSecret: string;
  pollingIntervalMs: number;
  maxWaitMs: number;
}

export function loadConfig(): ContractTestConfig {
  return {
    matrixHomeserverUrl: process.env.MATRIX_HOMESERVER_URL ?? 'http://127.0.0.1:6167',
    bridgeApiUrl: process.env.BRIDGE_API_URL ?? 'http://127.0.0.1:8004',
    lettaApiUrl: process.env.LETTA_API_URL ?? 'http://192.168.50.90:8289',
    matrixAdminUserId: process.env.MATRIX_ADMIN_USER_ID ?? '@admin:matrix.oculair.ca',
    matrixAdminPassword: process.env.MATRIX_ADMIN_PASSWORD ?? '',
    lettaToken: process.env.LETTA_TOKEN ?? 'lettaSecurePass123',
    webhookSecret:
      process.env.LETTA_WEBHOOK_SECRET ??
      '4df9db9855796bdf65f5ee65df492264777cbbae392c97ca32f12ba842990c8d',
    pollingIntervalMs: Number(process.env.CONTRACT_POLL_INTERVAL_MS ?? 2_000),
    maxWaitMs: Number(process.env.CONTRACT_MAX_WAIT_MS ?? 120_000),
  };
}

export function requireMatrixAdminPassword(config: ContractTestConfig): string {
  if (!config.matrixAdminPassword) {
    throw new Error('MATRIX_ADMIN_PASSWORD must be set for tests that call Matrix C-S API');
  }
  return config.matrixAdminPassword;
}
