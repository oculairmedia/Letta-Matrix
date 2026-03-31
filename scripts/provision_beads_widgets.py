#!/usr/bin/env python3
"""
Provision beads board widgets into PM agent rooms.

For each project with beads issues + a Letta agent, ensures the agent's
Matrix room has a beads-board widget pointing to the correct project.

Data sources:
  - Vibe Sync registry → project list with agent IDs and beads counts
  - Matrix API → agent_id → room_id mapping
  - Tuwunel → widget state events via Matrix client API

Idempotent: skips rooms that already have the widget.
"""

import json
import os
import sys
import urllib.parse
import urllib.request

VIBESYNC_API = os.getenv("VIBESYNC_API", "http://192.168.50.90:3110/api/registry/projects")
MATRIX_API = os.getenv("MATRIX_API", "http://192.168.50.90:8004")
HOMESERVER = os.getenv("MATRIX_HOMESERVER_URL", "http://tuwunel:6167")
WIDGET_BASE_URL = os.getenv("WIDGET_BASE_URL", "https://matrix.oculair.ca/widgets/beads-board.html")
ADMIN_TOKEN = os.getenv("MATRIX_ADMIN_TOKEN", "")
CREATOR_MXID = os.getenv("CREATOR_MXID", "@admin:matrix.oculair.ca")
DRY_RUN = "--dry-run" in sys.argv

# If no admin token, try to get one from the matrix-api login
if not ADMIN_TOKEN:
    admin_user = os.getenv("MATRIX_ADMIN_USERNAME", "admin")
    admin_pass = os.getenv("MATRIX_ADMIN_PASSWORD", "")
    if admin_pass:
        try:
            login_data = json.dumps({
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": admin_user},
                "password": admin_pass,
            }).encode()
            req = urllib.request.Request(
                f"{HOMESERVER}/_matrix/client/v3/login",
                data=login_data,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=10)
            ADMIN_TOKEN = json.loads(resp.read()).get("access_token", "")
        except Exception as e:
            print(f"Failed to get admin token: {e}")
            sys.exit(1)

if not ADMIN_TOKEN:
    print("No MATRIX_ADMIN_TOKEN set and login failed. Set env or provide credentials.")
    sys.exit(1)


def get_projects_with_beads():
    resp = urllib.request.urlopen(VIBESYNC_API, timeout=10)
    data = json.loads(resp.read())
    return [
        p for p in data.get("projects", [])
        if p.get("beads_issue_count", 0) > 0
        and p.get("letta_agent_id")
        and p.get("identifier")
    ]


def get_agent_room(agent_id):
    url = f"{MATRIX_API}/agents/{urllib.parse.quote(agent_id)}/room"
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read())
        if data.get("success") and data.get("room_id"):
            return data["room_id"]
    except Exception:
        pass
    return None


def get_existing_widgets(room_id):
    """Get existing widget state events for a room."""
    url = f"{HOMESERVER}/_matrix/client/v3/rooms/{urllib.parse.quote(room_id)}/state"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        events = json.loads(resp.read())
        return [
            e for e in events
            if e.get("type") == "im.vector.modular.widgets"
        ]
    except Exception:
        return []


def put_widget(room_id, project_identifier, project_name):
    """Add a beads-board widget to a room."""
    widget_id = f"beads-board-{project_identifier}"
    state_key = widget_id
    widget_url = f"{WIDGET_BASE_URL}?project={urllib.parse.quote(project_identifier)}"

    body = json.dumps({
        "type": "customwidget",
        "url": widget_url,
        "name": f"Beads: {project_name}",
        "id": widget_id,
        "creatorUserId": CREATOR_MXID,
    }).encode()

    url = (
        f"{HOMESERVER}/_matrix/client/v3/rooms/"
        f"{urllib.parse.quote(room_id)}/state/"
        f"im.vector.modular.widgets/{urllib.parse.quote(state_key)}"
    )
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {ADMIN_TOKEN}",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())


def main():
    projects = get_projects_with_beads()
    print(f"Found {len(projects)} projects with beads")

    provisioned = 0
    skipped = 0
    failed = 0

    for p in projects:
        identifier = p["identifier"]
        name = p.get("name", identifier)
        agent_id = p["letta_agent_id"]
        beads_count = p.get("beads_issue_count", 0)

        room_id = get_agent_room(agent_id)
        if not room_id:
            print(f"  SKIP {identifier}: no room for agent {agent_id}")
            skipped += 1
            continue

        # Check if widget already exists
        widgets = get_existing_widgets(room_id)
        existing_ids = [w.get("state_key", "") for w in widgets]
        widget_id = f"beads-board-{identifier}"

        if widget_id in existing_ids:
            print(f"  OK   {identifier}: widget already in {room_id}")
            skipped += 1
            continue

        if DRY_RUN:
            print(f"  DRY  {identifier}: would add widget to {room_id} ({beads_count} beads)")
            provisioned += 1
            continue

        try:
            result = put_widget(room_id, identifier, name)
            event_id = result.get("event_id", "?")
            print(f"  ADD  {identifier}: widget added to {room_id} ({event_id})")
            provisioned += 1
        except Exception as e:
            print(f"  FAIL {identifier}: {e}")
            failed += 1

    print(f"\nDone: {provisioned} provisioned, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
