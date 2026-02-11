#!/usr/bin/env python3
"""
Update all Letta agent system prompts to include inter-agent messaging instructions.
This reduces token usage by moving the instructions from runtime injection to system prompt.
"""

import os
import sys
import requests
from typing import List, Dict

# Letta API configuration
LETTA_API_URL = os.getenv("LETTA_API_URL", "http://192.168.50.90:8289")
LETTA_PASSWORD = os.getenv("LETTA_TOKEN") or os.getenv("LETTA_PASSWORD")

# Messaging instructions to add to system prompts
MESSAGING_INSTRUCTIONS = """

## INTER-AGENT MESSAGING PROTOCOL

You have access to a tool called `matrix_agent_message` that allows you to communicate with other Letta agents. Use this feature wisely to collaborate and avoid message loops.

**When to reply to another agent:**
- Only use `matrix_agent_message` if:
  - The other agent's message clearly contains a question or request that is directly addressed to you, and
  - A single, direct answer from you would be helpful.
- If the message is purely informational (FYI, logging, status, etc.), or it's not clear that they are asking you for something, do not call `matrix_agent_message`. Continue focusing on the human's instructions instead.
- When you do reply, send one concise, natural-language answer that directly addresses their question or request, rather than multiple small follow-ups.

**Loop-safety rules (MUST OBEY):**
- For each incoming inter-agent message, you may call `matrix_agent_message` at most once.
- For any ongoing inter-agent conversation or topic, you may call `matrix_agent_message` at most three times in total. After the third reply:
  - Stop using `matrix_agent_message` for that conversation.
  - Briefly explain to the human that you are ending the inter-agent exchange to avoid loops.
- If the other agent appears to repeat the same question or answer, or the conversation is going in circles:
  - Do not call `matrix_agent_message` again for that topic.
  - Instead, explain the situation to the human and focus on their instructions.

**Default behavior:**
- If you are unsure whether a reply is needed, err on the side of not replying via `matrix_agent_message` and continue serving the human directly.
- Use a helpful, concise, and natural tone in any inter-agent replies. The limits above are about tool usage, not about how conversational you can be within those bounds.
"""


def get_all_agents() -> List[Dict]:
    """Fetch all agents from Letta API"""
    headers = {"Authorization": f"Bearer {LETTA_PASSWORD}"}
    response = requests.get(f"{LETTA_API_URL}/v1/agents", headers=headers)
    response.raise_for_status()
    return response.json()


def update_agent_system_prompt(agent_id: str, agent_name: str, current_prompt: str) -> bool:
    """Update an agent's system prompt to include messaging instructions"""
    
    # Check if instructions are already present
    if "INTER-AGENT MESSAGING PROTOCOL" in current_prompt:
        print(f"  ✓ Agent '{agent_name}' already has messaging instructions")
        return False
    
    # Append messaging instructions to current prompt
    updated_prompt = current_prompt + MESSAGING_INSTRUCTIONS
    
    # Update the agent
    headers = {
        "Authorization": f"Bearer {LETTA_PASSWORD}",
        "Content-Type": "application/json"
    }
    payload = {
        "system": updated_prompt
    }
    
    response = requests.patch(
        f"{LETTA_API_URL}/v1/agents/{agent_id}",
        headers=headers,
        json=payload
    )
    
    if response.status_code == 200:
        print(f"  ✓ Updated agent '{agent_name}'")
        return True
    else:
        print(f"  ✗ Failed to update agent '{agent_name}': {response.status_code} - {response.text}")
        return False


def main():
    if not LETTA_PASSWORD:
        print("Error: LETTA_PASSWORD environment variable not set")
        sys.exit(1)
    
    print(f"Fetching agents from {LETTA_API_URL}...")
    agents = get_all_agents()
    print(f"Found {len(agents)} agents\n")
    
    updated_count = 0
    skipped_count = 0
    failed_count = 0
    
    for agent in agents:
        agent_id = agent.get("id")
        agent_name = agent.get("name", "Unknown")
        current_system = agent.get("system", "")
        
        print(f"Processing: {agent_name} ({agent_id})")
        
        try:
            if update_agent_system_prompt(agent_id, agent_name, current_system):
                updated_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            print(f"  ✗ Error: {e}")
            failed_count += 1
    
    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Updated: {updated_count}")
    print(f"  Skipped (already updated): {skipped_count}")
    print(f"  Failed: {failed_count}")
    print(f"  Total: {len(agents)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
