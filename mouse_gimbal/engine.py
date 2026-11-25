"""Device I/O: grab the real mouse, run the gimbal at a fixed rate, emit via uinput.

Runs in its own thread. While the gimbal is OFF the real mouse is not grabbed,
so it goes straight to the game with zero added latency. If this process dies the
kernel releases the grab, so the mouse can never get stuck.
"""
import os
import queue
import select
import threading
import time

import evdev
from evdev import UInput, ecodes as e

from .filters import Gimbal

VIRTUAL_NAME = "mouse-gimbal"


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
    """Mouse event nodes we can't open (missing udev rule)."""
    out = []
    for path in sorted(os.listdir("/dev/input")):
        full = f"/dev/input/{path}"
        if path.startswith("event") and not os.access(full, os.R_OK):
            out.append(full)
    return out


def find_mouse(name=""):
    mice = list_mice()
    pick = None
    for dev in mice:
        if dev.name == VIRTUAL_NAME:
            continue
        if name:
            if name.lower() in dev.name.lower() and pick is None:
                pick = dev
        # auto: first real (USB/Bluetooth) mouse that isn't a keyboard's mouse interface
        elif pick is None and is_real(dev) and "keyboard" not in dev.name.lower() and "keychron" not in dev.name.lower():
            pick = dev
    for dev in mice:
        if dev is not pick:
            dev.close()
    return pick


class Engine:
    def __init__(self, preset, rate=1000, device_name="", on_event=None):
        self.gimbal = Gimbal(preset)
        self.period = 1.0 / rate
        self.device_name = device_name
        self.on_event = on_event or (lambda *a: None)
        self.enabled = False
        self.dev = None
        self.ui = None
        self._cmds = queue.SimpleQueue()
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="gimbal", daemon=True)

    # --- control (any thread) -------------------------------------------------
    def start(self):
        self._open()
        self._thread.start()

    def send(self, cmd, *args):
        self._cmds.put((cmd, args))
        os.write(self._wake_w, b"x")

    def stop(self):
        self._stop = True
        self.send("noop")
        self._thread.join(1.0)

    # --- engine thread --------------------------------------------------------
    def _open(self):
        self.dev = find_mouse(self.device_name)
        if self.dev is None:
            raise RuntimeError("no mouse found (run: mouse-gimbal --list)")
        if self.ui is None:
            self.ui = UInput.from_device(self.dev, name=VIRTUAL_NAME)

    def _set_enabled(self, on):
        if on == self.enabled:
            return
        if on:
            self.dev.grab()
        else:
            try:
                self.dev.ungrab()
            except OSError:
                pass
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

    def _read_input(self):
        dx = dy = 0
        try:
            for ev in self.dev.read():
                if ev.type == e.EV_REL and ev.code == e.REL_X:
                    dx += ev.value
                elif ev.type == e.EV_REL and ev.code == e.REL_Y:
                    dy += ev.value
                elif ev.type == e.EV_SYN:
                    continue
                else:  # buttons, wheel: pass through untouched, immediately
                    self.ui.write(ev.type, ev.code, ev.value)
                    self.ui.syn()
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
            else:
                timeout = 0.5
            fds = [self._wake_r] + ([self.dev.fd] if self.dev and self.enabled else [])
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

            if self.dev.fd in ready:
                try:
                    dx, dy = self._read_input()
                except OSError as err:  # unplugged
                    self.enabled = False
                    self.on_event("error", f"mouse lost: {err}")
                    self._reconnect()
                    continue
                if dx or dy:
                    self.gimbal.feed(dx, dy)
                    if idle:  # wake up: first tick right now with a normal dt
                        idle = False
                        last_tick = time.perf_counter() - self.period
                        next_tick = time.perf_counter()
            if self.gimbal.cruise is not None and idle:
                idle = False
                last_tick = time.perf_counter() - self.period
                next_tick = time.perf_counter()

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

    def _reconnect(self):
        try:
            self.dev.close()
        except OSError:
            pass
        self.on_event("enabled", False)
        while not self._stop:
            time.sleep(1.0)
            dev = find_mouse(self.device_name)
            if dev:
                self.dev = dev
                self.on_event("error", f"mouse back: {dev.name}")
                return

    def close(self):
        self.stop()
        if self.enabled:
            try:
                self.dev.ungrab()
            except OSError:
                pass
        if self.ui:
            self.ui.close()
