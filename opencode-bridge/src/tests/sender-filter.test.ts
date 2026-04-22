import { test } from "node:test";
import assert from "node:assert/strict";
import {
  bodyMentionsAny,
  isAdmissibleForRoomDelivery,
  isKnownBotSender,
  isOpenCodeIdentity,
  mentionUserIdsInclude,
} from "../sender-filter.js";

const DOMAIN = "matrix.oculair.ca";
const ROOM_OWNER = ["@oc_letta_mobile:matrix.oculair.ca", "@oc_letta_mobile_v2:matrix.oculair.ca"];
const AGENT_BOTS = new Set<string>(["@lettapm_agent:matrix.oculair.ca"]);

test("isOpenCodeIdentity matches @oc_ and @opencode_ on the configured domain", () => {
  assert.equal(isOpenCodeIdentity("@oc_foo:matrix.oculair.ca", DOMAIN), true);
  assert.equal(isOpenCodeIdentity("@opencode_bar:matrix.oculair.ca", DOMAIN), true);
  assert.equal(isOpenCodeIdentity("@user:matrix.oculair.ca", DOMAIN), false);
  assert.equal(isOpenCodeIdentity("@oc_foo:other.example", DOMAIN), false);
});

test("isKnownBotSender combines opencode identities and configured agent bots", () => {
  assert.equal(isKnownBotSender("@oc_any:matrix.oculair.ca", AGENT_BOTS, DOMAIN), true);
  assert.equal(isKnownBotSender("@lettapm_agent:matrix.oculair.ca", AGENT_BOTS, DOMAIN), true);
  assert.equal(isKnownBotSender("@alice:matrix.oculair.ca", AGENT_BOTS, DOMAIN), false);
});

test("bodyMentionsAny is case-insensitive and handles empty inputs", () => {
  assert.equal(bodyMentionsAny("hello @OC_Letta_Mobile:matrix.oculair.ca", ROOM_OWNER), true);
  assert.equal(bodyMentionsAny("no mention here", ROOM_OWNER), false);
  assert.equal(bodyMentionsAny("", ROOM_OWNER), false);
  assert.equal(bodyMentionsAny("anything", []), false);
});

test("admits a human sender", () => {
  const r = isAdmissibleForRoomDelivery({
    senderMxid: "@emmanuel:matrix.oculair.ca",
    body: "hey, can you help?",
    roomOwnerIdentities: ROOM_OWNER,
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });
  assert.equal(r.admit, true);
});

test("drops the room's own opencode identity (echo)", () => {
  const r = isAdmissibleForRoomDelivery({
    senderMxid: ROOM_OWNER[0],
    body: "self echo",
    roomOwnerIdentities: ROOM_OWNER,
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });
  assert.equal(r.admit, false);
  assert.match(r.reason, /own opencode identity/);
});

test("drops a known agent bot reply with no mention (the Letta PM feedback loop)", () => {
  const r = isAdmissibleForRoomDelivery({
    senderMxid: "@lettapm_agent:matrix.oculair.ca",
    body: "⏳ Queued (position 5) — will process after current task.",
    roomOwnerIdentities: ROOM_OWNER,
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });
  assert.equal(r.admit, false);
  assert.match(r.reason, /bot sender.*without explicit mention/);
});

test("admits a known agent bot when it explicitly mentions the room owner", () => {
  const r = isAdmissibleForRoomDelivery({
    senderMxid: "@lettapm_agent:matrix.oculair.ca",
    body: "@oc_letta_mobile:matrix.oculair.ca please handle this",
    roomOwnerIdentities: ROOM_OWNER,
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });
  assert.equal(r.admit, true);
});

test("drops a peer @oc_* identity chatter without mention", () => {
  const r = isAdmissibleForRoomDelivery({
    senderMxid: "@oc_matrix_tuwunel_deploy:matrix.oculair.ca",
    body: "unrelated chatter",
    roomOwnerIdentities: ROOM_OWNER,
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });
  assert.equal(r.admit, false);
});

test("admits a peer @oc_* identity that explicitly mentions the room owner", () => {
  const r = isAdmissibleForRoomDelivery({
    senderMxid: "@oc_matrix_tuwunel_deploy:matrix.oculair.ca",
    body: "hey @oc_letta_mobile_v2:matrix.oculair.ca can you take over?",
    roomOwnerIdentities: ROOM_OWNER,
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });
  assert.equal(r.admit, true);
});

test("mentionUserIdsInclude is case-insensitive and handles empty inputs", () => {
  assert.equal(mentionUserIdsInclude(["@OC_Letta_Mobile_v2:matrix.oculair.ca"], ROOM_OWNER), true);
  assert.equal(mentionUserIdsInclude(["@someone_else:matrix.oculair.ca"], ROOM_OWNER), false);
  assert.equal(mentionUserIdsInclude([], ROOM_OWNER), false);
  assert.equal(mentionUserIdsInclude(undefined, ROOM_OWNER), false);
  assert.equal(mentionUserIdsInclude(["@oc_letta_mobile:matrix.oculair.ca"], []), false);
});

test("admits an agent bot that pill-mentions the room owner (body has displayname only)", () => {
  // Reproduces the displayname-rename drop: pill clients put the displayname in
  // body, so bodyMentionsAny returns false. The MXID must come from m.mentions.user_ids.
  const r = isAdmissibleForRoomDelivery({
    senderMxid: "@lettapm_agent:matrix.oculair.ca",
    body: "@Letta Mobile please handle this",
    mentionUserIds: ["@oc_letta_mobile_v2:matrix.oculair.ca"],
    roomOwnerIdentities: ROOM_OWNER,
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });
  assert.equal(r.admit, true);
});

test("still drops an agent bot when neither body nor pills mention the room owner", () => {
  const r = isAdmissibleForRoomDelivery({
    senderMxid: "@lettapm_agent:matrix.oculair.ca",
    body: "just some chatter with @Someone Else",
    mentionUserIds: ["@oc_other_project_v2:matrix.oculair.ca"],
    roomOwnerIdentities: ROOM_OWNER,
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });
  assert.equal(r.admit, false);
  assert.match(r.reason, /bot sender.*without explicit mention/);
});

test("admits a peer @oc_* identity via pill-only mention", () => {
  const r = isAdmissibleForRoomDelivery({
    senderMxid: "@oc_matrix_tuwunel_deploy:matrix.oculair.ca",
    body: "hey @Letta Mobile can you take over?",
    mentionUserIds: ["@oc_letta_mobile_v2:matrix.oculair.ca"],
    roomOwnerIdentities: ROOM_OWNER,
    agentBotMxids: AGENT_BOTS,
    matrixDomain: DOMAIN,
  });
  assert.equal(r.admit, true);
});
