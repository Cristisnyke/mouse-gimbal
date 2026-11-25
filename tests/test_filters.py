import math
import random
import unittest

from mouse_gimbal.config import DEFAULT_TOML, parse, qt_key
from mouse_gimbal.filters import LOCK_HORIZON, Gimbal, Preset

import tomllib

DT = 0.001


def run(g, ticks, feed=None):
    """Tick g; feed(i) -> (dx, dy) or None. Returns list of emitted (ix, iy)."""
    out = []
    for i in range(ticks):
        if feed:
            d = feed(i)
            if d:
                g.feed(*d)
        out.append(g.tick(DT))
    return out


class GimbalTest(unittest.TestCase):
    def test_conserves_motion(self):
        """Total output equals total input once settled: aim ends where the hand put it."""
        for preset in [Preset("a", 0.25, stabilize=True), Preset("b", 0.6, max_speed=2000),
                       Preset("c", 0.0), Preset("d", 0.06, stabilize=True, min_cutoff=2)]:
            g = Gimbal(preset)
            rnd = random.Random(1)
            moves = [(rnd.randint(-40, 40), rnd.randint(-40, 40)) for _ in range(500)]
            emitted = run(g, 6000, lambda i: moves[i] if i < 500 else None)
            sx = sum(m[0] for m in moves); sy = sum(m[1] for m in moves)
            self.assertEqual(sum(e[0] for e in emitted), sx, preset.name)
            self.assertEqual(sum(e[1] for e in emitted), sy, preset.name)
            self.assertTrue(g.settled, preset.name)

    def test_step_is_smoothed(self):
        """A sudden 1000-count jerk becomes many small steps with no overshoot."""
        g = Gimbal(Preset("s", 0.25))
        out = run(g, 3000, lambda i: (1000, 0) if i == 0 else None)
        xs = [o[0] for o in out]
        self.assertEqual(sum(xs), 1000)
        self.assertLess(max(xs), 20)        # never a big jump
        self.assertGreaterEqual(min(xs), 0)  # never backwards (no overshoot)

    def test_subcount_slow_pan(self):
        """0.3 counts/tick of pan comes out as an even 0/1 pattern, nothing lost."""
        g = Gimbal(Preset("s", 0.0))
        g.cruise = (300.0, 0.0)
        out = run(g, 1000)
        self.assertIn(sum(o[0] for o in out), (299, 300, 301))
        self.assertLessEqual(max(o[0] for o in out), 1)

    def test_max_speed(self):
        g = Gimbal(Preset("s", 0.2, max_speed=2000))
        out = run(g, 4000, lambda i: (5000, 0) if i == 0 else None)
        # 2000 counts/s at 1000 Hz = 2/tick (+1 rounding)
        self.assertLessEqual(max(o[0] for o in out), 3)
        self.assertEqual(sum(o[0] for o in out), 5000)

    def test_horizon_lock(self):
        g = Gimbal(Preset("s", 0.1, lock=LOCK_HORIZON))
        out = run(g, 500, lambda i: (5, 7))
        self.assertEqual(sum(o[1] for o in out), 0)

    def test_stabilize_reduces_tremor(self):
        """Jitter on top of a slow pan: output is much smoother than input."""
        rnd = random.Random(2)
        jitter = [(round(math.sin(i * 0.9) * 6 + rnd.gauss(0, 3)) + 2, 0) for i in range(3000)]

        def roughness(seq):
            return sum(abs(seq[i + 1] - seq[i]) for i in range(len(seq) - 1))

        raw = [j[0] for j in jitter]
        g = Gimbal(Preset("s", 0.25, stabilize=True))
        out = [o[0] for o in run(g, 3000, lambda i: jitter[i])]
        self.assertLess(roughness(out[500:]), roughness(raw[500:]) / 5)

    def test_cruise(self):
        g = Gimbal(Preset("s", 0.2))
        run(g, 300, lambda i: (3, 0))   # moving at ~3000 counts/s
        self.assertTrue(g.toggle_cruise())
        out = run(g, 1000)              # hands off, keeps panning
        self.assertGreater(sum(o[0] for o in out), 2000)
        self.assertFalse(g.toggle_cruise())

    def test_cruise_needs_motion(self):
        g = Gimbal(Preset("s", 0.2))
        self.assertFalse(g.toggle_cruise())


class ConfigTest(unittest.TestCase):
    def test_default_config_parses(self):
        cfg = parse(tomllib.loads(DEFAULT_TOML))
        self.assertEqual([p.name for p in cfg["presets"]], ["Light", "Cinematic", "Heavy", "Tripod pan"])
        self.assertEqual(cfg["presets"][cfg["start_index"]].name, "Cinematic")
        for spec in cfg["hotkeys"].values():
            qt_key(spec)

    def test_qt_key(self):
        self.assertEqual(qt_key("Ctrl+Alt+G"), 0x04000000 | 0x08000000 | ord("G"))
        self.assertEqual(qt_key("F9"), 0x01000038)
        self.assertEqual(qt_key("Shift+Pause"), 0x02000000 | 0x01000008)


if __name__ == "__main__":
    unittest.main()
