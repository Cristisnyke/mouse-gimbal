"""User config: ~/.config/mouse-gimbal/config.toml (written with defaults on first run)."""
import os
import tomllib

from .filters import LOCKS, Preset

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "mouse-gimbal")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")

DEFAULT_TOML = """\
# mouse-gimbal config. Restart the app after editing.

# Part of the mouse name to use (see: mouse-gimbal --list). Empty = auto.
device = ""

# Output updates per second sent to the game.
rate = 1000

# Preset selected at start (by name).
start_preset = "Cinematic"

# Global hotkeys (KDE). Modifiers: Ctrl, Alt, Shift, Meta. Keys: A-Z, 0-9, F1-F24,
# Pause, ScrollLock, Insert, Home, End, PageUp, PageDown.
[hotkeys]
toggle = "Ctrl+Alt+G"   # gimbal on/off
preset = "Ctrl+Alt+N"   # next preset
lock   = "Ctrl+Alt+H"   # axis lock: off -> horizon -> vertical
cruise = "Ctrl+Alt+C"   # keep panning at the current speed, hands off

# smooth_time: seconds the camera lags behind your hand (spring). 0 = none.
# max_speed:   camera speed cap in mouse counts per second. 0 = unlimited.
# stabilize:   tremor filter (One Euro). min_cutoff lower = steadier slow moves,
#              beta higher = less lag on fast moves.
# lock:        "off", "horizon" (no vertical motion) or "vertical".

[[preset]]
name = "Light"
smooth_time = 0.06
stabilize = true
min_cutoff = 2.0
beta = 0.004

[[preset]]
name = "Cinematic"
smooth_time = 0.25
stabilize = true
min_cutoff = 1.0
beta = 0.002

[[preset]]
name = "Heavy"
smooth_time = 0.6
stabilize = true
min_cutoff = 0.5
beta = 0.001

[[preset]]
name = "Tripod pan"
smooth_time = 0.8
max_speed = 3000
stabilize = true
min_cutoff = 0.5
beta = 0.001
lock = "horizon"
"""

PRESET_KEYS = {"name", "smooth_time", "max_speed", "stabilize", "min_cutoff", "beta", "lock"}


def load(path=CONFIG_PATH):
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(DEFAULT_TOML)
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return parse(raw)


def parse(raw):
    presets = []
    for p in raw.get("preset", []):
        unknown = set(p) - PRESET_KEYS
        if unknown:
            raise ValueError(f"preset {p.get('name')!r}: unknown keys {sorted(unknown)}")
        preset = Preset(**p)
        if preset.lock not in LOCKS:
            raise ValueError(f"preset {preset.name!r}: lock must be one of {LOCKS}")
        presets.append(preset)
    if not presets:
        presets = [Preset("Cinematic")]
    hotkeys = {"toggle": "Ctrl+Alt+G", "preset": "Ctrl+Alt+N", "lock": "Ctrl+Alt+H", "cruise": "Ctrl+Alt+C"}
    hotkeys.update(raw.get("hotkeys", {}))
    names = [p.name for p in presets]
    start = raw.get("start_preset", names[0])
    return {
        "device": raw.get("device", ""),
        "rate": int(raw.get("rate", 1000)),
        "presets": presets,
        "start_index": names.index(start) if start in names else 0,
        "hotkeys": hotkeys,
    }


QT_MODS = {"shift": 0x02000000, "ctrl": 0x04000000, "control": 0x04000000, "alt": 0x08000000, "meta": 0x10000000}
QT_SPECIAL = {
    "pause": 0x01000008, "scrolllock": 0x01000026, "insert": 0x01000006, "home": 0x01000010,
    "end": 0x01000011, "pageup": 0x01000016, "pagedown": 0x01000017,
}


def qt_key(spec):
    """'Ctrl+Alt+G' -> Qt key code int, as KGlobalAccel wants it."""
    code = 0
    parts = [s.strip() for s in spec.split("+") if s.strip()]
    for mod in parts[:-1]:
        code |= QT_MODS[mod.lower()]
    key = parts[-1]
    low = key.lower()
    if len(key) == 1 and key.isalnum():
        code |= ord(key.upper())
    elif low in QT_SPECIAL:
        code |= QT_SPECIAL[low]
    elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 35:
        code |= 0x01000030 + int(low[1:]) - 1
    else:
        raise ValueError(f"unknown key {key!r} in hotkey {spec!r}")
    return code
