import { test } from "node:test";
import assert from "node:assert/strict";

import { selectMemberFallbackTarget } from "../member-fallback.js";

const DOMAIN = "matrix.oculair.ca";
const AGENT_BOTS = new Set<string>(["@lettapm_agent:matrix.oculair.ca"]);
const TARGET = {
  mxid: "@oc_matrix_tuwunel_deploy_v2:matrix.oculair.ca",
  roomOwnerIdentities: [
    "@oc_matrix_tuwunel_deploy:matrix.oculair.ca",
    "@oc_matrix_tuwunel_deploy_v2:matrix.oculair.ca",
  ],
} as const;

test("drops peer OpenCode chatter in member fallback when target is not explicitly mentioned", () => {
  const result = selectMemberFallbackTarget({
    senderMxid: "@oc_letta_code_parallel_v2:matrix.oculair.ca",
    body: "status update from another room",
    candidates: [TARGET],
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });

  assert.equal(result.kind, "drop");
  if (result.kind === "drop") {
    assert.equal(result.target.mxid, TARGET.mxid);
    assert.match(result.reason, /without explicit mention/);
  }
});

test("forwards member fallback when a peer OpenCode sender explicitly mentions the target", () => {
  const result = selectMemberFallbackTarget({
    senderMxid: "@oc_letta_code_parallel_v2:matrix.oculair.ca",
    body: "hey @oc_matrix_tuwunel_deploy_v2:matrix.oculair.ca can you take this?",
    candidates: [TARGET],
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });

  assert.deepEqual(result, { kind: "forward", target: TARGET });
});

test("does not choose a member fallback target when multiple OpenCode identities are present", () => {
  const result = selectMemberFallbackTarget({
    senderMxid: "@emmanuel:matrix.oculair.ca",
    body: "who can help?",
    candidates: [
      TARGET,
      {
        mxid: "@oc_vibesync_v2:matrix.oculair.ca",
        roomOwnerIdentities: [
          "@oc_vibesync:matrix.oculair.ca",
          "@oc_vibesync_v2:matrix.oculair.ca",
        ],
      },
    ],
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });

  assert.equal(result.kind, "ambiguous");
  if (result.kind === "ambiguous") {
    assert.equal(result.candidates.length, 2);
  }
});
