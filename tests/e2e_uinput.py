"""Real-device test: fake uinput mouse -> Engine -> virtual mouse. Needs /dev/uinput.

Run: python3 tests/e2e_uinput.py   (the gimbal output is grabbed, so the cursor stays put)"""
import sys, time, threading, select
import os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import evdev
from evdev import UInput, ecodes as e
from mouse_gimbal.engine import Engine, VIRTUAL_NAME
from mouse_gimbal.filters import Preset

src = UInput({e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT], e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL]},
             name="gimbal-test-src", phys="test/src")
time.sleep(0.5)
events = []
eng = Engine(Preset("t", 0.25, stabilize=True), 1000, "gimbal-test-src", on_event=lambda *a: events.append(a))
eng.start()
time.sleep(0.3)
out = next(evdev.InputDevice(p) for p in evdev.list_devices() if evdev.InputDevice(p).name == VIRTUAL_NAME)
out.grab()  # keep the test motion off the real desktop cursor
eng.send("enable", True); time.sleep(0.2)
log = []
def reader():
    t_end = time.time() + 3
    while time.time() < t_end:
        r,_,_ = select.select([out.fd],[],[],0.1)
        if r:
            for ev in out.read():
                log.append((time.perf_counter(), ev.type, ev.code, ev.value))
th = threading.Thread(target=reader); th.start()
t0 = time.perf_counter()
src.write(e.EV_REL, e.REL_X, 500); src.syn()         # sudden jerk
src.write(e.EV_KEY, e.BTN_LEFT, 1); src.syn()        # click passthrough
src.write(e.EV_KEY, e.BTN_LEFT, 0); src.syn()
time.sleep(1.2)
src.write(e.EV_REL, e.REL_X, -500); src.syn()
th.join()
xs = [(t, v) for t, ty, c, v in log if ty == e.EV_REL and c == e.REL_X]
btn = [v for t, ty, c, v in log if ty == e.EV_KEY]
print("events", events)
print("total x", sum(v for _, v in xs), "n", len(xs), "max step", max(abs(v) for _, v in xs))
print("buttons", btn)
gaps = [ (xs[i+1][0]-xs[i][0])*1000 for i in range(len(xs)-1) if xs[i+1][0]-xs[i][0] < 0.01]
gaps.sort()
print("interval ms p50 %.3f p99 %.3f max %.3f" % (gaps[len(gaps)//2], gaps[int(len(gaps)*.99)], gaps[-1]))
print("first output after %.2f ms" % ((xs[0][0]-t0)*1000))
out.ungrab(); eng.close(); src.close()
