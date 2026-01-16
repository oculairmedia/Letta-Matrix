import type { Middleware } from "xmcp";
import { initializeServices, getServices } from "./core/services.js";

let servicesInitialized = false;

const middleware: Middleware = async (req, res, next) => {
  if (!servicesInitialized) {
    await initializeServices();
    servicesInitialized = true;
    console.log("[Middleware] Services initialized");
  }

  const agentId = req.headers["x-agent-id"];
  if (agentId && typeof agentId === "string") {
    const services = getServices();
    if (services) {
      services.currentAgentId = agentId;
    }
  }

  return next();
};

export default middleware;
