import type { XmcpConfig } from "xmcp";

const config: XmcpConfig = {
  http: {
    port: parseInt(process.env.PORT || "3100", 10),
    host: "0.0.0.0",
    endpoint: "/mcp",
    cors: {
      origin: "*",
      methods: ["GET", "POST", "DELETE", "OPTIONS"],
      allowedHeaders: [
        "Content-Type",
        "Authorization",
        "mcp-session-id",
        "mcp-protocol-version",
        "X-Agent-Id",
      ],
      exposedHeaders: ["Content-Type", "Authorization", "mcp-session-id"],
      credentials: false,
      maxAge: 86400,
    },
  },
  stdio: false,
};

export default config;
