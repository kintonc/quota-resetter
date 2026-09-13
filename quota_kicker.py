#!/usr/bin/env python3
"""Start a new Codex/Claude/Antigravity CLI session shortly after an observed 5-hour reset.

Run this program every minute or two using launchd (macOS) or Task Scheduler
(Windows). It has no third-party dependencies and works with Python 3.9+.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

APP_DIR = Path(os.environ.get("QUOTA_KICKER_HOME", Path.home() / ".quota-kicker"))
STATE = APP_DIR / "state.json"
LOG = APP_DIR / "quota-kicker.log"
CLAUDE_LIMITS = Path.home() / ".claude_limits.json"
ANTIGRAVITY_LIMITS = Path.home() / ".antigravity_limits.json"
PROMPT = "Reply with exactly: OK"
GRACE_SECONDS = 75


def now() -> int:
    return int(time.time())


def iso(value: Optional[int]) -> str:
    return datetime.fromtimestamp(value or now(), timezone.utc).astimezone().isoformat(timespec="seconds")


def format_timestamp_human(ts: Optional[int]) -> Optional[str]:
    if not isinstance(ts, (int, float)):
        return None
    dt = datetime.fromtimestamp(ts, timezone.utc).astimezone()
    offset = dt.strftime("%z")
    offset_fmt = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
    tz_name = dt.tzname() or ""
    time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    return f"{time_str} {tz_name} ({offset_fmt})".strip()


def relative_time(ts: Optional[int]) -> Optional[str]:
    if not isinstance(ts, (int, float)):
        return None
    diff = int(ts - now())
    if diff > 0:
        mins, secs = divmod(diff, 60)
        hours, mins = divmod(mins, 60)
        if hours:
            return f"in {hours}h {mins}m"
        return f"in {mins}m {secs}s"
    diff_past = abs(diff)
    mins, secs = divmod(diff_past, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h {mins}m ago"
    return f"{mins}m {secs}s ago"


def format_status(state: dict[str, Any], raw: bool = False) -> dict[str, Any]:
    if raw:
        return state
    formatted = json.loads(json.dumps(state))
    services = formatted.get("services")
    if isinstance(services, dict):
        for service, s_state in services.items():
            if isinstance(s_state, dict):
                for key in ("expected_reset", "kicked_reset"):
                    val = s_state.get(key)
                    if isinstance(val, (int, float)):
                        s_state[f"{key}_unix"] = int(val)
                        s_state[key] = format_timestamp_human(int(val))
                        if key == "expected_reset":
                            s_state["time_remaining"] = relative_time(int(val))
    return formatted


def log(message: str) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{iso(None)}  {message}"
    print(line)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state() -> dict[str, Any]:
    if not STATE.exists():
        return {"services": {}}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log("state file was unreadable; starting with empty state")
        return {"services": {}}


def save_state(state: dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(STATE)


def find_executable(service: str) -> Optional[str]:
    if service == "antigravity":
        candidates = ["agy", "antigravity"]
    else:
        candidates = [service]
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
        local_fallback = Path.home() / ".local" / "bin" / name
        if local_fallback.is_file() and os.access(local_fallback, os.X_OK):
            return str(local_fallback)
    return None


def parse_timestamp(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            clean = value.replace("Z", "+00:00")
            return int(datetime.fromisoformat(clean).timestamp())
        except (ValueError, TypeError):
            pass
        try:
            return int(float(value))
        except (ValueError, TypeError):
            pass
    return None


def rpc_read_codex_limits() -> Optional[dict[str, Any]]:
    """Return Codex rate-limit snapshot, or None if this CLI/account does not expose it."""
    executable = find_executable("codex")
    if not executable:
        log("CODEX unavailable: executable not found")
        return None
    try:
        proc = subprocess.Popen(
            [executable, "app-server", "--stdio"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8",
        )
        assert proc.stdin and proc.stdout
        # JSON-RPC initialization required by the Codex app-server protocol.
        initialize = {
            "id": 1, "method": "initialize",
            "params": {"clientInfo": {"name": "quota-kicker", "version": "1.0"}, "capabilities": {}},
        }
        proc.stdin.write(json.dumps(initialize) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + 12
        initialized = False
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            response = json.loads(line)
            if response.get("id") == 1:
                initialized = "result" in response
                break
        if not initialized:
            return None
        proc.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n")
        proc.stdin.write(json.dumps({"id": 2, "method": "account/rateLimits/read", "params": {}}) + "\n")
        proc.stdin.flush()
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            response = json.loads(line)
            if response.get("id") == 2:
                return response.get("result", {}).get("rateLimits")
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        log(f"CODEX limit read failed: {exc}")
    finally:
        try:
            proc.terminate()  # type: ignore[has-type]
            proc.wait(timeout=2)  # type: ignore[has-type]
        except (UnboundLocalError, OSError, subprocess.SubprocessError):
            pass
    return None


def five_hour_reset(limits: Any) -> Optional[int]:
    if not isinstance(limits, dict):
        return None
    # Buckets may be called primary/secondary or have other names. Use duration.
    for bucket in limits.values():
        if isinstance(bucket, dict) and bucket.get("windowDurationMins") == 300:
            value = bucket.get("resetsAt")
            if isinstance(value, (int, float)):
                return int(value)
    return None


def claude_reset() -> Optional[int]:
    try:
        limits = json.loads(CLAUDE_LIMITS.read_text(encoding="utf-8"))
        five_hour = limits.get("five_hour", {})
        value = five_hour.get("resets_at")
        return int(value) if isinstance(value, (int, float)) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def antigravity_reset() -> Optional[int]:
    if not ANTIGRAVITY_LIMITS.exists():
        return None
    try:
        data = json.loads(ANTIGRAVITY_LIMITS.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    recorded_at = data.get("recorded_at")
    if not isinstance(recorded_at, (int, float)):
        try:
            recorded_at = int(ANTIGRAVITY_LIMITS.stat().st_mtime)
        except OSError:
            recorded_at = now()

    # Look for 5-hour bucket in data["five_hour"], data["quota"], or data directly
    quota: dict[str, Any] = {}
    if isinstance(data.get("quota"), dict):
        quota = data["quota"]
    elif isinstance(data.get("five_hour"), dict):
        quota = {"five_hour": data["five_hour"]}
    else:
        quota = data

    # 1. Primary check: explicit 5-hour bucket
    for key, bucket in quota.items():
        if not isinstance(bucket, dict):
            continue
        key_lower = str(key).lower()
        if "5h" in key_lower or "five" in key_lower or bucket.get("windowDurationMins") == 300:
            ts = parse_timestamp(bucket.get("reset_time") or bucket.get("resets_at") or bucket.get("resetsAt"))
            if ts:
                return ts
            secs = bucket.get("reset_in_seconds")
            if isinstance(secs, (int, float)):
                return int(recorded_at + secs)

    # 2. Secondary check: any bucket with reset <= 5 hours away, ignoring weekly/monthly
    for key, bucket in quota.items():
        if not isinstance(bucket, dict):
            continue
        key_lower = str(key).lower()
        if "week" in key_lower or "month" in key_lower:
            continue
        secs = bucket.get("reset_in_seconds")
        if isinstance(secs, (int, float)) and 0 < secs <= 18300:
            return int(recorded_at + secs)
        ts = parse_timestamp(bucket.get("reset_time") or bucket.get("resets_at") or bucket.get("resetsAt"))
        if ts and 0 < (ts - recorded_at) <= 18300:
            return ts

    return None


def run_kick(service: str, dry_run: bool) -> bool:
    executable = find_executable(service)
    if not executable:
        log(f"{service.upper()} unavailable: executable not found")
        return False
    if service == "codex":
        command = [executable, "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-rules", "--ignore-user-config", "-C", str(APP_DIR), PROMPT]
    elif service == "antigravity":
        command = [executable, "-p", PROMPT]
    else:
        command = [executable, "--print", "--no-session-persistence", PROMPT]
    if dry_run:
        log(f"DRY RUN: would kick {service.upper()}")
        return True
    try:
        result = subprocess.run(command, cwd=APP_DIR, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=120, check=False)
        if result.returncode == 0:
            log(f"{service.upper()} kick succeeded")
            return True
        log(f"{service.upper()} kick failed (exit {result.returncode})")
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"{service.upper()} kick failed: {exc}")
    return False


def service_cycle(service: str, observed_reset: Optional[int], state: dict[str, Any], dry_run: bool) -> None:
    service_state = state.setdefault("services", {}).setdefault(service, {})
    expected = service_state.get("expected_reset")
    if not isinstance(expected, int) and observed_reset:
        service_state["expected_reset"] = observed_reset
        log(f"{service.upper()} observing next reset: {iso(observed_reset)}")
        return
    # Act on the stored timestamp before accepting a newer backend timestamp.
    if isinstance(expected, int) and now() >= expected + GRACE_SECONDS and service_state.get("kicked_reset") != expected:
        if run_kick(service, dry_run):
            service_state["kicked_reset"] = expected
    if observed_reset and observed_reset != expected:
        service_state["expected_reset"] = observed_reset
        log(f"{service.upper()} next reset: {iso(observed_reset)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="log due actions without running either CLI")
    parser.add_argument("--status", action="store_true", help="show state and exit")
    parser.add_argument("--raw", action="store_true", help="show raw unix timestamps in status output")
    parser.add_argument("--service", choices=("all", "codex", "claude", "antigravity"), default="all")
    args = parser.parse_args()
    state = load_state()
    if args.status:
        print(json.dumps(format_status(state, raw=args.raw), indent=2))
        return 0
    if args.service in ("all", "codex"):
        service_cycle("codex", five_hour_reset(rpc_read_codex_limits()), state, args.dry_run)
    if args.service in ("all", "claude"):
        service_cycle("claude", claude_reset(), state, args.dry_run)
    if args.service in ("all", "antigravity"):
        service_cycle("antigravity", antigravity_reset(), state, args.dry_run)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
