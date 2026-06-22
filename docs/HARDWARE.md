# Hardware bring-up — verify the board interface one layer at a time

`boards/usb.py` is a port of the official `chessnutech/EasyLinkSDK`, which reads
the board and lights the LEDs over **USB-HID** — the only transport the vendor
confirms for the Chessnut **Pro** (their docs note BLE "seems not to work yet").
The device IDs, the realtime command, the board decode and the LED command are
taken verbatim from the SDK, so this is high-confidence — but it has never run
against a physical Pro.

This is a **ladder**: each rung tests exactly *one* layer of the interface with a
single command, has a clear expected result, and tells you what to change if it
fails. Climb in order — the first red rung is the problem, and everything below it
is already proven. No need to debug the whole stack at once. Run it all from a
**Mac or Linux laptop**; no Pi needed.

The diagnostic commands talk straight to the protocol (no game logic, engine or
screen in the way). `--no-prefix` on any of them drops the leading `0x00` HID
report-ID byte — the one convention we're unsure of (see rung 2).

## Rung 0 — install the USB extra

```
pip install -e '.[usb]'
```

Linux also needs access to the hidraw device (rung 1).

## Rung 1 — the board is found

Plug the board in with a **data** USB cable, turn it on:

```
chessnood scan
```

- ✅ A line like `Chessnut Pro (pid 0x8100)`.
- ❌ Nothing → cable/power, **or** Linux permissions, **or** our device filter.
  - Linux: install the udev rule — `sudo cp scripts/99-chessnut.rules
    /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger`,
    then replug (or test once with `sudo`).
  - Still nothing with the board clearly on → the VID/PID/usage-page filter in
    `_find_device()` (`VENDOR_ID`, `PRODUCT_IDS`, `USAGE_PAGE`). Run `sudo
    python -c "import hid; print(hid.enumerate())"` and compare.

## Rung 2 — open + handshake (the write path)

```
chessnood dump --seconds 5
```

This opens the device and **writes** the realtime command `21 01 00`. If it opens
and writes without an exception, the write path works.

- ✅ No "Could not open the board" error; it starts streaming (rung 3).
- ❌ Opens but writes seem ignored / it errors on write → try `chessnood dump
  --no-prefix`. The `_write()` report-ID prefix (`# VERIFY`) is the likely cause;
  whichever variant works, set it as the default in `UsbBoard._write()`.

## Rung 3 — reports actually stream

Same `chessnood dump` window: move a piece.

- ✅ Lines scroll, e.g. `#0001 type=0x01 len=32  01 20 …`. Type `0x01` and
  length `32` match `REPORT_BOARD` / `BOARD_DATA_LEN`.
- ❌ No lines at all → the board didn't accept realtime (revisit rung 2), or it
  streams a different report type/length → adjust `REPORT_BOARD`,
  `BOARD_DATA_OFFSET`, `BOARD_DATA_LEN`.

## Rung 4 — decode: empty + start position

```
chessnood watch
```

Clear the board, then set the **standard starting position**.

- ✅ Empty board shows `(leer)`; the start position shows the full back ranks and
  pawns, correct piece letters (uppercase = white).
- ❌ Pieces decode but **wrong types** → `_CHESS_PIECES`. Nothing decodes →
  framing (rung 3).

## Rung 5 — read orientation ⟵ the big unknown

Still in `chessnood watch`: put a **single** distinctive piece on a known corner —
e.g. just a white king on **a1** — and read off where it shows.

- ✅ It appears on **a1**. Repeat for **h1**, **a8**, **h8**: each shows where you
  put it.
- ❌ It shows mirrored/rotated (e.g. you place a1 but it reads h8) → fix the
  square mapping in `decode_board_report()` (`chess.square(7 - j, 7 - i)`). The
  corner that's wrong tells you the transform: file flipped, rank flipped, or both.

Using one piece makes this unambiguous — no guessing from a full position.

## Rung 6 — LED write path

```
chessnood led a1
```

This writes the LED command. If it returns without an error, the write path for
LEDs works (independent of *which* square lights).

- ❌ Errors on write → as rung 2, try `chessnood led a1 --no-prefix`.

## Rung 7 — LED orientation ⟵ make-or-break

This is the one feature the whole project depends on (the player reads the board
LEDs, not the screen). With `chessnood led a1`, look at the board:

- ✅ Exactly **a1** lights. Repeat `chessnood led h1`, `led a8`, `led h8` — each
  lights the square you named.
- ❌ Wrong square lights → the bit/byte order in `encode_leds()` (`# VERIFY`).
  Nothing lights at all → the prefix (`--no-prefix`).

**Crucial:** the LED orientation (rung 7) and the read orientation (rung 5) must
agree — same square name → same physical square — or in a game the right move
will light the wrong squares. Light a corner and confirm it's the *same* corner
`watch` reported for that square.

## Rung 8 — beep

```
chessnood beep
```

- ✅ One tone. (`chessnood beep --freq 440 --ms 300` to vary it.)
- ❌ Silent / wrong → the `0B 04` encoding in `beep()`. Not essential; set
  `board.beeps: false` to disable.

## Then: the whole stack

Once the rungs are green, set `board.backend: usb` in `config.yaml` and run
`chessnood run`. The pieces and LEDs now drive a real game; the runner lights the
guidance squares (wrong squares in "fix" mode, king+rook for castling, the
captured pawn for en passant) using the exact mapping you just verified.

## Reference

Device VID `0x2d80`, Pro PID `0x81xx`, HID usage page `0xFF00`. Commands:
realtime `21 01 00`, LEDs `0A 08` + 8 rank bytes (byte 0 = rank 8, file a = high
bit), beep `0B 04` + freq(2) + dur(2), battery `29 01 00`. All from EasyLinkSDK
`sdk/EasyLink.cpp`.
