/**
 * Test cases for OpenCode bridge discovery and port handling
 *
 * Tests the bridge's ability to:
 * 1. Discover OpenCode instances on random ports
 * 2. Handle port changes when OpenCode restarts
 * 3. Retry forwarding after discovery refresh
 * 4. Clean up stale registrations
 *
 * Run with: npx tsx src/test-discovery.ts
 */

import { createServer, IncomingMessage, ServerResponse } from "http";

// Test configuration
const BRIDGE_URL = process.env.BRIDGE_URL || "http://127.0.0.1:3201";
const DISCOVERY_URL = process.env.DISCOVERY_URL || "http://127.0.0.1:3202";

// Colors for output
const GREEN = "\x1b[32m";
const RED = "\x1b[31m";
const YELLOW = "\x1b[33m";
const RESET = "\x1b[0m";

interface TestResult {
  name: string;
  passed: boolean;
  message: string;
  duration: number;
}

const results: TestResult[] = [];

// Helper to make HTTP requests
async function request(
  url: string,
  options: RequestInit = {},
): Promise<{ status: number; data: any }> {
  const response = await fetch(url, options);
  let data;
  try {
    data = await response.json();
  } catch {
    data = await response.text();
  }
  return { status: response.status, data };
}

// Test runner
async function runTest(
  name: string,
  testFn: () => Promise<void>,
): Promise<void> {
  const start = Date.now();
  try {
    await testFn();
    const duration = Date.now() - start;
    results.push({ name, passed: true, message: "OK", duration });
    console.log(`${GREEN}✓${RESET} ${name} (${duration}ms)`);
  } catch (error) {
    const duration = Date.now() - start;
    const message = error instanceof Error ? error.message : String(error);
    results.push({ name, passed: false, message, duration });
    console.log(`${RED}✗${RESET} ${name} (${duration}ms)`);
    console.log(`  ${RED}Error: ${message}${RESET}`);
  }
}

// Assertion helpers
function assert(condition: boolean, message: string): void {
  if (!condition) {
    throw new Error(message);
  }
}

