#!/usr/bin/env python3
"""Automated tests for Quota Kicker and Antigravity support."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import quota_kicker


class TestAntigravityStatusLimits(unittest.TestCase):
    def test_status_limits_script(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            script = Path(__file__).parent / "antigravity_status_limits.py"
            payload = {
                "quota": {
                    "gemini-5h": {
                        "remaining_fraction": 0.85,
                        "reset_time": "2026-09-13T17:00:00Z",
                        "reset_in_seconds": 18000,
                    },
                    "gemini-weekly": {
                        "remaining_fraction": 0.95,
                        "reset_time": "2026-09-20T12:00:00Z",
                        "reset_in_seconds": 604800,
                    },
                }
            }
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["USERPROFILE"] = str(home)
            proc = subprocess.run(
                [sys.executable, str(script)],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            self.assertEqual(proc.stdout, "\n")
            target = home / ".antigravity_limits.json"
            self.assertTrue(target.exists())
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn("gemini-5h", data["quota"])
            self.assertIn("recorded_at", data)


class TestQuotaKickerAntigravity(unittest.TestCase):
    def test_parse_timestamp(self):
        self.assertEqual(quota_kicker.parse_timestamp(1750000000), 1750000000)
        self.assertEqual(quota_kicker.parse_timestamp(1750000000.0), 1750000000)
        self.assertEqual(quota_kicker.parse_timestamp("1750000000"), 1750000000)
        iso_ts = quota_kicker.parse_timestamp("2026-09-13T17:00:00Z")
        self.assertIsInstance(iso_ts, int)
        self.assertGreater(iso_ts, 1700000000)
        self.assertIsNone(quota_kicker.parse_timestamp("invalid-date"))

    def test_antigravity_reset_explicit_5h_iso(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            limits_file = Path(tmpdir) / ".antigravity_limits.json"
            data = {
                "recorded_at": 1789400000,
                "quota": {
                    "gemini-5h": {
                        "reset_time": "2026-09-13T17:00:00Z",
                        "reset_in_seconds": 18000,
                    }
                }
            }
            limits_file.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(quota_kicker, "ANTIGRAVITY_LIMITS", limits_file):
                reset_val = quota_kicker.antigravity_reset()
                expected = quota_kicker.parse_timestamp("2026-09-13T17:00:00Z")
                self.assertEqual(reset_val, expected)

    def test_antigravity_reset_seconds_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            limits_file = Path(tmpdir) / ".antigravity_limits.json"
            data = {
                "recorded_at": 1700000000,
                "quota": {
                    "gemini-5h": {
                        "reset_in_seconds": 14400,
                    }
                }
            }
            limits_file.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(quota_kicker, "ANTIGRAVITY_LIMITS", limits_file):
                reset_val = quota_kicker.antigravity_reset()
                self.assertEqual(reset_val, 1700000000 + 14400)

    def test_antigravity_reset_ignores_weekly_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            limits_file = Path(tmpdir) / ".antigravity_limits.json"
            data = {
                "recorded_at": 1700000000,
                "quota": {
                    "gemini-weekly": {
                        "reset_time": "2026-09-20T12:00:00Z",
                        "reset_in_seconds": 604800,
                    }
                }
            }
            limits_file.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(quota_kicker, "ANTIGRAVITY_LIMITS", limits_file):
                self.assertIsNone(quota_kicker.antigravity_reset())

    def test_antigravity_reset_generic_bucket_within_5h(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            limits_file = Path(tmpdir) / ".antigravity_limits.json"
            data = {
                "recorded_at": 1700000000,
                "quota": {
                    "gemini-flash": {
                        "reset_in_seconds": 10800,
                    }
                }
            }
            limits_file.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(quota_kicker, "ANTIGRAVITY_LIMITS", limits_file):
                self.assertEqual(quota_kicker.antigravity_reset(), 1700010800)

    def test_find_executable_candidates(self):
        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/local/bin/agy" if cmd == "agy" else None
            self.assertEqual(quota_kicker.find_executable("antigravity"), "/usr/local/bin/agy")

    def test_run_kick_antigravity(self):
        with patch.object(quota_kicker, "find_executable", return_value="/fake/agy"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                result = quota_kicker.run_kick("antigravity", dry_run=False)
                self.assertTrue(result)
                mock_run.assert_called_once()
                args, kwargs = mock_run.call_args
                self.assertEqual(args[0], ["/fake/agy", "-p", "Reply with exactly: OK"])
                self.assertEqual(kwargs["cwd"], quota_kicker.APP_DIR)

    def test_cli_service_arg(self):
        with patch("sys.argv", ["quota_kicker.py", "--service", "antigravity", "--dry-run"]):
            with patch.object(quota_kicker, "antigravity_reset", return_value=None):
                with patch.object(quota_kicker, "save_state"):
                    exit_code = quota_kicker.main()
                    self.assertEqual(exit_code, 0)

    def test_format_status_human_and_raw(self):
        state = {
            "services": {
                "antigravity": {
                    "expected_reset": 1789337551,
                }
            }
        }
        human = quota_kicker.format_status(state, raw=False)
        self.assertIn("expected_reset", human["services"]["antigravity"])
        self.assertIn("expected_reset_unix", human["services"]["antigravity"])
        self.assertEqual(human["services"]["antigravity"]["expected_reset_unix"], 1789337551)
        self.assertIsInstance(human["services"]["antigravity"]["expected_reset"], str)
        self.assertIn("time_remaining", human["services"]["antigravity"])

        raw = quota_kicker.format_status(state, raw=True)
        self.assertEqual(raw, state)


if __name__ == "__main__":
    unittest.main()
