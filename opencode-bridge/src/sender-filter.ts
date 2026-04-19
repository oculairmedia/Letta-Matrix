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

export interface RoomDeliveryAdmissionInput {
  senderMxid: string;
  body: string;
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
  const { senderMxid, body, roomOwnerIdentities, agentBotMxids, matrixDomain } = input;

  if (roomOwnerIdentities.includes(senderMxid)) {
    return { admit: false, reason: "sender is the room's own opencode identity" };
  }

  if (isKnownBotSender(senderMxid, agentBotMxids, matrixDomain)) {
    if (!bodyMentionsAny(body, roomOwnerIdentities)) {
      return {
        admit: false,
        reason: `bot sender ${senderMxid} without explicit mention of room owner`,
      };
    }
  }

  return { admit: true, reason: "admit" };
}
