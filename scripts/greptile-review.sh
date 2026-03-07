#!/bin/bash
set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
  printf 'gh CLI is required\n' >&2
  exit 1
fi

PR_NUMBER="${1:-}"
WATCH_MODE="${2:---watch}"

if [ -z "$PR_NUMBER" ]; then
  PR_NUMBER="$(gh pr view --json number -q .number 2>/dev/null || true)"
fi

if [ -z "$PR_NUMBER" ]; then
  printf 'No PR detected for current branch. Pass PR number: ./scripts/greptile-review.sh <pr-number>\n' >&2
  exit 1
fi

gh pr comment "$PR_NUMBER" --body "@greptile please re-review the latest changes."

if [ "$WATCH_MODE" = "--watch" ]; then
  gh pr checks "$PR_NUMBER" --watch || true
fi

REPO_JSON="$(gh repo view --json owner,name)"
OWNER="$(printf '%s' "$REPO_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["owner"]["login"])')"
REPO="$(printf '%s' "$REPO_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["name"])')"

REVIEWS_JSON="$(gh api "repos/${OWNER}/${REPO}/pulls/${PR_NUMBER}/reviews")"

printf '%s' "$REVIEWS_JSON" | python3 -c '
import json, re, sys
reviews = json.load(sys.stdin)
bots = {"greptile-apps[bot]", "greptile-apps-staging[bot]", "greptile[bot]"}
greptile = [r for r in reviews if (r.get("user") or {}).get("login") in bots]
if not greptile:
    print("No Greptile review found yet on this PR.")
    raise SystemExit(0)
latest = greptile[-1]
body = latest.get("body") or ""
match = re.search(r"(\d+/5)", body)
score = match.group(1) if match else "unknown"
print(f"Latest Greptile review id={latest.get('id')} state={latest.get('state')} score={score}")
'

printf 'Greptile retrigger flow complete for PR #%s\n' "$PR_NUMBER"