function assertEqual<T>(actual: T, expected: T, message: string): void {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${expected}, got ${actual}`);
  }
}

function assertIncludes(str: string, substr: string, message: string): void {
  if (!str.includes(substr)) {
    throw new Error(`${message}: "${str}" does not include "${substr}"`);
  }
}

// ============================================================================
// TEST CASES
// ============================================================================

async function testBridgeHealth(): Promise<void> {
  const { status, data } = await request(`${BRIDGE_URL}/health`);
  assertEqual(status, 200, "Health check status");
  assertEqual(data.status, "ok", "Health status value");
  assert(
    typeof data.registrations === "number",
    "Should have registrations count",
  );
  assert(
    typeof data.matrixConnected === "boolean",
    "Should have matrixConnected flag",
  );
}

async function testDiscoveryServiceHealth(): Promise<void> {
  const { status, data } = await request(`${DISCOVERY_URL}/discover`);
  assertEqual(status, 200, "Discovery service status");
  assert(Array.isArray(data), "Should return array of instances");
}

async function testBridgeDiscoveryEndpoint(): Promise<void> {
  const { status, data } = await request(`${BRIDGE_URL}/discover`);
  assertEqual(status, 200, "Bridge discovery status");
  assertEqual(data.success, true, "Discovery success flag");
  assert(typeof data.count === "number", "Should have count");
  assert(Array.isArray(data.registrations), "Should have registrations array");
  assert(
    typeof data.identityMappings === "object",
    "Should have identity mappings",
  );
}

async function testRegistrationAfterDiscovery(): Promise<void> {
  // First trigger discovery
  await request(`${BRIDGE_URL}/discover`);

  // Then check registrations
  const { status, data } = await request(`${BRIDGE_URL}/registrations`);
  assertEqual(status, 200, "Registrations status");
  assert(typeof data.count === "number", "Should have count");
  assert(Array.isArray(data.registrations), "Should have registrations array");

  // If there are running OpenCode instances, verify registration structure
  if (data.registrations.length > 0) {
    const reg = data.registrations[0];
    assert(typeof reg.id === "string", "Registration should have id");
    assert(typeof reg.port === "number", "Registration should have port");
    assert(
      typeof reg.hostname === "string",
      "Registration should have hostname",
    );
    assert(
      typeof reg.directory === "string",
      "Registration should have directory",
    );
    assert(
      typeof reg.registeredAt === "number",
      "Registration should have registeredAt",
    );
    assert(
      typeof reg.lastSeen === "number",
      "Registration should have lastSeen",
    );
  }
}

async function testIdentityMapping(): Promise<void> {
  // Trigger discovery to populate mappings
  const { data } = await request(`${BRIDGE_URL}/discover`);

  if (data.registrations.length > 0) {
    const reg = data.registrations[0];
    const directory = reg.directory;

    // Verify identity mapping format
    // Directory like /opt/stacks/letta-MCP-server should map to @oc_letta_mcp_server_v2:matrix.oculair.ca
    // Directory like /opt/stacks/letta-MCP-server maps to @oc_letta_mcp_server_v2:matrix.oculair.ca
    // The localpart format is: oc_<directory_name>_v2

    // Check that at least one identity mapping exists for this registration
    const mappedIdentities = Object.keys(data.identityMappings).filter(
      (identity: string) =>
        data.identityMappings[identity].directory === directory,
    );

    assert(
      mappedIdentities.length > 0,
      `Should have identity mapping for ${directory}`,
    );

    // Verify v2 format exists
    const hasV2 = mappedIdentities.some((id: string) => id.includes("_v2:"));
    assert(hasV2, "Should have v2 format identity mapping");
  }
}

async function testManualRegistration(): Promise<void> {
  const testPort = 59999;
  const testDirectory = "/tmp/test-opencode-instance";

  // Register a fake instance
  const { status, data } = await request(`${BRIDGE_URL}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      port: testPort,
      hostname: "127.0.0.1",
      sessionId: "test-session-123",
      directory: testDirectory,
      rooms: ["!test:matrix.example.com"],
    }),
  });

  assertEqual(status, 200, "Registration status");
  assertEqual(data.success, true, "Registration success");
  assert(data.id.includes(String(testPort)), "ID should include port");
  assert(
    Array.isArray(data.matrixIdentities),
    "Should return matrix identities",
  );

  // Verify it appears in registrations
  const { data: regsData } = await request(`${BRIDGE_URL}/registrations`);
  const found = regsData.registrations.find(
    (r: any) => r.directory === testDirectory,
  );
  assert(found !== undefined, "Should find registered instance");
  assertEqual(found.port, testPort, "Port should match");

  // Clean up - unregister
  await request(`${BRIDGE_URL}/unregister`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: data.id }),
  });
}

async function testHeartbeat(): Promise<void> {
  const testPort = 59998;
  const testDirectory = "/tmp/test-heartbeat-instance";

  // Register a fake instance
  const { data: regData } = await request(`${BRIDGE_URL}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      port: testPort,
      hostname: "127.0.0.1",
      sessionId: "test-heartbeat-123",
      directory: testDirectory,
    }),
  });

  const registrationId = regData.id;

  // Wait a bit
  await new Promise((resolve) => setTimeout(resolve, 100));

  // Send heartbeat
  const { status, data } = await request(`${BRIDGE_URL}/heartbeat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: registrationId }),
  });

  assertEqual(status, 200, "Heartbeat status");
  assertEqual(data.success, true, "Heartbeat success");
  assert(
    data.lastSeen > regData.registration.lastSeen,
    "lastSeen should be updated",
  );

  // Clean up
  await request(`${BRIDGE_URL}/unregister`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: registrationId }),
  });
}

