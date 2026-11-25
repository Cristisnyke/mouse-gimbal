"""Tray app + KDE global hotkeys + audio cues. Entry point: main()."""
import argparse
import math
import os
import signal
import struct
import subprocess
import sys
import time
import wave

from .config import CONFIG_PATH, load, qt_key
from .engine import Engine, find_mouse, is_real, list_mice, unreadable_mice
from .filters import LOCKS

BUS_NAME = "org.snic.mousegimbal"
COMPONENT = "mouse-gimbal"
ACTIONS = {
    "toggle": "Gimbal on/off",
    "preset": "Next preset",
    "lock": "Axis lock (off/horizon/vertical)",
    "cruise": "Cruise: keep panning at current speed",
}
SOUND_DIR = os.path.expanduser("~/.cache/mouse-gimbal")
TONES = {  # name: list of (freq Hz, ms)
    "on": [(660, 60), (990, 80)],
    "off": [(990, 60), (660, 80)],
    "tick": [(1320, 40)],
    "cruise_on": [(880, 50), (0, 30), (880, 50)],
    "cruise_off": [(440, 90)],
    "error": [(220, 150)],
}


def make_sounds():
    os.makedirs(SOUND_DIR, exist_ok=True)
    rate = 44100
    for name, notes in TONES.items():
        path = os.path.join(SOUND_DIR, f"{name}.wav")
        if os.path.exists(path):
            continue
        frames = bytearray()
        for freq, ms in notes:
            n = int(rate * ms / 1000)
            for i in range(n):
                env = min(1.0, i / 200, (n - i) / 200)
                v = 0.25 * env * math.sin(2 * math.pi * freq * i / rate) if freq else 0.0
                frames += struct.pack("<h", int(v * 32767))
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))


