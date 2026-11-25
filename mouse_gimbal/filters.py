"""Pure motion math: no I/O, so it can be unit tested.

Coordinates are mouse counts. The hand moves a *target* position; a simulated
gimbal head (critically damped spring) follows it. Whatever the filters do, the
camera always ends up exactly where the hand put it: nothing is lost or added.
"""
import math
from dataclasses import dataclass

LOCK_NONE, LOCK_HORIZON, LOCK_VERTICAL = "off", "horizon", "vertical"
LOCKS = (LOCK_NONE, LOCK_HORIZON, LOCK_VERTICAL)


@dataclass
class Preset:
    name: str
    smooth_time: float = 0.2        # s, spring lag. 0 = no spring
    max_speed: float = 0.0          # counts/s cap on camera speed. 0 = unlimited
    stabilize: bool = False         # One Euro tremor filter before the spring
    min_cutoff: float = 1.0         # Hz, One Euro: lower = steadier when slow
    beta: float = 0.002             # One Euro: higher = less lag when fast
    lock: str = LOCK_NONE           # axis lock this preset starts with


class OneEuro:
    """One Euro filter (Casiez et al. 2012) for one axis."""

    def __init__(self, min_cutoff, beta, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = None
        self.dx = 0.0

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self, x):
        self.x, self.dx = x, 0.0

    def __call__(self, x, dt):
        if self.x is None:
            self.reset(x)
            return x
        dx = (x - self.x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self.dx += a_d * (dx - self.dx)
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        self.x += self._alpha(cutoff, dt) * (x - self.x)
        return self.x


def smooth_damp(cur, target, vel, smooth_time, max_speed, dt):
    """2D critically damped spring (Game Programming Gems 4, ch. 1.10).

    cur, target, vel are (x, y) tuples. Returns (new_pos, new_vel).
    max_speed (counts/s, 0 = unlimited) caps how far ahead the target may pull,
    so the camera never moves faster than that.
    """
    smooth_time = max(1e-4, smooth_time)
    omega = 2.0 / smooth_time
    x = omega * dt
    exp = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)

    cx, cy = cur[0] - target[0], cur[1] - target[1]
    if max_speed > 0:
        max_change = max_speed * smooth_time
        mag = math.hypot(cx, cy)
        if mag > max_change:
            cx, cy = cx / mag * max_change, cy / mag * max_change
    tx, ty = cur[0] - cx, cur[1] - cy

    tmpx = (vel[0] + omega * cx) * dt
    tmpy = (vel[1] + omega * cy) * dt
    vx = (vel[0] - omega * tmpx) * exp
    vy = (vel[1] - omega * tmpy) * exp
    ox = tx + (cx + tmpx) * exp
    oy = ty + (cy + tmpy) * exp

    # never overshoot the real target
    gx, gy = target[0] - cur[0], target[1] - cur[1]
    if gx * (ox - target[0]) + gy * (oy - target[1]) > 0:
        ox, oy = target
        vx = vy = 0.0
    return (ox, oy), (vx, vy)


class Gimbal:
    """Hand counts in, smoothed integer counts out, one tick at a time."""

    SETTLE = 0.05  # counts

    def __init__(self, preset: Preset):
        self.lock = preset.lock
        self.set_preset(preset)
        self.reset()

    def set_preset(self, preset: Preset):
        self.preset = preset
        self.lock = preset.lock
        self.euro = [OneEuro(preset.min_cutoff, preset.beta) for _ in range(2)]
        if hasattr(self, "target"):
            for f, v in zip(self.euro, self.filtered):
                f.reset(v)

    def reset(self):
        self.target = [0.0, 0.0]     # where the hand says the camera should go
        self.filtered = [0.0, 0.0]   # target after tremor filter
        self.out = (0.0, 0.0)        # where the camera is (float)
        self.vel = (0.0, 0.0)        # camera speed, counts/s
        self.emitted = [0, 0]        # integer counts already sent
        self.cruise = None           # constant pan velocity, counts/s
        for f in self.euro:
            f.reset(0.0)

    # --- input ---------------------------------------------------------------
    def feed(self, dx, dy):
        if self.lock == LOCK_HORIZON:
            dy = 0
        elif self.lock == LOCK_VERTICAL:
            dx = 0
        self.target[0] += dx
        self.target[1] += dy

    def toggle_cruise(self, min_speed=50.0):
        """Lock the current pan speed (hands off), or stop cruising.

        Returns True if cruise is now on."""
        if self.cruise is not None:
            self.cruise = None
            return False
        if math.hypot(*self.vel) < min_speed:
            return False
        self.cruise = self.vel
        return True

    # --- output --------------------------------------------------------------
    @property
    def settled(self):
        return (
            self.cruise is None
            and abs(self.target[0] - self.out[0]) < self.SETTLE
            and abs(self.target[1] - self.out[1]) < self.SETTLE
            and abs(self.vel[0]) < 1.0
            and abs(self.vel[1]) < 1.0
        )

    def tick(self, dt):
        """Advance dt seconds. Returns integer (dx, dy) to send to the game."""
        p = self.preset
        if self.cruise is not None:
            self.target[0] += self.cruise[0] * dt
            self.target[1] += self.cruise[1] * dt

        if p.stabilize:
            self.filtered = [f(t, dt) for f, t in zip(self.euro, self.target)]
        else:
            self.filtered = list(self.target)

        if p.smooth_time > 0:
            self.out, self.vel = smooth_damp(
                self.out, tuple(self.filtered), self.vel, p.smooth_time, p.max_speed, dt
            )
        else:
            new = tuple(self.filtered)
            self.vel = ((new[0] - self.out[0]) / dt, (new[1] - self.out[1]) / dt)
            self.out = new

        if self.settled:
            self.out = (self.target[0], self.target[1])
            self.vel = (0.0, 0.0)

        ix = round(self.out[0]) - self.emitted[0]
        iy = round(self.out[1]) - self.emitted[1]
        self.emitted[0] += ix
        self.emitted[1] += iy

        if self.settled:
            self._rebase()
        return ix, iy

    def _rebase(self):
        """Keep numbers small: shift the origin to the current integer position."""
        ox, oy = self.emitted
        self.target = [self.target[0] - ox, self.target[1] - oy]
        self.out = (self.out[0] - ox, self.out[1] - oy)
        self.filtered = [self.filtered[0] - ox, self.filtered[1] - oy]
        self.emitted = [0, 0]
        for f, v in zip(self.euro, self.filtered):
            f.reset(v)