async function testHeartbeatByDirectory(): Promise<void> {
  const testPort = 59997;
  const testDirectory = "/tmp/test-heartbeat-dir-instance";

  // Register a fake instance
  const { data: regData } = await request(`${BRIDGE_URL}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      port: testPort,
      hostname: "127.0.0.1",
      sessionId: "test-heartbeat-dir-123",
      directory: testDirectory,
    }),
  });

  // Send heartbeat by directory (not by id)
  const { status, data } = await request(`${BRIDGE_URL}/heartbeat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ directory: testDirectory }),
  });

  assertEqual(status, 200, "Heartbeat by directory status");
  assertEqual(data.success, true, "Heartbeat success");

  // Clean up
  await request(`${BRIDGE_URL}/unregister`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: regData.id }),
  });
}

async function testUnregister(): Promise<void> {
  const testPort = 59996;
  const testDirectory = "/tmp/test-unregister-instance";

  // Register
  const { data: regData } = await request(`${BRIDGE_URL}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      port: testPort,
      hostname: "127.0.0.1",
      sessionId: "test-unregister-123",
      directory: testDirectory,
    }),
  });

  // Verify it exists
  let { data: regsData } = await request(`${BRIDGE_URL}/registrations`);
  let found = regsData.registrations.find(
    (r: any) => r.directory === testDirectory,
  );
  assert(found !== undefined, "Should find registered instance");

  // Unregister
  const { status, data } = await request(`${BRIDGE_URL}/unregister`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: regData.id }),
  });

  assertEqual(status, 200, "Unregister status");
  assertEqual(data.success, true, "Unregister success");

  // Verify it's gone
  ({ data: regsData } = await request(`${BRIDGE_URL}/registrations`));
  found = regsData.registrations.find(
    (r: any) => r.directory === testDirectory,
  );
  assert(found === undefined, "Should not find unregistered instance");
}

async function testPortChangeDetection(): Promise<void> {
  // This test simulates what happens when OpenCode restarts on a different port
  const testDirectory = "/tmp/test-port-change-instance";
  const oldPort = 59995;
  const newPort = 59994;

  // Register with old port
  const { data: regData1 } = await request(`${BRIDGE_URL}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      port: oldPort,
      hostname: "127.0.0.1",
      sessionId: "test-port-old",
      directory: testDirectory,
    }),
  });

  // Verify old port registration
  let { data: regsData } = await request(`${BRIDGE_URL}/registrations`);
  let found = regsData.registrations.find(
    (r: any) => r.directory === testDirectory,
  );
  assertEqual(found.port, oldPort, "Should have old port");

  // Unregister old (simulating OpenCode shutdown)
  await request(`${BRIDGE_URL}/unregister`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: regData1.id }),
  });

  // Register with new port (simulating OpenCode restart)
  const { data: regData2 } = await request(`${BRIDGE_URL}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      port: newPort,
      hostname: "127.0.0.1",
      sessionId: "test-port-new",
      directory: testDirectory,
    }),
  });

  // Verify new port registration
  ({ data: regsData } = await request(`${BRIDGE_URL}/registrations`));
  found = regsData.registrations.find(
    (r: any) => r.directory === testDirectory,
  );
  assertEqual(found.port, newPort, "Should have new port");

  // Clean up
  await request(`${BRIDGE_URL}/unregister`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: regData2.id }),
  });
}

async function testDiscoveryUpdatesExistingRegistration(): Promise<void> {
  // Skip if no real OpenCode instances are running
  const { data: discovery } = await request(`${DISCOVERY_URL}/discover`);
  if (discovery.length === 0) {
    console.log(`  ${YELLOW}⚠ Skipped: No OpenCode instances running${RESET}`);
    return;
  }

  // Trigger discovery
  await request(`${BRIDGE_URL}/discover`);

  // Get registrations after discovery
  const { data: after } = await request(`${BRIDGE_URL}/registrations`);

  // Should have registrations for running instances
  assert(after.count > 0, "Should have registrations after discovery");

  // Verify discovered instances match running instances
  for (const instance of discovery) {
    const found = after.registrations.find(
      (r: any) =>
        r.directory === instance.directory && r.port === instance.port,
    );
    assert(
      found !== undefined,
      `Should have registration for ${instance.directory} on port ${instance.port}`,
    );
  }
}

async function testNotifyEndpoint(): Promise<void> {
  // Skip if no real OpenCode instances are running
  const { data: discovery } = await request(`${DISCOVERY_URL}/discover`);
  if (discovery.length === 0) {
    console.log(`  ${YELLOW}⚠ Skipped: No OpenCode instances running${RESET}`);
    return;
  }

  // First trigger discovery to ensure registrations exist
  await request(`${BRIDGE_URL}/discover`);

  const instance = discovery[0];

  // Try to notify (this may fail if OpenCode doesn't have an active session, but endpoint should respond)
  const { status, data } = await request(`${BRIDGE_URL}/notify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      directory: instance.directory,
      message: "Test notification from bridge tests",
      sender: "test-runner",
      agentName: "Test Agent",
    }),
  });

  // Should get a response (success or failure, but not 404)
  assert(status !== 404, "Notify endpoint should exist");
  assert(
    typeof data.success === "boolean" || typeof data.error === "string",
    "Should return success or error",
  );
}

