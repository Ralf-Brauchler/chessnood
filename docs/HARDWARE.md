# Hardware verification (do this once, when the board is reachable)

The Bluetooth protocol in `src/chessnood/boards/protocol.py` is implemented from
public documentation but **has not yet been confirmed on a physical Chessnut Pro.**
This checklist confirms it and tells you exactly what to tweak if something is off.
You can run all of this from a **Mac or Linux laptop** with Bluetooth — no Pi needed.

## 0. Install the BLE extra

```
pip install -e '.[ble]'
```

## 1. Find the board

Turn the board on, then:

```
chessnood scan
```

Expect a line like `... Chessnut Pro  <-- likely Chessnut`. Note the address.
If nothing shows up: the board may need to be woken (move a piece) or isn't
advertising — check it's not already connected to a phone.

## 2. Confirm board decoding

Set `board.backend: ble` (and optionally `board.address:` to the value from step 1)
in `config.yaml`, then run `chessnood run` with `log_level: debug`.

Set the pieces to the **standard starting position** and watch the log. Add a
tiny debug print of the decoded `piece_map` if needed. Success = the decoded
position matches the real board.

**If the decoded position is scrambled**, the fix is one of these constants in
`protocol.py` (each marked `# VERIFY`):

| Symptom | Constant to adjust |
|---|---|
| Board mirrored / rotated | `stream_index_to_square()` ordering |
| Two squares per byte swapped | nibble order in `decode_board()` |
| Wrong piece types | `_CODE_TO_SYMBOL` mapping |
| Whole board offset | `DATA_OFFSET` / `DATA_LEN` |

## 3. Confirm LED control  ← the biggest open question

While connected, light a known square. The cleanest check: it's the computer's
turn in a game and the from/to LEDs should light. If LEDs **don't** light or the
**wrong** squares light:

- adjust `LED_COMMAND`, or the bit order in `encode_leds()` (`# VERIFY`).
- If the Pro turns out not to expose LED control over BLE at all, fall back to an
  audible cue (a small buzzer on a GPIO pin) instead of board LEDs — the game
  logic is unaffected; only the "show the computer's move" step changes.

## 4. (Optional, later) USB instead of Bluetooth

The board is a USB-HID device (Pro product IDs `0x81xx`, vendor `0x2d99` — **verify**
with `lsusb`). USB is rock-solid on a Pi (no pairing) but the Linux USB path is
less proven than BLE; treat it as a later enhancement. The udev rule in
`scripts/99-chessnut.rules` grants access; a USB backend would live in
`boards/usb.py` alongside the BLE one.
