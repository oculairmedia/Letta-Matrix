#!/usr/bin/env python3
"""Tiny HTTP API that serves beads data as JSON for widgets.
Runs on port 9099, proxied through nginx at /widgets/api/beads.
"""
import json
import subprocess
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

BEADS_DIR = Path("/app/repo")
CACHE_TTL = 30  # seconds
_cache = {"data": None, "ts": 0}


def get_beads_data():
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]

    try:
        result = subprocess.run(
            ["bd", "list", "--json"],
            cwd=str(BEADS_DIR),
            capture_output=True, text=True, timeout=10
        )
        issues = json.loads(result.stdout) if result.returncode == 0 else []
    except Exception:
        issues = []

    by_status = {}
    by_priority = {}
    for i in issues:
        s = i.get("status", "unknown")
        p = f"P{i.get('priority', '?')}"
        by_status[s] = by_status.get(s, 0) + 1
        by_priority[p] = by_priority.get(p, 0) + 1

    open_issues = [i for i in issues if i.get("status") in ("open", "in_progress", "blocked")]
    open_issues.sort(key=lambda x: (x.get("priority", 9), x.get("id", "")))

    data = {
        "summary": {
            "total": len(issues),
            "by_status": by_status,
            "by_priority": by_priority,
        },
        "issues": [
            {
                "id": i["id"],
                "title": i.get("title", ""),
                "status": i.get("status", "unknown"),
                "priority": i.get("priority", 9),
                "labels": i.get("labels", []),
                "created_at": i.get("created_at", ""),
                "updated_at": i.get("updated_at", ""),
            }
            for i in open_issues
        ],
        "ts": int(now),
    }
    _cache["data"] = data
    _cache["ts"] = now
    return data


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/beads"):
            data = get_beads_data()
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "max-age=10")
            self.end_headers()
            self.write(body) if hasattr(self, 'write') else self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # silent


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9099), Handler)
    print("Beads widget API listening on :9099")
    server.serve_forever()
