"""Device I/O: grab the real mouse, run the gimbal at a fixed rate, emit via uinput.

Runs in its own thread. While the gimbal is OFF no mouse is open or grabbed, so it
goes straight to the game with zero added latency. If this process dies the kernel
releases the grab, so the mouse can never get stuck.

Mice are looked up fresh every time the gimbal turns on, and rescanned while it is
on, because one mouse can show up as different devices: wired vs. wireless dongle,
or a dongle exposing several mouse nodes (only one of which reports motion). We
grab every matching node and merge them.
"""
import os
import queue
import select
import threading
import time

import evdev
from evdev import UInput, ecodes as e

from . import kwin
from .filters import Gimbal

VIRTUAL_NAME = "mouse-gimbal"
RESCAN_S = 1.0  # while ON, look for new/reconnected mouse nodes this often (only when idle)

# Fixed virtual mouse profile, so it never depends on which real node was found first.
VIRTUAL_CAPS = {
    e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA,
               e.BTN_FORWARD, e.BTN_BACK, e.BTN_TASK],
    e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL,
               e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES],
}


def list_mice():
    mice = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        caps = dev.capabilities()
        if e.REL_X in caps.get(e.EV_REL, []) and e.BTN_LEFT in caps.get(e.EV_KEY, []):
            mice.append(dev)
        else:
            dev.close()
    return mice


def is_real(dev):
    """False for virtual (uinput) devices, including other tools' and our own."""
    return bool(dev.phys) and "uinput" not in dev.phys


def unreadable_mice():
    """Input event nodes we can't open (missing udev rule)."""
    out = []
    for path in sorted(os.listdir("/dev/input")):
        full = f"/dev/input/{path}"
        if path.startswith("event") and not os.access(full, os.R_OK):
            out.append(full)
    return out


def matches(dev, name=""):
    if dev.name == VIRTUAL_NAME or not is_real(dev):
        return False
    low = dev.name.lower()
    if name:
        return name.lower() in low
    # auto: any real mouse that isn't a keyboard's built-in mouse interface
    return "keyboard" not in low and "keychron" not in low


def find_mice(name="", skip_paths=()):
    """All mouse nodes matching `name` (or auto), opened. skip_paths: already held."""
    found = []
    for dev in list_mice():
        if dev.path not in skip_paths and matches(dev, name):
            found.append(dev)
        else:
            dev.close()
    return found


def find_mouse(name=""):
    """First matching mouse, for display (--list)."""
    mice = find_mice(name)
    for d in mice[1:]:
        d.close()
    return mice[0] if mice else None


