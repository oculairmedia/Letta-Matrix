import { test } from "node:test";
import assert from "node:assert/strict";
import { formatMatrixMessageForOpenCode } from "../message-format.js";

test("formats Matrix messages with display sender and MXID attribution", () => {
  assert.equal(
    formatMatrixMessageForOpenCode({
      sender: "PM - matrix-tuwunel-deploy",
      senderMxid: "@agent_b19ab6d5_460b_48f7_b300_738021d9a882:matrix.oculair.ca",
      body: "@oc_matrix_tuwunel_deploy_v2:matrix.oculair.ca helo",
    }),
    "Matrix from PM - matrix-tuwunel-deploy (@agent_b19ab6d5_460b_48f7_b300_738021d9a882:matrix.oculair.ca):\n@oc_matrix_tuwunel_deploy_v2:matrix.oculair.ca helo",
  );
});

test("falls back to MXID when display sender is unknown", () => {
  assert.equal(
    formatMatrixMessageForOpenCode({
      sender: "unknown",
      senderMxid: "@agent_example:matrix.oculair.ca",
      body: "helo",
    }),
    "Matrix from @agent_example:matrix.oculair.ca:\nhelo",
  );
});
