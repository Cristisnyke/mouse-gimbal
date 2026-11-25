# mouse-gimbal

A software camera gimbal for PC games: turns shaky hand mouse movement into smooth, cinematic camera motion for capturing footage. Linux (KDE Plasma, Wayland or X11).

## How it works

```
real mouse ──evdev (grabbed)──► tremor filter ──► gimbal spring ──► uinput virtual mouse @1000 Hz ──► game
```

1. **Grab** the real mouse via evdev so the game only sees the smoothed output. Works with any
   game that reads relative mouse input (native, Proton, Wine). No game hooks.
2. **Your hand moves a target**; a simulated gimbal head (critically damped spring) follows it,
   so every start and stop eases in and out. An optional One Euro filter removes hand tremor first.
3. **Emit** at a fixed 1000 Hz with a sub-count accumulator: slow pans don't stutter, and the
   camera always ends exactly where your hand put it (no drift, nothing lost).

Buttons and wheel pass straight through. When the gimbal is off the mouse isn't grabbed at all
(zero added latency). If the app dies the kernel releases the grab, so the mouse can't get stuck.

## Install

```sh
git clone https://github.com/Cristisnyke/mouse-gimbal && cd mouse-gimbal
./install.sh          # links ~/.local/bin/mouse-gimbal, adds a udev rule (asks for root once)
mouse-gimbal --list   # check which mouse it will use
mouse-gimbal          # tray app
```

Needs: `python-evdev`, `python-dbus`, `python-gobject`, `libayatana-appindicator`, `paplay`.
The udev rule gives the logged-in user access to mouse event nodes (`uaccess`), like game
controllers get.

## Use

| Hotkey | Action |
|--------|--------|
| `Ctrl+Alt+G` | Gimbal on / off (rising / falling beep) |
| `Ctrl+Alt+N` | Next preset |
| `Ctrl+Alt+H` | Axis lock: off → horizon (no vertical) → vertical |
| `Ctrl+Alt+C` | Cruise: keep panning at the current speed, hands off. Again to stop |

Presets (edit `~/.config/mouse-gimbal/config.toml`, restart to apply):

| Preset | Feel |
|--------|------|
| Light | 60 ms lag, tremor removed: still playable |
| Cinematic | 250 ms ease, the default |
| Heavy | 600 ms, big floaty camera |
| Tripod pan | 800 ms, horizon locked, speed capped |

## Footage tips

- Lower in-game sensitivity: more counts per degree = finer motion and more room for the filter.
- Turn off in-game mouse acceleration and smoothing; let the gimbal do all of it.
- Lock the game to a stable frame rate while recording, so per-frame camera steps are even.

## Develop

```sh
python3 -m unittest discover -s tests -t .   # math tests
python3 tests/e2e_uinput.py                  # real uinput round trip
```

## Roadmap

- Record / replay: record a move, smooth it offline (spline), replay it identically for retakes
- Cruise speed adjust with the wheel
- Max acceleration limit
