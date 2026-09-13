#!/usr/bin/env python3
"""Claude Code status-line hook: cache the latest rate-limit snapshot."""
import json
import sys
from pathlib import Path

try:
    payload = json.load(sys.stdin)
    limits = payload.get("rate_limits")
    if isinstance(limits, dict):
        target = Path.home() / ".claude_limits.json"
        target.write_text(json.dumps(limits), encoding="utf-8")
except (json.JSONDecodeError, OSError):
    pass

print("")
