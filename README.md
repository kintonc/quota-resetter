# Quota Kicker

This tiny, dependency-free utility starts a new, disposable Codex, Claude Code, or Google Antigravity (`agy`) CLI session just after a 5-hour reset it has already observed. It is designed for macOS and Windows.

It does **not** scrape a browser, store credentials, or fabricate reset times. Codex is queried through its local app-server protocol; Claude and Antigravity are read from local caches populated by their respective status-line helpers. The script only fires once per timestamp and uses a 75-second buffer.

## Before scheduling

1. Ensure `python3`/`python` and the CLIs are on the scheduler's PATH.
2. Run this once while you still have a Codex five-hour window, to record its next reset:

   ```sh
   python3 quota_kicker.py --service codex
   ```

3. For Claude, configure Claude Code's status line to run `claude_status_limits.py`. After Claude receives a response, it writes `~/.claude_limits.json`. Then run:

   ```sh
   python3 quota_kicker.py --service claude
   ```

4. For Antigravity, configure Antigravity CLI's status line to run `antigravity_status_limits.py`.
   In an active `agy` session, run:

   ```sh
   /statusline /absolute/path/to/antigravity_status_limits.py
   ```

   Or add the following to `~/.gemini/antigravity-cli/settings.json`:

   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "/absolute/path/to/antigravity_status_limits.py"
     }
   }
   ```

   After an interaction, it writes `~/.antigravity_limits.json`. Then run:

   ```sh
   python3 quota_kicker.py --service antigravity
   ```

5. Verify safely with `python3 quota_kicker.py --dry-run --status`.

If a limit bucket is not exposed by your installed CLI/account, the script logs that it cannot observe the reset and takes no action.

## Install the scheduler

On macOS:

```sh
sh install-macos.sh "$(pwd)/quota_kicker.py"
```

On Windows PowerShell (run from this folder):

```powershell
.\install-windows.ps1 -ScriptPath (Resolve-Path .\quota_kicker.py)
```

The computer must be running for the exact minute. Windows' `StartWhenAvailable` setting runs a missed task when it next wakes; the script will kick only if it had already recorded that reset timestamp.

## Operational notes

- State and logs live in `~/.quota-kicker/` (`state.json` and `quota-kicker.log`).
  - View recent logs on Windows: `Get-Content ~\.quota-kicker\quota-kicker.log -Tail 20`
  - View recent logs on macOS: `tail -n 20 ~/.quota-kicker/quota-kicker.log`
- Codex runs with `--ephemeral`, outside repositories, and ignores user/project instructions. Claude runs with `--no-session-persistence`. Antigravity runs non-interactively in headless mode with `-p` from `~/.quota-kicker`.
- To remove it: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.quota-kicker.plist` on macOS; `Unregister-ScheduledTask -TaskName "Quota Kicker" -Confirm:$false` in Windows PowerShell.
- The Codex app-server API is experimental and can change with CLI releases. Run `python3 quota_kicker.py --service codex` after updating Codex to confirm it still observes a timestamp.
