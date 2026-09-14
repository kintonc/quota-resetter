#!/usr/bin/env python3
"""Automated tests for Quota Kicker and Antigravity support."""
import io
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

    def test_find_antigravity_windows_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            executable = Path(tmpdir) / "agy" / "bin" / "agy.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch("shutil.which", return_value=None), patch.dict(os.environ, {"LOCALAPPDATA": tmpdir}):
                self.assertEqual(quota_kicker.find_executable("antigravity"), str(executable))

    def test_codex_limit_query_uses_no_window(self):
        responses = (
            '{"id": 1, "result": {}}\n'
            '{"id": 2, "result": {"rateLimits": {"primary": {"windowDurationMins": 300}}}}\n'
        )
        with patch.object(quota_kicker, "find_executable", return_value="C:/fake/codex.exe"):
            with patch("subprocess.Popen") as mock_popen:
                process = mock_popen.return_value
                process.stdin = io.StringIO()
                process.stdout = io.StringIO(responses)

                result = quota_kicker.rpc_read_codex_limits()

                self.assertIn("primary", result)
                self.assertEqual(mock_popen.call_args.kwargs["creationflags"], quota_kicker.NO_WINDOW)

    def test_run_kick_antigravity(self):
        with patch.object(quota_kicker, "find_executable", return_value="/fake/agy"):
            with patch.object(quota_kicker, "log"), patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                result = quota_kicker.run_kick("antigravity", dry_run=False)
                self.assertTrue(result)
                mock_run.assert_called_once()
                args, kwargs = mock_run.call_args
                self.assertEqual(args[0], ["/fake/agy", "-p", "Reply with exactly: OK"])
                self.assertEqual(kwargs["cwd"], quota_kicker.APP_DIR)
                self.assertEqual(kwargs["creationflags"], quota_kicker.NO_WINDOW)

    def test_successful_claude_kick_schedules_next_window(self):
        reset = 1_700_000_000
        state = {"services": {"claude": {"expected_reset": reset}}}

        with patch.object(quota_kicker, "log"), patch.object(quota_kicker, "now", return_value=reset + 76):
            with patch.object(quota_kicker, "run_kick", return_value=True):
                quota_kicker.service_cycle("claude", reset, state, dry_run=False)

        claude = state["services"]["claude"]
        self.assertEqual(claude["kicked_reset"], reset)
        self.assertEqual(claude["expected_reset"], reset + 76 + 5 * 60 * 60)

    def test_successful_antigravity_kick_schedules_next_window(self):
        reset = 1_700_000_000
        state = {"services": {"antigravity": {"expected_reset": reset}}}

        with patch.object(quota_kicker, "log"), patch.object(quota_kicker, "now", return_value=reset + 76):
            with patch.object(quota_kicker, "run_kick", return_value=True):
                quota_kicker.service_cycle("antigravity", reset, state, dry_run=False)

        antigravity = state["services"]["antigravity"]
        self.assertEqual(antigravity["kicked_reset"], reset)
        self.assertEqual(antigravity["expected_reset"], reset + 76 + 5 * 60 * 60)

    def test_codex_keeps_expired_reset_during_grace_period(self):
        reset = 1_700_000_000
        next_reset = reset + 5 * 60 * 60
        state = {"services": {"codex": {"expected_reset": reset}}}

        with patch.object(quota_kicker, "log"), patch.object(quota_kicker, "now", return_value=reset + 30):
            with patch.object(quota_kicker, "run_kick") as mock_kick:
                quota_kicker.service_cycle("codex", next_reset, state, dry_run=False)

        mock_kick.assert_not_called()
        self.assertEqual(state["services"]["codex"]["expected_reset"], reset)

    def test_codex_keeps_expired_reset_after_failed_kick(self):
        reset = 1_700_000_000
        next_reset = reset + 5 * 60 * 60
        state = {"services": {"codex": {"expected_reset": reset}}}

        with patch.object(quota_kicker, "log"), patch.object(quota_kicker, "now", return_value=reset + 76):
            with patch.object(quota_kicker, "run_kick", return_value=False):
                quota_kicker.service_cycle("codex", next_reset, state, dry_run=False)

        self.assertEqual(state["services"]["codex"]["expected_reset"], reset)
        self.assertNotIn("kicked_reset", state["services"]["codex"])

    def test_codex_accepts_new_reset_after_successful_kick(self):
        reset = 1_700_000_000
        next_reset = reset + 5 * 60 * 60
        state = {"services": {"codex": {"expected_reset": reset}}}

        with patch.object(quota_kicker, "log"), patch.object(quota_kicker, "now", return_value=reset + 76):
            with patch.object(quota_kicker, "run_kick", return_value=True):
                quota_kicker.service_cycle("codex", next_reset, state, dry_run=False)

        codex = state["services"]["codex"]
        self.assertEqual(codex["kicked_reset"], reset)
        self.assertEqual(codex["expected_reset"], next_reset)

    def test_codex_ignores_one_second_reset_jitter(self):
        reset = 1_700_000_000
        state = {"services": {"codex": {"expected_reset": reset}}}

        with patch.object(quota_kicker, "log") as mock_log:
            with patch.object(quota_kicker, "now", return_value=reset - 60):
                quota_kicker.service_cycle("codex", reset + 1, state, dry_run=False)

        mock_log.assert_not_called()
        self.assertEqual(state["services"]["codex"]["expected_reset"], reset)

    def test_claude_ignores_snapshot_from_kicked_window(self):
        kicked_reset = 1_700_000_000
        expected_reset = kicked_reset + 5 * 60 * 60
        state = {
            "services": {
                "claude": {
                    "expected_reset": expected_reset,
                    "kicked_reset": kicked_reset,
                }
            }
        }

        with patch.object(quota_kicker, "log"), patch.object(quota_kicker, "now", return_value=expected_reset - 60):
            quota_kicker.service_cycle("claude", kicked_reset, state, dry_run=False)

        self.assertEqual(state["services"]["claude"]["expected_reset"], expected_reset)

    def test_existing_claude_kick_advances_stored_reset(self):
        reset = 1_700_000_000
        state = {
            "services": {
                "claude": {
                    "expected_reset": reset,
                    "kicked_reset": reset,
                }
            }
        }

        with patch.object(quota_kicker, "log"), patch.object(quota_kicker, "now", return_value=reset + 120):
            quota_kicker.service_cycle("claude", reset, state, dry_run=False)

        self.assertEqual(state["services"]["claude"]["expected_reset"], reset + 5 * 60 * 60)

    def test_existing_antigravity_kick_advances_stored_reset(self):
        reset = 1_700_000_000
        state = {
            "services": {
                "antigravity": {
                    "expected_reset": reset,
                    "kicked_reset": reset,
                }
            }
        }

        with patch.object(quota_kicker, "log"), patch.object(quota_kicker, "now", return_value=reset + 120):
            quota_kicker.service_cycle("antigravity", reset, state, dry_run=False)

        self.assertEqual(state["services"]["antigravity"]["expected_reset"], reset + 5 * 60 * 60)

    def test_cli_service_arg(self):
        with patch("sys.argv", ["quota_kicker.py", "--service", "antigravity", "--dry-run"]):
            with patch.object(quota_kicker, "load_state", return_value={"services": {}}):
                with patch.object(quota_kicker, "antigravity_reset", return_value=None):
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
