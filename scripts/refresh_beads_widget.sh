#!/bin/bash
# Refresh beads-data.json for widget consumption — multi-project
# Run via cron every 60s
# Uses flock to prevent concurrent runs

WIDGET_DIR="/opt/stacks/matrix-tuwunel-deploy/widgets"
LOCKFILE="/tmp/beads-widget-refresh.lock"

# Prevent concurrent runs
exec 200>"$LOCKFILE"
flock -n 200 || exit 0

python3 - "$WIDGET_DIR" <<'PYEOF'
import json, time, sys, os, urllib.request, urllib.parse

WIDGET_DIR = sys.argv[1]
VIBESYNC_API = "http://192.168.50.90:3110/api/registry/projects"

all_data = {"projects": {}, "updated_at": int(time.time())}

# Discover projects from Vibe Sync registry API
try:
    resp = urllib.request.urlopen(VIBESYNC_API, timeout=5)
    registry = json.loads(resp.read())
except Exception as e:
    print(f"Failed to reach registry: {e}")
    sys.exit(0)

projects_with_beads = [
    p for p in registry.get("projects", [])
    if p.get("beads_issue_count", 0) > 0 and p.get("identifier")
]

for p in projects_with_beads:
    name = p.get("name") or p.get("identifier", "unknown")
    identifier = p.get("identifier", "")

    # Fetch issues from Vibe Sync API (reads Dolt directly — no bd spawn)
    try:
        url = f"{VIBESYNC_API}/{urllib.parse.quote(identifier, safe='')}/issues"
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read())
        issues = data.get("issues", [])
    except Exception:
        issues = []

    if not issues:
        continue

    by_status = {}
    by_priority = {}
    for i in issues:
        s = i.get("status", "unknown")
        pr = f"P{i.get('priority', '?')}"
        by_status[s] = by_status.get(s, 0) + 1
        by_priority[pr] = by_priority.get(pr, 0) + 1

    active = [i for i in issues if i.get("status") in ("open", "in_progress", "blocked")]
    active.sort(key=lambda x: (x.get("priority", 9), x.get("id", "")))

    all_data["projects"][identifier] = {
        "name": name,
        "summary": {"total": len(issues), "by_status": by_status, "by_priority": by_priority},
        "issues": [{
            "id": i["id"], "title": i.get("title", ""), "status": i.get("status", ""),
            "priority": i.get("priority", 9), "labels": i.get("labels", []),
        } for i in active],
    }

# Only write if we got data — never overwrite good data with empty
out_path = f"{WIDGET_DIR}/beads-data.json"
if all_data["projects"]:
    content = json.dumps(all_data)
    # Write to /tmp first, then move (atomic on same filesystem won't work across mounts)
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp_path, 0o644)
    os.rename(tmp_path, out_path)

    total_active = sum(len(p["issues"]) for p in all_data["projects"].values())
    print(f"{len(all_data['projects'])} projects, {total_active} active issues")
else:
    print("No data collected, keeping existing file")
PYEOF