async function testNotifyMissingDirectory(): Promise<void> {
  const { status, data } = await request(`${BRIDGE_URL}/notify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      directory: "/nonexistent/directory/that/does/not/exist",
      message: "Test notification",
    }),
  });

  assertEqual(status, 404, "Should return 404 for missing directory");
  assertIncludes(
    data.error,
    "No OpenCode instance registered",
    "Should have appropriate error message",
  );
}

async function testRoomsEndpoint(): Promise<void> {
  const { status, data } = await request(`${BRIDGE_URL}/rooms`);
  assertEqual(status, 200, "Rooms endpoint status");
  assert(typeof data.count === "number", "Should have count");
  assert(Array.isArray(data.rooms), "Should have rooms array");
}

// ============================================================================
// MOCK SERVER TESTS
// ============================================================================

/**
 * Creates a mock OpenCode server that responds to session.list and session.prompt
 * Used to test the bridge's retry logic with port changes
 */
async function createMockOpenCodeServer(
  port: number,
  directory: string,
): Promise<{
  server: ReturnType<typeof createServer>;
  close: () => Promise<void>;
}> {
  const sessionId = `mock-session-${Date.now()}`;

  const server = createServer((req: IncomingMessage, res: ServerResponse) => {
    const url = new URL(req.url || "/", `http://localhost:${port}`);

    // Handle session list
    if (url.pathname === "/session" && req.method === "GET") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify([
          {
            id: sessionId,
            directory,
            time: { created: Date.now(), updated: Date.now() },
          },
        ]),
      );
      return;
    }

    // Handle session prompt
    if (
      url.pathname === `/session/${sessionId}/prompt` &&
      req.method === "POST"
    ) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: true }));
      return;
    }

    res.writeHead(404);
    res.end("Not found");
  });

  await new Promise<void>((resolve) => server.listen(port, resolve));

  return {
    server,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.close((err) => (err ? reject(err) : resolve()));
      }),
  };
}

async function testMockServerForwarding(): Promise<void> {
  const port = 59990;
  const directory = "/tmp/mock-opencode-test";

  // Start mock server
  const { close } = await createMockOpenCodeServer(port, directory);

  try {
    // Register the mock instance
    const { data: regData } = await request(`${BRIDGE_URL}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        port,
        hostname: "127.0.0.1",
        sessionId: "mock-session",
        directory,
      }),
    });

    // Try to notify
    const { status, data } = await request(`${BRIDGE_URL}/notify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        directory,
        message: "Test message to mock server",
        sender: "test",
        agentName: "Test Agent",
      }),
    });

    assertEqual(status, 200, "Notify should succeed");
    assertEqual(data.success, true, "Should forward successfully");

    // Clean up registration
    await request(`${BRIDGE_URL}/unregister`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: regData.id }),
    });
  } finally {
    await close();
  }
}