class Engine:
    def __init__(self, preset, rate=1000, device_name="", on_event=None):
        self.gimbal = Gimbal(preset)
        self.period = 1.0 / rate
        self.device_name = device_name
        self.on_event = on_event or (lambda *a: None)
        self.enabled = False
        self.devs = {}  # fd -> InputDevice, grabbed while enabled
        self.ui = None
        self._cmds = queue.SimpleQueue()
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        self._stop = False
        self._last_scan = 0.0
        self._thread = threading.Thread(target=self._run, name="gimbal", daemon=True)

    # --- control (any thread) -------------------------------------------------
    def start(self):
        self.ui = UInput(VIRTUAL_CAPS, name=VIRTUAL_NAME)
        self._thread.start()

    def send(self, cmd, *args):
        self._cmds.put((cmd, args))
        os.write(self._wake_w, b"x")

    def stop(self):
        self._stop = True
        self.send("noop")
        self._thread.join(1.0)

    # --- devices (engine thread) ----------------------------------------------
    def _scan(self):
        """Grab any matching mouse nodes not held yet. Returns how many were added."""
        self._last_scan = time.monotonic()
        held = {d.path for d in self.devs.values()}
        added = 0
        for dev in find_mice(self.device_name, held):
            try:
                dev.grab()
            except OSError:  # gone again, or grabbed by another tool
                dev.close()
                continue
            self.devs[dev.fd] = dev
            added += 1
        if added:  # match the real mouse's KDE speed/accel (else KDE uses defaults)
            src = sorted(d.path for d in self.devs.values())[0]
            kwin.copy_async(src, self.ui.device.path)
        return added

    def _drop(self, dev):
        self.devs.pop(dev.fd, None)
        try:
            dev.ungrab()
        except OSError:
            pass
        try:
            dev.close()
        except OSError:
            pass

    def _release_all(self):
        for dev in list(self.devs.values()):
            self._drop(dev)

    def _set_enabled(self, on):
        if on == self.enabled:
            return
        if on:
            self._scan()
            if not self.devs:
                self.on_event("error", "no mouse found (run: mouse-gimbal --list)")
                self.on_event("enabled", False)
                return
            names = sorted({d.name for d in self.devs.values()})
            print(f"mouse-gimbal: grabbed {len(self.devs)} node(s): {', '.join(names)}", flush=True)
        else:
            self._release_all()
        self.gimbal.reset()
        self.enabled = on
        self.on_event("enabled", on)

    def _handle(self, cmd, args):
        g = self.gimbal
        if cmd == "enable":
            self._set_enabled(args[0])
        elif cmd == "preset":
            g.set_preset(args[0])
            self.on_event("preset", args[0])
        elif cmd == "lock":
            g.lock = args[0]
            self.on_event("lock", args[0])
        elif cmd == "cruise":
            on = g.toggle_cruise() if self.enabled else False
            self.on_event("cruise", on)

    def _read_input(self, dev):
        dx = dy = 0
        try:
            for ev in dev.read():
                if ev.type == e.EV_REL and ev.code == e.REL_X:
                    dx += ev.value
                elif ev.type == e.EV_REL and ev.code == e.REL_Y:
                    dy += ev.value
                elif ev.type in (e.EV_KEY, e.EV_REL):  # buttons, wheel: pass through now
                    try:
                        self.ui.write(ev.type, ev.code, ev.value)
                        self.ui.syn()
                    except OSError:
                        pass
        except BlockingIOError:
            pass
        return dx, dy

    def _emit(self, ix, iy):
        if ix:
            self.ui.write(e.EV_REL, e.REL_X, ix)
        if iy:
            self.ui.write(e.EV_REL, e.REL_Y, iy)
        if ix or iy:
            self.ui.syn()

    # --- main loop --------------------------------------------------------------
    def _run(self):
        try:
            os.nice(-5)  # only works with CAP_SYS_NICE / rtkit limits; harmless otherwise
        except OSError:
            pass
        next_tick = last_tick = time.perf_counter()
        idle = True
        while not self._stop:
            if self.enabled and not idle:
                timeout = max(0.0, next_tick - time.perf_counter())
            elif self.enabled:
                timeout = RESCAN_S
            else:
                timeout = 0.5
            fds = [self._wake_r] + (list(self.devs) if self.enabled else [])
            try:
                ready, _, _ = select.select(fds, [], [], timeout)
            except (OSError, ValueError):
                ready = []

            if self._wake_r in ready:
                try:
                    os.read(self._wake_r, 4096)
                except BlockingIOError:
                    pass
            while True:
                try:
                    cmd, args = self._cmds.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(cmd, args)
                except OSError as err:
                    self.on_event("error", str(err))

            if not self.enabled:
                idle = True
                continue

            dx = dy = 0
            for fd in ready:
                dev = self.devs.get(fd)
                if dev is None:
                    continue
                try:
                    x, y = self._read_input(dev)
                except OSError:  # unplugged / switched wired <-> wireless
                    print(f"mouse-gimbal: lost {dev.name}", flush=True)
                    self._drop(dev)
                    continue
                dx += x
                dy += y
            if dx or dy:
                self.gimbal.feed(dx, dy)
            if idle and (dx or dy or self.gimbal.cruise is not None):
                idle = False  # wake up: first tick right now with a normal dt
                last_tick = time.perf_counter() - self.period
                next_tick = time.perf_counter()

            if idle and time.monotonic() - self._last_scan >= RESCAN_S:
                if self._scan():  # mouse reconnected or switched mode: keep going
                    names = sorted({d.name for d in self.devs.values()})
                    print(f"mouse-gimbal: now holding {', '.join(names)}", flush=True)
                continue

            now = time.perf_counter()
            if not idle and now >= next_tick:
                dt = min(now - last_tick, 0.05)
                last_tick = now
                self._emit(*self.gimbal.tick(dt))
                next_tick += self.period
                if next_tick < now:  # fell behind: don't burst
                    next_tick = now + self.period
                if self.gimbal.settled:
                    idle = True

    def close(self):
        self.stop()
        self._release_all()
        if self.ui:
            self.ui.close()
