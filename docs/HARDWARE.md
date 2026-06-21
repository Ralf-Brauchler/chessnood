# Hardware verification (do this once, when the board is plugged in)

`boards/usb.py` is a port of the official `chessnutech/EasyLinkSDK`, which reads
the board and lights the LEDs over **USB-HID** — the only transport the vendor
confirms for the Chessnut **Pro** (their docs note BLE "seems not to work yet").
The device IDs, the realtime command, the board decode and the LED command are
taken verbatim from the SDK, so this is high-confidence; this checklist confirms
it on a physical Pro and tells you what to tweak if anything is off. You can run
all of it from a **Mac or Linux laptop** — no Pi needed.

## 0. Install the USB extra

```
pip install -e '.[usb]'
```

On Linux you also need access to the hidraw device (see step 1).

## 1. Find the board

Plug the board into the computer with a USB cable and turn it on, then:

```
chessnood scan
```

Expect a line like `Chessnut Pro (pid 0x8100)`. If nothing shows up:

- **Linux permissions:** raw HID needs a udev rule. `scripts/99-chessnut.rules`
  grants access — install it (`sudo cp scripts/99-chessnut.rules /etc/udev/rules.d/
  && sudo udevadm control --reload && sudo udevadm trigger`) and replug. Until
  then you can test with `sudo`.
- Check the cable is a **data** cable and the board is powered on.

## 2. Confirm board decoding

Set `board.backend: usb` in `config.yaml` and run `chessnood run` with
`log_level: debug`. Set the pieces to the **standard starting position** and watch
the log. Success = the decoded position matches the real board.

**If the decoded position is scrambled**, the fix is in `boards/usb.py`:

| Symptom | What to adjust |
|---|---|
| Board mirrored / rotated | the square mapping in `decode_board_report()` (`7 - j`, `7 - i`) |
| Wrong piece types | `_CHESS_PIECES` |
| Nothing decodes at all | the report framing (`REPORT_BOARD`, `BOARD_DATA_OFFSET`) |

## 3. Confirm LED control  ← the make-or-break check

It's the computer's turn in a game, so the from/to LEDs should light on the board.
The LED bytes are ported verbatim from the SDK, so this is expected to work. If
LEDs **don't** light or the **wrong** squares light:

- The most likely culprit is the HID **report-ID prefix** in `UsbBoard._write()`
  (it prepends `0x00`; some setups want the raw bytes with no prefix) — this is
  flagged `# VERIFY`. Try removing the prefix.
- Wrong squares → the bit/byte mapping in `encode_leds()` (`# VERIFY`).

This is the single feature the whole project depends on (the player reads the
board LEDs, not the screen), so verify it deliberately. The runner lights the
guidance squares here too (wrong squares in "fix" mode, king+rook for castling,
the captured pawn for en passant) — confirm those light as expected.

## 4. (Optional) Confirm the beep

The board has a beep command (`0B 04` + frequency + duration). The service uses
it for "your turn" / wrong-move / game-over cues (`board.beeps: true`). If beeps
don't sound or sound wrong, that command/encoding in `boards/usb.py` is the place
to adjust; set `board.beeps: false` to disable.

## Reference

Device VID `0x2d80`, Pro PID `0x81xx`, HID usage page `0xFF00`. Commands:
realtime `21 01 00`, LEDs `0A 08` + 8 rank bytes, beep `0B 04 …`, battery
`29 01 00`. All from EasyLinkSDK `sdk/EasyLink.cpp`.
