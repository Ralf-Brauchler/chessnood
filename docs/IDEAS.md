# Ideas / backlog

Notes for future work. Not yet implemented.

## Guided, one-square-at-a-time computer move (sequential LEDs)

**Idea.** Instead of lighting both the *from* and *to* squares of the computer's
move at once, guide the player through it one step at a time:

1. Light **only the source square** (the piece to move).
2. Wait until the player **lifts that piece** (source square goes empty).
3. Then light **only the destination square**.
4. Ideally, **"march" the LEDs** square by square along the path from source to
   destination, repeating, so the destination is easy to find and the direction
   is obvious.

**Why (for the father).** Less to interpret at once — "pick up *this* one", then
"put it *there*". Removes the ambiguity of two simultaneously lit squares (which is
from, which is to). The marching animation is a strong, intuitive direction/target
cue. Very senior-friendly.

**Assessment: promising, medium effort. Do it after LED hardware is verified.**

### Feasibility
- Our board LEDs are individually addressable (on/off, any subset), and the runner
  is already async/timed — a marching animation is just a timed LED loop. The SDK
  enforces ~200 ms between writes, so a ~7-square sweep ≈ 1.4 s; fine for a gentle
  repeat.
- Needs us to react to the **lift event** (source square empties) to advance from
  step 1 to step 3. Today the settle-debounce *absorbs* that transient on purpose;
  for this we'd watch specifically for "source square empty" during
  `ENGINE_MOVE_SHOWN` and advance immediately (bypassing settle for that one cue).

### Open points / complications
- **Path for the animation.** Rook/bishop/queen = straight line of squares.
  Knight = an L (no straight path) — pick a sensible 2-leg path or just pulse
  from→to alternately for knights.
- **Special moves become multi-step.** Castling: king from→to, then rook from→to.
  En passant: from→to, then the captured-pawn square to remove. Capture: light the
  destination (enemy piece there) so he removes+places. More sub-states.
- **Hardware-gated.** Entirely LED-driven, so it depends on LED control working on
  the real Chessnut Pro (the one big unverified piece). The screen already shows the
  move; it could mirror the same sequential/animation logic.

### Suggested phasing
1. ~~Phase A: two-step (light source → on lift → light destination), no animation.~~
   **DONE.** For *simple* moves (no capture/castling/en passant/promotion),
   `compute_guidance` now lights only the source ("Hebe die leuchtende Figur an.")
   and, once it is lifted, only the destination ("Stelle die Figur auf das
   leuchtende Feld."). The runner advances this **live** during `ENGINE_MOVE_SHOWN`
   (`_show_sensed` → `_apply_guidance(beep=False)`), bypassing the settle window so
   the LEDs follow the piece; the move still only *commits* after settling. Special
   moves keep lighting all involved squares at once (that's Phase C). The demo's
   `SelfPlayBoard` follows the lit squares (`_play_engine_move` / `_wait_for_leds`)
   instead of inferring the move from two simultaneous LEDs. Verified live in the
   demo (both clean play and the fumble/recovery path). Still LED-hardware-gated on
   the real Pro; the screen mirrors the same sequence today.
2. Phase B: add the marching-path animation.
3. Phase C: sequential handling for castling / en passant / capture.

---

## Demo: let the mock make mistakes (showcase self-healing) — DONE

`SelfPlayBoard` now optionally fumbles: before the correct final position it can
emit a *wrong* placement (the moving piece on an empty, non-destination square,
chosen via `detect_move` so it reads as INVALID), held `mistake_pause` seconds
(> settle window) so it commits and the recovery UI shows, then corrects itself.
Controlled by `mistake_chance` (constructor default 0 = always correct) and the
`chessnood demo --mistakes P` flag (default 0.3). Verified live: a fumbled player
move shows "Das passt nicht", a fumbled engine-move execution shows "Fast — bitte
den leuchtenden Zug ausführen", both auto-recovering. Tests in test_game.py.
