import type { XmcpConfig } from "xmcp";

const config: XmcpConfig = {
  http: {
    port: undefined as unknown as number,
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
  paths: {
    tools: "src/tools",
    prompts: false,
    resources: false,
  },
  typescript: {
    skipTypeCheck: true,
  },
  bundler: (config) => ({
    ...config,
    externals: [
      // Native modules cannot be bundled - must be loaded at runtime
      /\.node$/,
      "@matrix-org/matrix-sdk-crypto-nodejs",
    ],
    resolve: {
      ...(config.resolve ?? {}),
      fullySpecified: false,
      extensions: [
        ".ts",
        ".mts",
        ".js",
        ".mjs",
        ".json",
        ...(config.resolve?.extensions ?? []),
      ],
      extensionAlias: {
        ".js": [".ts", ".mts", ".js"],
        ".mjs": [".mts", ".mjs"],
      },
    },
  }),
};

export default config;
