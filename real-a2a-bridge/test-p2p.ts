#!/usr/bin/env bun

import { P2PClient } from "./src/p2p-client";

async function testP2P() {
  console.log("=== Testing P2P Client ===\n");

  console.log("1. Starting daemon without ticket (creates new room)...");
  const daemon1 = new P2PClient("test-agent-1", "test-room");
  
  let generatedTicket: string | null = null;
  daemon1.on("ticket", (ticket: string) => {
    generatedTicket = ticket;
    console.log("✅ Generated ticket:", ticket.slice(0, 20) + "...");
  });

  daemon1.on("ready", () => {
    console.log("✅ Daemon 1 ready!");
  });

  daemon1.on("message", (msg: any) => {
    console.log(`📨 Daemon 1 received: [${msg.fromName}] ${msg.content}`);
  });

  daemon1.on("peer-connected", (peerId: string) => {
    console.log(`🔗 Daemon 1: Peer connected: ${peerId}`);
  });

  await daemon1.start();

  console.log("\n2. Waiting for ticket generation...");
  await new Promise(r => setTimeout(r, 3000));

  if (!generatedTicket) {
    console.error("❌ No ticket generated!");
    await daemon1.stop();
    process.exit(1);
  }

  console.log("\n3. Starting second daemon with ticket (joins room)...");
  const daemon2 = new P2PClient("test-agent-2", "test-room", generatedTicket);
  
  daemon2.on("ready", () => {
    console.log("✅ Daemon 2 ready!");
  });

  daemon2.on("message", (msg: any) => {
    console.log(`📨 Daemon 2 received: [${msg.fromName}] ${msg.content}`);
  });

  daemon2.on("peer-connected", (peerId: string) => {
    console.log(`🔗 Daemon 2: Peer connected: ${peerId}`);
  });

  await daemon2.start();

  console.log("\n4. Waiting for peer connection...");
  await new Promise(r => setTimeout(r, 3000));

  console.log("\n5. Sending test messages...");
  
  await daemon1.sendMessage("Hello from agent 1!");
  await new Promise(r => setTimeout(r, 1000));
  
  await daemon2.sendMessage("Hello from agent 2!");
  await new Promise(r => setTimeout(r, 1000));
  
  await daemon1.sendMessage("How are you, agent 2?");
  await new Promise(r => setTimeout(r, 1000));
  
  await daemon2.sendMessage("Doing great! P2P works!");

  console.log("\n6. Waiting for messages to propagate...");
  await new Promise(r => setTimeout(r, 3000));

  console.log("\n7. Stopping daemons...");
  await daemon1.stop();
  await daemon2.stop();

  console.log("\n✅ Test complete!");
  process.exit(0);
}

testP2P().catch(err => {
  console.error("❌ Test failed:", err);
  process.exit(1);
});
