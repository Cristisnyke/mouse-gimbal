"""Real-device test: fake uinput mice -> Engine -> virtual mouse. Needs /dev/uinput.

Run: python3 tests/e2e_uinput.py   (the gimbal output is grabbed, so the cursor stays put)

Covers: smoothing + conservation, click passthrough, a mouse with two nodes (like the
SCYROX 8K dongle), and unplug -> replug as a different device (wired <-> wireless).
"""
import os
import select
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import evdev  # noqa: E402
from evdev import UInput, ecodes as e  # noqa: E402

from mouse_gimbal.engine import RESCAN_S, VIRTUAL_NAME, Engine  # noqa: E402
from mouse_gimbal.filters import Preset  # noqa: E402

CAPS = {e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT], e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL]}


def fake(name, phys):
    return UInput(CAPS, name=name, phys=phys)


def move(src, dx):
    src.write(e.EV_REL, e.REL_X, dx)
    src.syn()


wired = fake("gimbaltest V8", "test/wired")
time.sleep(0.4)
events = []
eng = Engine(Preset("t", 0.25, stabilize=True), 1000, "gimbaltest", on_event=lambda *a: events.append(a))
eng.start()
time.sleep(0.4)
out = next(evdev.InputDevice(p) for p in evdev.list_devices() if evdev.InputDevice(p).name == VIRTUAL_NAME)
out.grab()  # keep the test motion off the real desktop cursor

log = []
done = threading.Event()


def reader():
    while not done.is_set():
        r, _, _ = select.select([out.fd], [], [], 0.1)
        if r:
            for ev in out.read():
                log.append((time.perf_counter(), ev.type, ev.code, ev.value))


th = threading.Thread(target=reader)
th.start()


def total_x(since=0.0):
    return sum(v for t, ty, c, v in log if t >= since and ty == e.EV_REL and c == e.REL_X)


fails = []


def check(label, ok, info=""):
    print(f"{'PASS' if ok else 'FAIL'}  {label} {info}")
    if not ok:
        fails.append(label)


# 1. smoothing, conservation, clicks
eng.send("enable", True)
time.sleep(0.3)
t0 = time.perf_counter()
move(wired, 500)
wired.write(e.EV_KEY, e.BTN_LEFT, 1); wired.syn()
wired.write(e.EV_KEY, e.BTN_LEFT, 0); wired.syn()
time.sleep(1.5)
xs = [(t, v) for t, ty, c, v in log if ty == e.EV_REL and c == e.REL_X]
check("jerk conserved", total_x() == 500, f"total {total_x()}")
check("jerk smoothed", max(abs(v) for _, v in xs) <= 3, f"steps {len(xs)} max {max(abs(v) for _, v in xs)}")
check("click passthrough", [v for _, ty, _, v in log if ty == e.EV_KEY] == [1, 0])
gaps = sorted((xs[i + 1][0] - xs[i][0]) * 1000 for i in range(len(xs) - 1) if xs[i + 1][0] - xs[i][0] < 0.01)
check("1 kHz output", 0.9 < gaps[len(gaps) // 2] < 1.2, f"interval p50 {gaps[len(gaps) // 2]:.3f} ms")

# 2. unplug wired, plug in a two-node dongle; only the second node moves
wired.close()
time.sleep(0.2)
node_a = fake("gimbaltest 8K Dongle Mouse", "test/dongle/input1")
node_b = fake("gimbaltest 8K Dongle", "test/dongle/input2")
time.sleep(RESCAN_S + 0.6)
t1 = time.perf_counter()
move(node_b, 300)
time.sleep(1.5)
check("dongle picked up after hot-swap", total_x(t1) == 300, f"total {total_x(t1)}")
check("errors", not [ev for ev in events if ev[0] == "error"], str(events))

# 3. off releases everything, on again re-finds the dongle
eng.send("enable", False)
time.sleep(0.2)
check("off: nothing held", not eng.devs)
eng.send("enable", True)
time.sleep(0.3)
t2 = time.perf_counter()
move(node_a, -200)
time.sleep(1.5)
check("re-enable finds dongle", total_x(t2) == -200, f"total {total_x(t2)}")

done.set()
th.join()
out.ungrab()
eng.close()
node_a.close()
node_b.close()
print("\nALL PASS" if not fails else f"\nFAILED: {fails}")
sys.exit(1 if fails else 0)