async function testMockServerPortChange(): Promise<void> {
  const oldPort = 59989;
  const newPort = 59988;
  const directory = "/tmp/mock-port-change-test";

  // Start mock server on old port
  const mock1 = await createMockOpenCodeServer(oldPort, directory);

  // Register with old port
  const { data: regData } = await request(`${BRIDGE_URL}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      port: oldPort,
      hostname: "127.0.0.1",
      sessionId: "mock-old",
      directory,
    }),
  });

  // Verify forwarding works
  let { status, data } = await request(`${BRIDGE_URL}/notify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      directory,
      message: "Test before port change",
    }),
  });

  assertEqual(status, 200, "Should forward to old port");

  // Stop old server
  await mock1.close();

  // Start new server on new port
  const mock2 = await createMockOpenCodeServer(newPort, directory);

  try {
    // Update registration with new port (simulating discovery finding new port)
    await request(`${BRIDGE_URL}/unregister`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: regData.id }),
    });

    await request(`${BRIDGE_URL}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        port: newPort,
        hostname: "127.0.0.1",
        sessionId: "mock-new",
        directory,
      }),
    });

    // Verify forwarding works on new port
    ({ status, data } = await request(`${BRIDGE_URL}/notify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        directory,
        message: "Test after port change",
      }),
    }));

    assertEqual(status, 200, "Should forward to new port");
    assertEqual(data.success, true, "Forwarding should succeed");
  } finally {
    await mock2.close();

    // Clean up
    const { data: regsData } = await request(`${BRIDGE_URL}/registrations`);
    const reg = regsData.registrations.find(
      (r: any) => r.directory === directory,
    );
    if (reg) {
      await request(`${BRIDGE_URL}/unregister`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: reg.id }),
      });
    }
  }
}

// ============================================================================
// MAIN
// ============================================================================

async function main(): Promise<void> {
  console.log("\n🧪 OpenCode Bridge Discovery Tests\n");
  console.log(`Bridge URL: ${BRIDGE_URL}`);
  console.log(`Discovery URL: ${DISCOVERY_URL}\n`);
  console.log("─".repeat(60));

  // Basic health tests
  console.log("\n📡 Health & Connectivity\n");
  await runTest("Bridge health check", testBridgeHealth);
  await runTest("Discovery service health", testDiscoveryServiceHealth);

  // Discovery tests
  console.log("\n🔍 Discovery\n");
  await runTest("Bridge discovery endpoint", testBridgeDiscoveryEndpoint);
  await runTest("Registration after discovery", testRegistrationAfterDiscovery);
  await runTest("Identity mapping format", testIdentityMapping);
  await runTest(
    "Discovery updates existing registrations",
    testDiscoveryUpdatesExistingRegistration,
  );

  // Registration management tests
  console.log("\n📝 Registration Management\n");
  await runTest("Manual registration", testManualRegistration);
  await runTest("Heartbeat by ID", testHeartbeat);
  await runTest("Heartbeat by directory", testHeartbeatByDirectory);
  await runTest("Unregister", testUnregister);
  await runTest("Port change detection", testPortChangeDetection);

  // Notification tests
  console.log("\n📨 Notifications\n");
  await runTest("Rooms endpoint", testRoomsEndpoint);
  await runTest("Notify missing directory", testNotifyMissingDirectory);
  await runTest("Notify endpoint (if OpenCode running)", testNotifyEndpoint);

  // Mock server tests
  console.log("\n🎭 Mock Server Tests\n");
  await runTest("Mock server forwarding", testMockServerForwarding);
  await runTest("Mock server port change", testMockServerPortChange);

  // Summary
  console.log("\n" + "─".repeat(60));
  const passed = results.filter((r) => r.passed).length;
  const failed = results.filter((r) => !r.passed).length;
  const total = results.length;

  console.log(`\n📊 Results: ${passed}/${total} passed`);

  if (failed > 0) {
    console.log(`\n${RED}Failed tests:${RESET}`);
    for (const result of results.filter((r) => !r.passed)) {
      console.log(`  ${RED}✗${RESET} ${result.name}: ${result.message}`);
    }
    process.exit(1);
  } else {
    console.log(`\n${GREEN}All tests passed!${RESET}\n`);
    process.exit(0);
  }
}

main().catch((error) => {
  console.error("Test runner failed:", error);
  process.exit(1);
});
