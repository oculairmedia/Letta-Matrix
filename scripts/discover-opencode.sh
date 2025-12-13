#!/bin/bash
# Discover running OpenCode instances and output JSON
# This script runs on the host and outputs JSON with OpenCode instances

output="["
first=true

# Find all opencode processes
for pid in $(pgrep -x opencode 2>/dev/null); do
    # Get working directory
    cwd=$(readlink -f /proc/$pid/cwd 2>/dev/null)
    
    # Get listening port
    port=$(ss -tlnp 2>/dev/null | grep "pid=$pid," | grep -oP '127\.0\.0\.1:\K\d+' | head -1)
    
    if [[ -n "$cwd" && -n "$port" ]]; then
        if [[ "$first" == "false" ]]; then
            output="$output,"
        fi
        first=false
        output="$output{\"pid\":$pid,\"directory\":\"$cwd\",\"port\":$port}"
    fi
done

output="$output]"
echo "$output"
