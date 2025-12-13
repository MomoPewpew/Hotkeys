#!/usr/bin/env bash
set -e

mkdir -p "$HOME/.config/systemd/user"

cat > "$HOME/.config/systemd/user/hotkeys.service" <<'EOF'
[Unit]
Description=Hotkeys macro daemon
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=/mnt/files/Workspaces/workspace-py/Hotkeys
ExecStart=/mnt/files/Workspaces/workspace-py/Hotkeys/.venv/bin/python -m hotkeys.main --config-dir configs --evdev-devices /dev/input/event6,/dev/input/event7
Restart=on-failure
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now hotkeys.service