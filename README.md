# mouse-gimbal

A software camera gimbal for PC games: turns shaky hand mouse movement into smooth, cinematic camera motion for capturing footage.

## How it works

```
physical mouse ──evdev (grabbed)──► filter / motion engine ──uinput virtual mouse @1000 Hz──► game
```

1. **Grab** the real mouse via evdev (`EVIOCGRAB`) so the game never sees raw input.
2. **Filter** the motion: treat the mouse as a *target* camera velocity, and let a
   simulated gimbal follow it.
3. **Emit** relative motion through a uinput virtual mouse at a fixed high rate, with a
   sub-count accumulator so fractional motion is never lost or quantized into jitter.

Works with any game that reads relative mouse input (native, Proton, Wine). No game hooks.

## Planned modes

| Mode | What it does |
|------|--------------|
| **Stabilize** | One Euro / low-pass filter: removes hand tremor, keeps intent |
| **Fluid** | Critically damped spring on velocity: inertia, ease-in/ease-out on every start/stop |
| **Horizon lock** | Kills vertical (or horizontal) motion for flat pans |
| **Speed limit** | Caps angular speed and acceleration so pans never whip |
| **Dolly** | Hotkey sets a constant pan speed; camera drifts on its own, hands off |
| **Record / replay** | Record a move, smooth it offline (spline), replay it perfectly repeatably |

## Footage tips

- Lower in-game sensitivity and output large counts: more counts per degree = finer motion.
- Disable in-game mouse acceleration/smoothing; let the gimbal do all smoothing.
- Lock the game to a stable frame rate when recording, so per-frame camera steps are even.
