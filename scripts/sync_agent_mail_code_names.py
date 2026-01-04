#!/usr/bin/env python3
"""
Sync Agent Mail code names to bridge mapping file.

Agent Mail assigns internal code names (like "BlueCreek", "WhiteStone") 
when agents are registered. This script queries the Agent Mail database
and updates the bridge's mapping file with the correct code names.

Usage:
    python3 sync_agent_mail_code_names.py
"""

import json
import sqlite3
import sys
from pathlib import Path

# Configuration
AGENT_MAIL_DB = "/opt/stacks/mcp_agent_mail/storage.sqlite3"
MAPPING_FILE = "/opt/stacks/matrix-synapse-deployment/matrix_client_data/agent_mail_mappings.json"
PROJECT_SLUG = "opt-stacks-matrix-synapse-deployment"

def main():
    print("=" * 80)
    print("Agent Mail Code Name Sync")
    print("=" * 80)
    
    # Check if files exist
    if not Path(AGENT_MAIL_DB).exists():
        print(f"❌ Error: Agent Mail database not found at {AGENT_MAIL_DB}")
        sys.exit(1)
    
    if not Path(MAPPING_FILE).exists():
        print(f"❌ Error: Mapping file not found at {MAPPING_FILE}")
        sys.exit(1)
    
    # Load current mapping
    print(f"\n📂 Loading mapping file: {MAPPING_FILE}")
    with open(MAPPING_FILE, 'r') as f:
        mappings = json.load(f)
    print(f"   Found {len(mappings)} agents in mapping file")
    
    # Connect to Agent Mail database
    print(f"\n🔌 Connecting to Agent Mail database: {AGENT_MAIL_DB}")
    conn = sqlite3.connect(AGENT_MAIL_DB)
    cursor = conn.cursor()
    
    # Get all agents for our project
    cursor.execute("""
        SELECT a.name, a.task_description 
        FROM agents a 
        JOIN projects p ON a.project_id = p.id 
        WHERE p.slug = ?
    """, (PROJECT_SLUG,))
    
    # Create reverse mapping: task_description -> code_name
    db_mapping = {}
    for code_name, task_desc in cursor.fetchall():
        db_mapping[task_desc] = code_name
    
    print(f"   Found {len(db_mapping)} agents in Agent Mail database")
    
    if not db_mapping:
        print(f"\n⚠️  Warning: No agents found in Agent Mail for project '{PROJECT_SLUG}'")
        print("   Make sure agents are registered first!")
        conn.close()
        sys.exit(1)
    
    # Update mappings
    print(f"\n🔄 Syncing code names...")
    updated_count = 0
    not_found = []
    
    for agent_id, info in mappings.items():
        # Try to find by matrix_name or agent_mail_name
        search_names = [info.get('matrix_name'), info.get('agent_mail_name')]
        
        found = False
        for search_name in search_names:
            if search_name and search_name in db_mapping:
                old_name = info.get('agent_mail_name')
                new_name = db_mapping[search_name]
                
                if old_name != new_name:
                    info['agent_mail_name'] = new_name
                    updated_count += 1
                    print(f"   ✓ {search_name}: {old_name} → {new_name}")
                
                found = True
                break
        
        if not found:
            not_found.append(info.get('matrix_name', agent_id))
    
    # Save updated mappings
    if updated_count > 0:
        print(f"\n💾 Saving updated mapping file...")
        with open(MAPPING_FILE, 'w') as f:
            json.dump(mappings, f, indent=2)
        print(f"   ✅ Successfully updated {updated_count} agent(s)")
    else:
        print(f"\n✨ All agents already have correct code names (no updates needed)")
    
    # Report agents not found
    if not_found:
        print(f"\n⚠️  Warning: {len(not_found)} agent(s) not found in Agent Mail database:")
        for name in not_found[:10]:  # Show first 10
            print(f"   - {name}")
        if len(not_found) > 10:
            print(f"   ... and {len(not_found) - 10} more")
    
    conn.close()
    
    print("\n" + "=" * 80)
    print("Sync complete!")
    print("=" * 80)
    
    return 0 if not not_found else 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⏸️  Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
