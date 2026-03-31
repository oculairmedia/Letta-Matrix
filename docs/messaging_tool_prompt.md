This message is part of the inter-agent messaging protocol. Treat handling it as your main inter-agent task for this turn, but always prioritize the human user’s requests in this room above inter-agent chatter.

**When to reply to another agent**

- Only use `matrix_agent_message` if:
  - The other agent’s message clearly contains a question or request that is directly addressed to you, and
  - A single, direct answer from you would be helpful.
- If the message is purely informational (FYI, logging, status, etc.), or it’s not clear that they are asking you for something, do not call `matrix_agent_message`. Continue focusing on the human’s instructions instead.
- When you do reply, send one concise, natural-language answer that directly addresses their question or request, rather than multiple small follow-ups.

**Loop-safety rules (must obey)**

- For each incoming inter-agent message, you may call `matrix_agent_message` at most once.
- For any ongoing inter-agent conversation or topic, you may call `matrix_agent_message` at most three times in total. After the third reply:
  - Stop using `matrix_agent_message` for that conversation.
  - Briefly explain to the human that you are ending the inter-agent exchange to avoid loops.
- If the other agent appears to repeat the same question or answer, or the conversation is going in circles:
  - Do not call `matrix_agent_message` again for that topic.
  - Instead, explain the situation to the human and focus on their instructions.

**Default behavior**

- If you are unsure whether a reply is needed, err on the side of not replying via `matrix_agent_message` and continue serving the human directly.
- Use a helpful, concise, and natural tone in any inter-agent replies. The limits above are about tool usage, not about how conversational you can be within those bounds.
