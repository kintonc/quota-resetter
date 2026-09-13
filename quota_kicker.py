#!/usr/bin/env python3
"""Start a new Codex/Claude CLI session shortly after an observed 5-hour reset.

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
PROMPT = "Reply with exactly: OK"
GRACE_SECONDS = 75


def now() -> int:
    return int(time.time())


def iso(value: Optional[int]) -> str:
    return datetime.fromtimestamp(value or now(), timezone.utc).astimezone().isoformat(timespec="seconds")


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


def rpc_read_codex_limits() -> Optional[dict[str, Any]]:
    """Return Codex rate-limit snapshot, or None if this CLI/account does not expose it."""
    executable = shutil.which("codex")
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


def run_kick(service: str, dry_run: bool) -> bool:
    executable = shutil.which(service)
    if not executable:
        log(f"{service.upper()} unavailable: executable not found")
        return False
    if service == "codex":
        command = [executable, "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-rules", "--ignore-user-config", "-C", str(APP_DIR), PROMPT]
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
    parser.add_argument("--service", choices=("all", "codex", "claude"), default="all")
    args = parser.parse_args()
    state = load_state()
    if args.status:
        print(json.dumps(state, indent=2))
        return 0
    if args.service in ("all", "codex"):
        service_cycle("codex", five_hour_reset(rpc_read_codex_limits()), state, args.dry_run)
    if args.service in ("all", "claude"):
        service_cycle("claude", claude_reset(), state, args.dry_run)
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
