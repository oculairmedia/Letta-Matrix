#!/bin/sh
set -e

DATA_DIR="${TUWUNEL_DATABASE_PATH:-/var/lib/tuwunel}"
CURRENT_FILE="$DATA_DIR/CURRENT"

echo "Tuwunel startup: validating database consistency..."

# Check if CURRENT file exists and points to a valid MANIFEST
if [ -f "$CURRENT_FILE" ]; then
    CURRENT_MANIFEST=$(cat "$CURRENT_FILE")
    MANIFEST_PATH="$DATA_DIR/$CURRENT_MANIFEST"
    
    if [ ! -f "$MANIFEST_PATH" ]; then
        echo "WARNING: CURRENT points to missing manifest: $CURRENT_MANIFEST"
        echo "Auto-repairing: finding latest MANIFEST..."
        
        # Find the latest MANIFEST file by number
        LATEST_MANIFEST=$(ls -1 "$DATA_DIR"/MANIFEST-* 2>/dev/null | sort -V | tail -n 1 | xargs basename 2>/dev/null || echo "")
        
        if [ -n "$LATEST_MANIFEST" ] && [ -f "$DATA_DIR/$LATEST_MANIFEST" ]; then
            echo "Found latest manifest: $LATEST_MANIFEST"
            echo "$LATEST_MANIFEST" > "$CURRENT_FILE"
            echo "✓ Repaired CURRENT file"
        else
            echo "ERROR: No valid MANIFEST files found in $DATA_DIR"
            exit 1
        fi
    else
        echo "✓ Database consistency OK: $CURRENT_MANIFEST exists"
    fi
else
    echo "No CURRENT file found - assuming fresh database"
fi

echo "Starting Tuwunel..."
exec /usr/local/bin/tuwunel "$@"
