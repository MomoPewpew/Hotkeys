# Hotkeys (Linux macro daemon)

Python daemon for window-aware gaming/utility macros on Linux Mint (X11). It watches keyboard/mouse input with `pynput`, chooses macros based on the focused window (via `xprop`/`wmctrl`), and simulates input through `pynput` controllers.

## Features
- YAML profiles per game/app (`configs/*.yml`).
- Focus-aware: activates the profile whose window class/title matches the focused window.
- Logs: focused window name every 5s and every input event.
- Macro triggers: key press based.
- Actions: key taps, mouse clicks, delays; supports `once`, `toggle_loop`, and `hold_loop`.
- In-game macro subprofiles: `macros_warrior`, `macros_wizard`, etc. Cycle with a hotkey and persist selection in YAML.

## Prereqs
- X11 session (not Wayland).
- System packages: `wmctrl`, `x11-utils` (for `xprop`), and optionally `xdotool` if you want to extend it. If you need to remap DPI/extra buttons on Logitech mice: `ratbagd` + `piper`.
  ```bash
  sudo apt update
  sudo apt install wmctrl x11-utils ratbagd piper
  ```
- Python 3.10+.

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running
```bash
python3 -m hotkeys.main --config-dir configs \
  --evdev-devices /dev/input/event6,/dev/input/event7
```

You'll see log lines for the active window every 5 seconds and every input you perform. When a profile is active and a trigger key is pressed, its actions run according to the behavior (`once`, `toggle_loop`, `hold_loop`).

## Config format (example)
See `configs/sample_game.yml`:
```yaml
name: Example Game
match:
  wm_class_contains: ["steam_app"]
  title_contains: ["Example Game"]
macro_profile_cycle_hotkey: alt+num_pad0
selected_macro_profile: warrior
macro_profile_order: [warrior, wizard]
macros:
  - name: rapid_fire
    trigger:
      key: f8
      behavior: toggle_loop   # once | toggle_loop | hold_loop
    actions:
      - type: tap_mouse
        button: left          # left | right | middle
      - type: delay
        ms: 120
  - name: cycle_example
    trigger: { key: button13, behavior: cycle }
    actions_cycle:
      - [{ type: mouse_down, button: left }]
      - [{ type: mouse_up, button: left }]
macros_warrior:
  - name: warrior_shout
    trigger: { key: button12, behavior: once }
    actions:
      - { type: tap_key, key: g }
macros_wizard:
  - name: wizard_blink
    trigger: { key: button12, behavior: once }
    actions:
      - { type: tap_key, key: r }
```

### Supported action types
- `tap_key`: `{type: tap_key, key: "g"}` or special names like `ctrl`, `shift`, `alt`, `space`, `enter`, `esc`, `f1`..`f12`, `left`, `right`, `up`, `down`.
- `key_down` / `key_up`: press or release without auto-releasing (useful for custom down/up timing).
- `tap_mouse`: `{type: tap_mouse, button: left}`.
- `mouse_down` / `mouse_up`: press or release a mouse button separately.
- `delay`: `{type: delay, ms: 150}`.
- Scroll triggers: use `key` values `scroll_up`, `scroll_down`, `scroll_left`, `scroll_right` (pynput scroll events).

### Behaviors
- `once`: run actions once on trigger press.
- `toggle_loop`: press to start looping actions until pressed again.
- `hold_loop`: loops while the trigger key is held (starts on press, stops on release).
- `cycle`: each press advances to the next action group (configure with `actions_cycle: [[...],[...]]` or numbered keys `actions-1`, `actions-2`, ...).
- Alt-tab / unfocus immediately aborts running macros (including mid-delay); held keys/buttons are released.

### Macro subprofiles (within a game)
- **Shared macros** go under `macros`.
- **Subprofile macros** go under `macros_<name>` (e.g., `macros_warrior`, `macros_wizard`).
- **Cycle hotkey**: set `macro_profile_cycle_hotkey` (supports combos like `alt+num_pad0`).
- **Order**: optional `macro_profile_order: [warrior, wizard]`.
- **Persistence**: `selected_macro_profile` is updated automatically when you cycle (written back into the same `.yml`).

## Extending
- Add more action types (text input, scroll, key down/up) in `hotkeys/macro_engine.py`.
- Add configuration validation or live reload if needed.

G502 / extra mouse buttons
- After remapping in `ratbagctl`/`piper`, run with `--evdev-devices /dev/input/event6,/dev/input/event7`.
- Pynput names extra buttons like `button12`, `button13`; evdev may report `btn_task` or `code_280`/`code_281` (our DPI toggle showed `code_281`).
- Wheel tilt maps to scroll triggers: `scroll_left` / `scroll_right`.
- Profile button on some G502 units stays invisible; may require G Hub onboard remap.

Ratbag quick commands (profile 0)
```bash
ratbagctl "Logitech Gaming Mouse G502" profile active get
ratbagctl "Logitech Gaming Mouse G502" profile 0 button 6 action set button 8   # dpi-down -> extra
ratbagctl "Logitech Gaming Mouse G502" profile 0 button 7 action set button 9   # dpi-up   -> extra
ratbagctl "Logitech Gaming Mouse G502" profile 0 button 8 action set key KEY_F13 # example key remap
sudo systemctl restart ratbagd
sudo evtest /dev/input/event6   # verify events
```

Autostart (user systemd, hidden)
```bash
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
```
Or run the bundled helper (one-time; persists across reboots once enabled):
```bash
bash /mnt/files/Workspaces/workspace-py/Hotkeys/autostart.sh
# optional: for boot without active login
loginctl enable-linger marijn
# To stop/disable later
systemctl --user disable --now hotkeys.service
```


