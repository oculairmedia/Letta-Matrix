export function isOpenCodeIdentity(mxid: string, matrixDomain: string): boolean {
  if (!mxid.endsWith(`:${matrixDomain}`)) return false;
  return mxid.startsWith("@oc_") || mxid.startsWith("@opencode_");
}

export function isKnownBotSender(
  mxid: string,
  agentBotMxids: ReadonlySet<string>,
  matrixDomain: string
): boolean {
  return isOpenCodeIdentity(mxid, matrixDomain) || agentBotMxids.has(mxid);
}

export function bodyMentionsAny(body: string, mxids: ReadonlyArray<string>): boolean {
  if (mxids.length === 0 || !body) return false;
  const lower = body.toLowerCase();
  return mxids.some((id) => lower.includes(id.toLowerCase()));
}

// Pill clients (Element et al.) render @-mentions as the target's displayname
// in `body`, so body-substring matching misses them. MSC 3952 m.mentions.user_ids
// carries the authoritative MXID list — check there first.
export function mentionUserIdsInclude(
  mentionUserIds: ReadonlyArray<string> | undefined,
  mxids: ReadonlyArray<string>
): boolean {
  if (!mentionUserIds || mentionUserIds.length === 0 || mxids.length === 0) return false;
  const target = new Set(mxids.map((id) => id.toLowerCase()));
  return mentionUserIds.some((id) => typeof id === "string" && target.has(id.toLowerCase()));
}

export interface RoomDeliveryAdmissionInput {
  senderMxid: string;
  body: string;
  mentionUserIds?: ReadonlyArray<string>;
  roomOwnerIdentities: ReadonlyArray<string>;
  agentBotMxids: ReadonlySet<string>;
  matrixDomain: string;
}

export interface RoomDeliveryAdmissionResult {
  admit: boolean;
  reason: string;
}

export function isAdmissibleForRoomDelivery(
  input: RoomDeliveryAdmissionInput
): RoomDeliveryAdmissionResult {
  const { senderMxid, body, mentionUserIds, roomOwnerIdentities, agentBotMxids, matrixDomain } = input;

  if (roomOwnerIdentities.includes(senderMxid)) {
    return { admit: false, reason: "sender is the room's own opencode identity" };
  }

  if (isKnownBotSender(senderMxid, agentBotMxids, matrixDomain)) {
    const mentioned =
      mentionUserIdsInclude(mentionUserIds, roomOwnerIdentities) ||
      bodyMentionsAny(body, roomOwnerIdentities);
    if (!mentioned) {
      return {
        admit: false,
        reason: `bot sender ${senderMxid} without explicit mention of room owner`,
      };
    }
  }

  return { admit: true, reason: "admit" };
}
