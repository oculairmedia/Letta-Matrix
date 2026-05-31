export interface MatrixSenderAttribution {
  sender: string;
  senderMxid: string;
  body: string;
}

export function formatMatrixMessageForOpenCode({
  sender,
  senderMxid,
  body,
}: MatrixSenderAttribution): string {
  const displaySender = sender && sender !== "unknown" ? sender : senderMxid;
  const attribution = senderMxid && senderMxid !== displaySender
    ? `${displaySender} (${senderMxid})`
    : displaySender;

  return `Matrix from ${attribution}:\n${body}`;
}
