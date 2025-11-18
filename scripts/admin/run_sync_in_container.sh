#!/bin/bash
#
# Run the mapping sync script inside the matrix-client container
# This ensures we have access to the database and all dependencies
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "🚀 Running mapping sync in matrix-client container..."
echo ""

# Run the sync script inside the container
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/scripts/admin/sync_mappings_to_db.py "$@"

echo ""
echo "✅ Sync complete!"
