import type { Plugin } from "@opencode-ai/plugin"
import { tool } from "@opencode-ai/plugin"
import { spawn, type ChildProcess } from "child_process"
import { createInterface } from "readline"

// Log at module load time to verify plugin is being loaded
console.log("[P2P-Bridge] Plugin module loading...")

export const P2PBridge: Plugin = async ({ project, client, $, directory, worktree }) => {
  console.log("[P2P-Bridge] Disabled: Matrix direct mode active")
  return {}

  const P2P_ROOM = process.env.P2P_ROOM || "agent-swarm-global"
  const P2P_TICKET = process.env.P2P_TICKET
  const P2P_IDENTITY = process.env.P2P_IDENTITY || `opencode-${directory.split("/").pop()}`

  let daemon: ChildProcess | null = null

  console.log(`[P2P-Bridge] Plugin initialized for ${directory}`)
  console.log(`[P2P-Bridge] Room: ${P2P_ROOM}, Identity: ${P2P_IDENTITY}, Ticket: ${P2P_TICKET ? "set" : "not set"}`)

  client.app.log({
    body: {
      service: "p2p-bridge",
      level: "info",
      message: `Plugin initialized for ${directory}`,
      extra: { room: P2P_ROOM, identity: P2P_IDENTITY, ticket: P2P_TICKET ? "set" : "not set" }
    }
  }).catch(() => { })

  function parseP2PMessage(line: string) {
    const msgMatch = line.match(/^\[[\d:]+\]\s+<([^@]+)@([^>]+)>\s+(.+)$/)
    if (msgMatch) {
      return {
        type: "message" as const,
        identity: msgMatch[1],
        nodeId: msgMatch[2],
        content: msgMatch[3],
      }
    }

    const ticketMatch = line.match(/^\s+Ticket:\s+(\S+)/)
    if (ticketMatch) {
      return { type: "ticket" as const, ticket: ticketMatch[1] }
    }

    const peerMatch = line.match(/\*\* peer connected: ([^\s]+)/)
    if (peerMatch) {
      return { type: "peer_connected" as const, peerId: peerMatch[1] }
    }

    const readyMatch = line.match(/\*\* ready!/)
    if (readyMatch) {
      return { type: "ready" as const }
    }

    return null
  }

  async function getActiveSessionId(): Promise<string | null> {
    try {
      const response = await client.session.list()
      if (response.data && response.data.length > 0) {
        const sorted = [...response.data].sort((a: any, b: any) =>
          new Date(b.time?.updated || 0).getTime() - new Date(a.time?.updated || 0).getTime()
        )
        return sorted[0].id
      }
    } catch (e) { }
    return null
  }



  async function handleIncomingMessage(identity: string, content: string) {
    if (identity === P2P_IDENTITY) return

    console.log(`[P2P-Bridge] Received message from ${identity}: ${content.substring(0, 50)}...`)
    
    client.tui.showToast({
      body: { message: `P2P: ${identity}: ${content.substring(0, 50)}...`, variant: "info" }
    }).catch(() => {})

    const sessionId = await getActiveSessionId()
    if (!sessionId) {
      console.log("[P2P-Bridge] No active session to inject message into")
      client.tui.showToast({
        body: { message: "P2P: No active session!", variant: "warning" }
      }).catch(() => {})
      return
    }

    try {
      await client.session.prompt({
        path: { id: sessionId },
        body: {
          noReply: true,
          parts: [{ 
            type: "text", 
            text: `[P2P Network Message from ${identity}]\n${content}` 
          }],
        },
      })
      
      console.log(`[P2P-Bridge] Injected message from ${identity} into session ${sessionId}`)
      
      client.tui.showToast({
        body: { message: `P2P message injected from ${identity}`, variant: "success" }
      }).catch(() => {})
    } catch (e: any) {
      console.log(`[P2P-Bridge] Failed to inject message: ${e.message}`)
      client.tui.showToast({
        body: { message: `P2P inject failed: ${e.message}`, variant: "error" }
      }).catch(() => {})
    }
  }

  function startDaemon() {
    if (daemon) return

    const args = ["daemon", "--identity", P2P_IDENTITY, "--room", P2P_ROOM]
    if (P2P_TICKET) {
      args.push("--join", P2P_TICKET)
    }

    console.log(`[P2P-Bridge] Starting daemon: real-a2a ${args.join(" ")}`)

    client.app.log({
      body: {
        service: "p2p-bridge",
        level: "info",
        message: `Starting daemon: real-a2a ${args.join(" ")}`
      }
    })

    daemon = spawn("real-a2a", args, {
      stdio: ["pipe", "pipe", "pipe"],
    })

    const rl = createInterface({ input: daemon.stdout! })

    rl.on("line", (line) => {
      console.log(`[P2P-Bridge] Daemon stdout: ${line}`)
      const parsed = parseP2PMessage(line)
      if (!parsed) return

      switch (parsed.type) {
        case "message":
          handleIncomingMessage(parsed.identity, parsed.content)
          break
        case "ticket":
          console.log(`[P2P-Bridge] Ticket generated: ${parsed.ticket.substring(0, 50)}...`)
          client.app.log({
            body: {
              service: "p2p-bridge",
              level: "info",
              message: `Ticket generated: ${parsed.ticket.substring(0, 50)}...`
            }
          })
          break
        case "ready":
          console.log("[P2P-Bridge] Daemon ready and connected")
          client.app.log({
            body: {
              service: "p2p-bridge",
              level: "info",
              message: "Daemon ready and connected"
            }
          })
          break
        case "peer_connected":
          console.log(`[P2P-Bridge] Peer connected: ${parsed.peerId}`)
          client.app.log({
            body: {
              service: "p2p-bridge",
              level: "info",
              message: `Peer connected: ${parsed.peerId}`
            }
          })
          break
      }
    })

    daemon.stderr?.on("data", (data) => {
      const text = data.toString().trim()
      if (text) {
        console.log(`[P2P-Bridge] Daemon stderr: ${text}`)
        client.app.log({
          body: {
            service: "p2p-bridge",
            level: "warn",
            message: `stderr: ${text}`
          }
        })
      }
    })

    daemon.on("close", (code) => {
      console.log(`[P2P-Bridge] Daemon exited with code ${code}`)
      client.app.log({
        body: {
          service: "p2p-bridge",
          level: "info",
          message: `Daemon exited with code ${code}`
        }
      })
      daemon = null
    })

    daemon.on("error", (err) => {
      console.log(`[P2P-Bridge] Daemon error: ${err.message}`)
      client.app.log({
        body: {
          service: "p2p-bridge",
          level: "error",
          message: `Daemon error: ${err.message}`
        }
      })
      daemon = null
    })
  }

  function stopDaemon() {
    if (daemon) {
      daemon.kill()
      daemon = null
    }
  }

  async function sendMessage(message: string): Promise<boolean> {
    if (!daemon || !daemon.stdin) {
      console.log("[P2P-Bridge] Cannot send - daemon not running or no stdin")
      return false
    }

    try {
      daemon.stdin.write(message + "\n")
      console.log(`[P2P-Bridge] Sent message via stdin: ${message.substring(0, 50)}...`)
      return true
    } catch (e: any) {
      console.log(`[P2P-Bridge] Send failed: ${e.message}`)
      await client.app.log({
        body: {
          service: "p2p-bridge",
          level: "error",
          message: `Send failed: ${e.message}`
        }
      })
      return false
    }
  }

  // Start daemon after a short delay if ticket is set
  if (P2P_TICKET) {
    console.log("[P2P-Bridge] P2P_TICKET is set, will start daemon in 2 seconds...")
    setTimeout(() => startDaemon(), 2000)
  } else {
    console.log("[P2P-Bridge] P2P_TICKET not set, daemon will not auto-start")
  }

  return {
    tool: {
      p2p_send: tool({
        description: "Send a message to the P2P agent network",
        args: {
          message: tool.schema.string().describe("The message to send to other agents on the P2P network"),
        },
        async execute(args) {
          if (!daemon) {
            startDaemon()
            await new Promise((r) => setTimeout(r, 3000))
          }

          const success = await sendMessage(args.message)
          return JSON.stringify({
            success,
            identity: P2P_IDENTITY,
            room: P2P_ROOM,
          })
        },
      }),

      p2p_connect: tool({
        description: "Connect to a P2P agent network using a ticket",
        args: {
          ticket: tool.schema.string().describe("The P2P ticket to join the network"),
        },
        async execute(args) {
          stopDaemon()
          process.env.P2P_TICKET = args.ticket
          startDaemon()

          await new Promise((r) => setTimeout(r, 3000))

          return JSON.stringify({
            success: daemon !== null,
            identity: P2P_IDENTITY,
            room: P2P_ROOM,
            connected: true,
          })
        },
      }),

      p2p_status: tool({
        description: "Get P2P bridge connection status",
        args: {},
        async execute() {
          return JSON.stringify({
            connected: daemon !== null,
            identity: P2P_IDENTITY,
            room: P2P_ROOM,
            ticket: P2P_TICKET ? "set" : "not set",
          })
        },
      }),
    },
  }
}


