#!/usr/bin/env python3
"""Antigravity CLI status-line hook: cache the latest quota snapshot."""
import json
import sys
import time
from pathlib import Path

try:
    payload = json.load(sys.stdin)
    quota = payload.get("quota")
    if isinstance(quota, dict):
        target = Path.home() / ".antigravity_limits.json"
        snapshot = {
            "quota": quota,
            "recorded_at": int(time.time()),
        }
        target.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
except (json.JSONDecodeError, OSError):
    pass

print("")