def play(name):
    try:
        subprocess.Popen(["paplay", os.path.join(SOUND_DIR, f"{name}.wav")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class App:
    def __init__(self, bus, cfg):
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator, GLib, Gtk

        self.Gtk, self.GLib, self.AI = Gtk, GLib, AppIndicator
        self.bus = bus
        self.cfg = cfg
        self.presets = cfg["presets"]
        self.preset_i = cfg["start_index"]
        self.enabled = False
        self.cruise = False
        self.lock = self.presets[self.preset_i].lock
        self.last_key = {}

        self.engine = Engine(self.presets[self.preset_i], cfg["rate"], cfg["device"],
                             on_event=lambda *a: GLib.idle_add(self._on_engine, *a))
        self.engine.start()

        self.ind = AppIndicator.Indicator.new(
            COMPONENT, "input-mouse", AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.ind.set_menu(self._build_menu())
        self._register_hotkeys()
        self._refresh()

    # --- hotkeys -------------------------------------------------------------
    def _register_hotkeys(self):
        import dbus
        self.kga = dbus.Interface(self.bus.get_object("org.kde.kglobalaccel", "/kglobalaccel"),
                                  "org.kde.KGlobalAccel")
        for action, label in ACTIONS.items():
            aid = [COMPONENT, action, "Mouse Gimbal", label]
            self.kga.doRegister(aid)
            code = qt_key(self.cfg["hotkeys"][action])
            # SetPresent | NoAutoloading: use the config's key, not KDE's saved one
            self.kga.setShortcut(aid, dbus.Array([code], signature="i"), dbus.UInt32(6))
        self.bus.add_signal_receiver(
            self._on_hotkey, signal_name="globalShortcutPressed",
            dbus_interface="org.kde.kglobalaccel.Component", bus_name="org.kde.kglobalaccel",
            path=self.kga.getComponent(COMPONENT))

    def _unregister_hotkeys(self):
        import dbus
        for action, label in ACTIONS.items():
            aid = [COMPONENT, action, "Mouse Gimbal", label]
            try:
                self.kga.setInactive(aid)
                self.kga.unregister(COMPONENT, action)
            except dbus.DBusException:
                pass

    def _on_hotkey(self, component, action, _ts):
        if component != COMPONENT:
            return
        now = time.monotonic()
        if now - self.last_key.get(action, 0) < 0.15:  # key autorepeat
            return
        self.last_key[action] = now
        {"toggle": self.toggle, "preset": self.next_preset,
         "lock": self.next_lock, "cruise": self.toggle_cruise}[action]()

    # --- actions -------------------------------------------------------------
    def toggle(self):
        self.engine.send("enable", not self.enabled)

    def set_preset(self, i):
        self.preset_i = i % len(self.presets)
        self.engine.send("preset", self.presets[self.preset_i])

    def next_preset(self):
        self.set_preset(self.preset_i + 1)

    def set_lock(self, lock):
        self.engine.send("lock", lock)

    def next_lock(self):
        self.set_lock(LOCKS[(LOCKS.index(self.lock) + 1) % len(LOCKS)])

    def toggle_cruise(self):
        self.engine.send("cruise")

    def _on_engine(self, kind, value):
        if kind == "enabled":
            self.enabled = value
            self.cruise = False
            play("on" if value else "off")
        elif kind == "preset":
            self.lock = value.lock
            play("tick")
        elif kind == "lock":
            self.lock = value
            play("tick")
        elif kind == "cruise":
            if value or self.cruise:
                play("cruise_on" if value else "cruise_off")
            else:
                play("error")  # not moving (or gimbal off): nothing to cruise
            self.cruise = value
        elif kind == "error":
            print(f"mouse-gimbal: {value}", file=sys.stderr)
            play("error")
        self._refresh()
        return False

    # --- tray ----------------------------------------------------------------
    def _build_menu(self):
        Gtk = self.Gtk
        menu = Gtk.Menu()
        self.status_item = Gtk.MenuItem(label="")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)
        self.toggle_item = Gtk.MenuItem(label="")
        self.toggle_item.connect("activate", lambda _: self.toggle())
        menu.append(self.toggle_item)
        menu.append(Gtk.SeparatorMenuItem())

        self.preset_items = []
        sub_item, sub = Gtk.MenuItem(label="Preset"), Gtk.Menu()
        group = None
        for i, p in enumerate(self.presets):
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, p.name)
            group = item
            item.connect("toggled", lambda w, i=i: w.get_active() and i != self.preset_i and self.set_preset(i))
            sub.append(item)
            self.preset_items.append(item)
        sub_item.set_submenu(sub)
        menu.append(sub_item)

        self.lock_items = {}
        sub_item, sub = Gtk.MenuItem(label="Axis lock"), Gtk.Menu()
        group = None
        for lock in LOCKS:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, lock.capitalize())
            group = item
            item.connect("toggled", lambda w, lock=lock: w.get_active() and lock != self.lock and self.set_lock(lock))
            sub.append(item)
            self.lock_items[lock] = item
        sub_item.set_submenu(sub)
        menu.append(sub_item)

        menu.append(Gtk.SeparatorMenuItem())
        hk = self.cfg["hotkeys"]
        info = Gtk.MenuItem(label=f"Keys: {hk['toggle']} on/off · {hk['preset']} preset · "
                                  f"{hk['lock']} lock · {hk['cruise']} cruise")
        info.set_sensitive(False)
        menu.append(info)
        edit = Gtk.MenuItem(label="Edit config…")
        edit.connect("activate", lambda _: subprocess.Popen(["xdg-open", CONFIG_PATH]))
        menu.append(edit)
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: self.quit())
        menu.append(quit_item)
        menu.show_all()
        return menu

    def _refresh(self):
        p = self.presets[self.preset_i]
        state = "ON" if self.enabled else "Off"
        extra = []
        if self.lock != "off":
            extra.append(f"{self.lock} lock")
        if self.cruise:
            extra.append("cruising")
        text = f"Gimbal {state} — {p.name}" + (f" ({', '.join(extra)})" if extra else "")
        self.status_item.set_label(text)
        self.toggle_item.set_label("Turn off" if self.enabled else "Turn on")
        self.preset_items[self.preset_i].set_active(True)
        self.lock_items[self.lock].set_active(True)
        icon = "media-record" if self.cruise else ("camera-video" if self.enabled else "input-mouse")
        self.ind.set_icon_full(icon, state)
        self.ind.set_title(text)

    def quit(self):
        self.engine.close()
        self._unregister_hotkeys()
        self.Gtk.main_quit()


def main():
    ap = argparse.ArgumentParser(prog="mouse-gimbal", description="Smooth cinematic mouse camera for games")
    ap.add_argument("--list", action="store_true", help="list mice and exit")
    args = ap.parse_args()

    if args.list:
        mice = list_mice()
        for dev in mice:
            print(f"{dev.path}\t{dev.name}\t{dev.phys if is_real(dev) else '(virtual)'}")
        auto = find_mouse(load()["device"])
        print(f"\nWill use: {auto.name if auto else 'NOTHING'}")
        blocked = unreadable_mice()
        if blocked and not any(is_real(d) for d in mice):
            print(f"\n{len(blocked)} input devices are not readable; if your mouse is missing, "
                  "install the udev rule (see README).")
        return

    cfg = load()
    make_sounds()

    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    if bus.request_name(BUS_NAME, dbus.bus.NAME_FLAG_DO_NOT_QUEUE) != dbus.bus.REQUEST_NAME_REPLY_PRIMARY_OWNER:
        sys.exit("mouse-gimbal is already running (see the tray)")

    try:
        app = App(bus, cfg)
    except RuntimeError as err:
        sys.exit(f"mouse-gimbal: {err}")
    from gi.repository import GLib
    try:
        from gi.repository import GLibUnix
        signal_add = GLibUnix.signal_add
    except ImportError:
        signal_add = GLib.unix_signal_add
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (app.quit(), False)[1])
    app.Gtk.main()
