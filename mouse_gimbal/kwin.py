"""Copy KDE pointer speed/acceleration from the real mouse to the virtual one.

KWin keeps pointer settings per device. Our uinput mouse is a new device, so KDE
would run it at default speed with adaptive acceleration: much faster than a tuned
real mouse. Runtime only (nothing is written to kcminputrc). No-op outside KDE.
"""
import os
import subprocess
import threading

DEST = "org.kde.KWin"
IFACE = "org.kde.KWin.InputDevice"
PROPS = (("pointerAcceleration", "d"),
         ("pointerAccelerationProfileFlat", "b"),
         ("pointerAccelerationProfileAdaptive", "b"))


def _busctl(*args):
    return subprocess.run(["busctl", "--user", *args], capture_output=True, text=True, timeout=2)


def _get(sysname, prop):
    r = _busctl("get-property", DEST, f"/org/kde/KWin/InputDevice/{sysname}", IFACE, prop)
    if r.returncode != 0:
        return None
    return r.stdout.split(None, 1)[1].strip()  # 'd -0.875' -> '-0.875'


def _set(sysname, prop, sig, value):
    # "--" so a negative speed like -0.875 isn't parsed as an option
    return _busctl("set-property", DEST, f"/org/kde/KWin/InputDevice/{sysname}", IFACE,
                   prop, sig, "--", value).returncode == 0


def copy_pointer_settings(src_path, dst_path, log=print):
    src, dst = os.path.basename(src_path), os.path.basename(dst_path)
    try:
        values = [(p, s, _get(src, p)) for p, s in PROPS]
        if any(v is None for _, _, v in values):
            return False
        # set the profile before the speed: KWin applies speed per profile
        ok = all([_set(dst, p, s, v) for p, s, v in values[1:] + values[:1]])
        if not ok:
            log(f"mouse-gimbal: could not copy pointer settings to {dst}")
            return False
        log(f"mouse-gimbal: pointer settings copied from {src} (speed {values[0][2]}, "
            f"flat {values[1][2]})")
        return True
    except (OSError, subprocess.SubprocessError, IndexError):
        return False


def copy_async(src_path, dst_path):
    threading.Thread(target=copy_pointer_settings, args=(src_path, dst_path), daemon=True).start()
