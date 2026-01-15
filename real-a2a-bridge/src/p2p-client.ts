import { Subprocess } from "bun";
import { EventEmitter } from "events";
import type { P2PMessage } from "./types";

export class P2PClient extends EventEmitter {
  private daemon: Subprocess | null = null;
  private identity: string;
  private room: string;
  private ticket?: string;
  private ready: boolean = false;

  constructor(identity: string, room: string, ticket?: string) {
    super();
    this.identity = identity;
    this.room = room;
    this.ticket = ticket;
  }

  async start(): Promise<void> {
    const args = ["daemon", "--identity", this.identity, "--room", this.room];
    if (this.ticket) {
      args.push("--join", this.ticket);
    }

    console.log(`[P2P] Starting daemon: real-a2a ${args.join(" ")}`);

    this.daemon = Bun.spawn(["real-a2a", ...args], {
      stdout: "pipe",
      stderr: "pipe",
      stdin: "ignore",
    });

    this.watchStdout();
    this.watchStderr();

    await this.waitForReady();
  }

  private watchStdout(): void {
    if (!this.daemon?.stdout) return;

    const reader = this.daemon.stdout.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    const read = async () => {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          this.parseLine(line);
        }
      }
    };

    read().catch((err) => console.error("[P2P] Stdout read error:", err));
  }

  private watchStderr(): void {
    if (!this.daemon?.stderr) return;

    const reader = this.daemon.stderr.getReader();
    const decoder = new TextDecoder();

    const read = async () => {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        console.error("[P2P ERROR]", decoder.decode(value));
      }
    };

    read().catch((err) => console.error("[P2P] Stderr read error:", err));
  }

  private parseLine(line: string): void {
    console.log("[P2P RAW]", line);

    const messageMatch = line.match(/\[(\d{2}:\d{2}:\d{2})\] <(.+?)@(.+?)> (.+)/);
    if (messageMatch) {
      const [, timestamp, fromName, fromId, content] = messageMatch;
      const shortId = fromId.slice(0, 8);
      
      const message: P2PMessage = {
        timestamp,
        fromName,
        fromId: shortId,
        content,
        messageId: `p2p:${shortId}:${timestamp}:${Date.now()}`,
      };

      this.emit("message", message);
      return;
    }

    if (line.includes("peer connected")) {
      const peerMatch = line.match(/peer connected: (.+?)\.\.\./);
      if (peerMatch) {
        const peerId = peerMatch[1];
        this.emit("peer-connected", peerId);
        console.log(`[P2P] Peer connected: ${peerId}`);
      }
      return;
    }

    if (line.includes("peer disconnected")) {
      const peerMatch = line.match(/peer disconnected: (.+?)\.\.\./);
      if (peerMatch) {
        const peerId = peerMatch[1];
        this.emit("peer-disconnected", peerId);
        console.log(`[P2P] Peer disconnected: ${peerId}`);
      }
      return;
    }

    if (line.includes("Ticket:")) {
      const ticket = line.split("Ticket:")[1].trim();
      this.emit("ticket", ticket);
      console.log(`[P2P] Generated ticket: ${ticket}`);
      return;
    }

    if (line.includes("ready! waiting for messages")) {
      this.ready = true;
      this.emit("ready");
      console.log("[P2P] Daemon ready!");
      return;
    }
  }

  private async waitForReady(): Promise<void> {
    return new Promise((resolve) => {
      if (this.ready) {
        resolve();
        return;
      }

      const timeout = setTimeout(() => {
        console.warn("[P2P] Ready timeout - proceeding anyway");
        resolve();
      }, 10000);

      this.once("ready", () => {
        clearTimeout(timeout);
        resolve();
      });
    });
  }

  async sendMessage(content: string): Promise<void> {
    const proc = Bun.spawn(
      ["real-a2a", "send", "--identity", this.identity, content],
      {
        stdout: "pipe",
        stderr: "pipe",
      }
    );

    const exitCode = await proc.exited;
    if (exitCode !== 0) {
      const stderr = await new Response(proc.stderr).text();
      throw new Error(`Send failed: ${stderr}`);
    }
  }

  async stop(): Promise<void> {
    if (this.daemon) {
      this.daemon.kill();
      await this.daemon.exited;
      console.log("[P2P] Daemon stopped");
    }
  }
}
