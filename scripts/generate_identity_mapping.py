#!/usr/bin/env python3
"""
Generate Agent Mail identity mapping from Matrix agent data

This script creates agent_mail_mappings.json by reading agent_user_mappings.json
and generating valid Agent Mail names for each agent.

Usage:
    python generate_identity_mapping.py
"""

import json
import re
from pathlib import Path
from datetime import datetime, timezone


def sanitize_for_agent_mail(matrix_name: str) -> str:
    """
    Convert Matrix agent name to valid Agent Mail name
    
    Examples:
        "Huly - Matrix Synapse Deployment" → "HulyMatrixSynapse"
        "BMO" → "BMO"
        "Meridian" → "Meridian"
        "GraphitiExplorer" → "GraphitiExplorer"
        "letta-cli-agent" → "LettaCliAgent"
    """
    # Remove special chars except spaces
    clean = re.sub(r'[^a-zA-Z0-9\s]', '', matrix_name)
    
    # Split and capitalize
    words = clean.split()
    
    # Handle single word
    if len(words) == 1:
        return words[0].capitalize()
    
    # Take first 3 words, CamelCase
    return ''.join(word.capitalize() for word in words[:3])


def generate_mapping(data_dir: Path):
    """Generate identity mapping from Matrix data"""
    
    agent_file = data_dir / "agent_user_mappings.json"
    if not agent_file.exists():
        print(f"❌ Matrix agent mappings not found: {agent_file}")
        return
    
    print(f"📖 Reading Matrix agent data from {agent_file}")
    with open(agent_file) as f:
        agent_data = json.load(f)
    
    mappings = {}
    skipped = []
    
    for agent_id, data in agent_data.items():
        matrix_name = data.get('agent_name', 'UnknownAgent')
        matrix_user_id = data.get('matrix_user_id')
        room_id = data.get('room_id')
        
        if not matrix_user_id:
            skipped.append(f"{agent_id}: missing user_id")
            continue
        
        if not room_id:
            skipped.append(f"{agent_id} ({matrix_name}): missing room_id")
            continue
        
        # Generate Agent Mail name
        mail_name = sanitize_for_agent_mail(matrix_name)
        
        # Check for duplicates
        duplicate = False
        for existing_id, existing_info in mappings.items():
            if existing_info['agent_mail_name'] == mail_name and existing_id != agent_id:
                print(f"⚠️  Duplicate name detected:")
                print(f"   {existing_id} ({existing_info['matrix_name']}) → {mail_name}")
                print(f"   {agent_id} ({matrix_name}) → {mail_name}")
                # Append agent_id suffix to make unique
                mail_name = f"{mail_name}{len(agent_id[:8])}"
                print(f"   Resolving to: {mail_name}")
                duplicate = True
                break
        
        # Store mapping
        mappings[agent_id] = {
            'matrix_user_id': matrix_user_id,
            'matrix_room_id': room_id,
            'matrix_name': matrix_name,
            'agent_mail_name': mail_name,
            'agent_mail_registered': False,
            'last_sync': datetime.now(timezone.utc).isoformat()
        }
    
    # Save mapping file
    output_file = data_dir / "agent_mail_mappings.json"
    with open(output_file, 'w') as f:
        json.dump(mappings, f, indent=2)
    
    print(f"\n✅ Generated identity mapping for {len(mappings)} agents")
    print(f"📝 Saved to: {output_file}")
    
    if skipped:
        print(f"\n⚠️  Skipped {len(skipped)} agents:")
        for item in skipped:
            print(f"   - {item}")
    
    # Print sample mappings
    print(f"\n📋 Sample mappings:")
    for i, (agent_id, info) in enumerate(list(mappings.items())[:5]):
        print(f"   {info['matrix_name']:<40} → {info['agent_mail_name']}")
    
    if len(mappings) > 5:
        print(f"   ... and {len(mappings) - 5} more")
    
    return mappings


def main():
    """Main entry point"""
    data_dir = Path("/opt/stacks/matrix-synapse-deployment/matrix_client_data")
    
    print("🔧 Agent Mail Identity Mapping Generator")
    print("=" * 60)
    print()
    
    mapping = generate_mapping(data_dir)
    
    if mapping:
        print()
        print("=" * 60)
        print("✅ Identity mapping generated successfully!")
        print()
        print("Next steps:")
        print("1. Review the mapping in agent_mail_mappings.json")
        print("2. Deploy the bridge service with Docker Compose")
        print("3. Bridge will auto-register agents in Agent Mail")


if __name__ == '__main__':
    main()
