import { isAdmissibleForRoomDelivery } from "./sender-filter.js";

export interface MemberFallbackCandidate {
  mxid: string;
  roomOwnerIdentities: ReadonlyArray<string>;
}

export type MemberFallbackDecision =
  | { kind: "forward"; target: MemberFallbackCandidate }
  | { kind: "drop"; reason: string; target: MemberFallbackCandidate }
  | { kind: "ambiguous"; candidates: MemberFallbackCandidate[] }
  | { kind: "none" };

export interface MemberFallbackInput {
  senderMxid: string;
  body: string;
  mentionUserIds?: ReadonlyArray<string>;
  candidates: MemberFallbackCandidate[];
  agentBotMxids: ReadonlySet<string>;
  matrixDomain: string;
}

export function selectMemberFallbackTarget(
  input: MemberFallbackInput,
): MemberFallbackDecision {
  const { senderMxid, body, mentionUserIds, candidates, agentBotMxids, matrixDomain } = input;

  if (candidates.length === 0) {
    return { kind: "none" };
  }

  if (candidates.length > 1) {
    return { kind: "ambiguous", candidates: [...candidates] };
  }

  const target = candidates[0];
  const admission = isAdmissibleForRoomDelivery({
    senderMxid,
    body,
    mentionUserIds,
    roomOwnerIdentities: target.roomOwnerIdentities,
    agentBotMxids,
    matrixDomain,
  });

  if (!admission.admit) {
    return { kind: "drop", reason: admission.reason, target };
  }

  return { kind: "forward", target };
}
