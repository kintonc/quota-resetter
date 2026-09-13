#!/bin/sh
# Installs a per-user launchd job. Run: sh install-macos.sh /absolute/path/to/quota_kicker.py
set -eu
script=${1:?Pass the absolute path to quota_kicker.py}
python=${PYTHON:-python3}
plist="$HOME/Library/LaunchAgents/com.quota-kicker.plist"
mkdir -p "$HOME/.quota-kicker"
cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.quota-kicker</string>
  <key>ProgramArguments</key><array><string>$python</string><string>$script</string></array>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>WorkingDirectory</key><string>$HOME/.quota-kicker</string>
  <key>StandardOutPath</key><string>$HOME/.quota-kicker/launchd.out</string>
  <key>StandardErrorPath</key><string>$HOME/.quota-kicker/launchd.err</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "Installed. Check: launchctl print gui/$(id -u)/com.quota-kicker"
