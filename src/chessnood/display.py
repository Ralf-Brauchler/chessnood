"""Touch display UI: plain-language status panel + a "Neue Partie" touch button.

The **board LEDs remain the primary move indicator** for the player (the user's
father, who does not read algebraic notation). This 3.5" screen is a calm,
plain-language status panel — and a *visual* highlighted board that doubles as a
fallback should LED-over-BLE turn out not to work on the Pro. It never shows
coordinates like "e2-e4".

Rendering is pure Pillow and produces an in-memory image; only the output sink
and the touch input differ per backend, so the exact look can be previewed on a
Mac (``chessnood preview``) long before the Pi is wired up.

⚠️  The framebuffer byte layout, rotation and touch coordinate mapping for the
MHS-3.5 display are best-effort and flagged ``# VERIFY`` until confirmed on the
real Pi — same philosophy as ``boards/protocol.py``.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field

import chess

from .boards.base import ConnectionState
from .config import DisplayConfig

log = logging.getLogger(__name__)

SCREEN_W = 480
SCREEN_H = 320

# Layout (landscape 480x320).
_BOARD_ORIGIN = (14, 60)
_BOARD_SIZE = 200
_PANEL_X = 232
BUTTON_RECT = (10, 264, 470, 312)  # x0, y0, x1, y1 — the "Neue Partie" touch target

# Colours: dark, high-contrast, low-glare for a living room.
_BG = (16, 20, 24)
_FG = (236, 239, 242)
_MUTED = (150, 158, 168)
_LIGHT_SQ = (186, 190, 198)
_DARK_SQ = (92, 99, 110)
_HILITE = (242, 191, 64)
_BTN = (39, 110, 72)
_BTN_FG = (255, 255, 255)
_CONN_COLOUR = {
    ConnectionState.CONNECTED: (76, 175, 80),
    ConnectionState.SCANNING: (242, 191, 64),
    ConnectionState.ERROR: (211, 64, 64),
    ConnectionState.DISCONNECTED: (110, 116, 124),
}

_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf",
)


@dataclass
class UiModel:
    """Everything the screen needs to draw one frame."""

    connection: ConnectionState = ConnectionState.DISCONNECTED
    status: str = ""          # short headline, e.g. "Du bist am Zug"
    instruction: str = ""     # one-line plain-language guidance
    board: chess.Board | None = None
    highlight: list[int] = field(default_factory=list)  # squares to outline


# --- rendering (pure Pillow) ---------------------------------------------

def _font(size: int):
    from PIL import ImageFont

    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:  # older Pillow: fixed tiny default
        return ImageFont.load_default()


def _fit_font(draw, text: str, max_w: int, start: int, minimum: int = 16):
    """Largest font (<= start) that keeps ``text`` within ``max_w`` pixels."""
    size = start
    while size > minimum:
        font = _font(size)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 2
    return _font(minimum)


def _centred(draw, text, font, fill, box):
    x0, y0, x1, y1 = box
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((x0 + (x1 - x0 - tw) / 2 - bbox[0], y0 + (y1 - y0 - th) / 2 - bbox[1]),
              text, font=font, fill=fill)


def _wrap(draw, text, font, max_w):
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _draw_piece(draw, x, y, sq, piece: chess.Piece):
    pad = sq * 0.14
    white = piece.color == chess.WHITE
    fill = (245, 245, 245) if white else (24, 24, 24)
    edge = (24, 24, 24) if white else (236, 236, 236)
    draw.ellipse((x + pad, y + pad, x + sq - pad, y + sq - pad), fill=fill, outline=edge, width=2)
    _centred(draw, piece.symbol().upper(), _font(int(sq * 0.5)), edge,
             (x, y, x + sq, y + sq))


def _draw_board(draw, board: chess.Board | None, highlight):
    x0, y0 = _BOARD_ORIGIN
    sq = _BOARD_SIZE // 8
    hl = set(highlight or [])
    for row in range(8):          # row 0 = rank 8 (top)
        for col in range(8):      # col 0 = file a (left)
            square = chess.square(col, 7 - row)
            x, y = x0 + col * sq, y0 + row * sq
            base = _LIGHT_SQ if (row + col) % 2 == 0 else _DARK_SQ
            draw.rectangle((x, y, x + sq, y + sq), fill=base)
            if square in hl:
                draw.rectangle((x, y, x + sq, y + sq), outline=_HILITE, width=4)
            piece = board.piece_at(square) if board else None
            if piece:
                _draw_piece(draw, x, y, sq, piece)


def render(model: UiModel):
    """Render one UI frame to an RGB PIL image (480x320)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (SCREEN_W, SCREEN_H), _BG)
    draw = ImageDraw.Draw(img)

    # connection indicator (top-right dot)
    draw.ellipse((SCREEN_W - 30, 16, SCREEN_W - 14, 32),
                 fill=_CONN_COLOUR.get(model.connection, _MUTED))

    # headline status
    if model.status:
        font = _fit_font(draw, model.status, SCREEN_W - 56, 34)
        draw.text((16, 12), model.status, font=font, fill=_FG)

    _draw_board(draw, model.board, model.highlight)

    # plain-language instruction in the right panel
    if model.instruction:
        font = _font(20)
        y = 64
        for line in _wrap(draw, model.instruction, font, SCREEN_W - _PANEL_X - 14):
            draw.text((_PANEL_X, y), line, font=font, fill=_MUTED)
            y += 26

    # the one big touch control
    draw.rounded_rectangle(BUTTON_RECT, radius=10, fill=_BTN)
    _centred(draw, "Neue Partie", _font(30), _BTN_FG, BUTTON_RECT)
    return img


def point_in_button(x: int, y: int) -> bool:
    x0, y0, x1, y1 = BUTTON_RECT
    return x0 <= x <= x1 and y0 <= y <= y1


# --- backends -------------------------------------------------------------

class Display:
    """Base / no-op display. ``backend: none`` uses this directly."""

    def __init__(self) -> None:
        self._new_game = None

    def on_new_game(self, handler) -> None:
        self._new_game = handler

    def _fire_new_game(self) -> None:
        if self._new_game is not None:
            self._new_game()

    def update(self, model: UiModel) -> None:  # pragma: no cover - no-op
        pass

    def close(self) -> None:  # pragma: no cover - no-op
        pass


class ConsoleDisplay(Display):
    """No screen: log status transitions. Default fallback off a Pi."""

    def __init__(self) -> None:
        super().__init__()
        self._last = None

    def update(self, model: UiModel) -> None:
        if model.status != self._last:
            self._last = model.status
            log.info("[screen] %s", model.status)


class PreviewDisplay(Display):
    """Write each frame to a PNG so the look can be inspected on any machine."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path

    def update(self, model: UiModel) -> None:
        render(model).save(self._path)


class FramebufferDisplay(Display):
    """Draw to the SPI TFT framebuffer; read taps from the touch device.

    Everything hardware-specific (framebuffer geometry/byte order, touch
    calibration) is best-effort and flagged ``# VERIFY``.
    """

    def __init__(self, cfg: DisplayConfig) -> None:
        super().__init__()
        self._cfg = cfg
        self._fb = cfg.fb_device
        self._fb_size, self._bpp = _probe_framebuffer(self._fb)
        self._stop = threading.Event()
        self._touch = threading.Thread(target=self._touch_loop, daemon=True)
        self._touch.start()

    def update(self, model: UiModel) -> None:
        img = render(model)
        if self._cfg.rotate:
            img = img.rotate(-self._cfg.rotate, expand=True)  # VERIFY direction
        try:
            with open(self._fb, "wb") as fh:
                fh.write(_pack(img, self._bpp))
        except OSError as exc:
            log.debug("Framebuffer write failed: %s", exc)

    def close(self) -> None:
        self._stop.set()

    def _touch_loop(self) -> None:
        try:
            from evdev import InputDevice, ecodes, list_devices
        except Exception as exc:  # noqa: BLE001 - evdev missing/not on a Pi
            log.info("Touch disabled (evdev unavailable): %s", exc)
            return
        path = self._cfg.touch_device or _find_touch(list_devices, InputDevice)
        if not path:
            log.info("No touch device found; 'Neue Partie' available over SSH only")
            return
        dev = InputDevice(path)
        ax = dev.absinfo(ecodes.ABS_X)
        ay = dev.absinfo(ecodes.ABS_Y)
        x = y = None
        for event in dev.read_loop():  # pragma: no cover - hardware loop
            if self._stop.is_set():
                break
            if event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_X:
                    x = event.value
                elif event.code == ecodes.ABS_Y:
                    y = event.value
            elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH and event.value == 1:
                if x is None or y is None:
                    continue
                sx = int((x - ax.min) / max(1, ax.max - ax.min) * SCREEN_W)  # VERIFY calibration
                sy = int((y - ay.min) / max(1, ay.max - ay.min) * SCREEN_H)  # VERIFY calibration
                if point_in_button(sx, sy):
                    log.info("Touch: Neue Partie")
                    self._fire_new_game()


def _probe_framebuffer(device: str) -> tuple[tuple[int, int], int]:
    """Read geometry/bpp from /sys; fall back to 480x320x16 for the MHS-3.5."""
    name = os.path.basename(device)
    base = f"/sys/class/graphics/{name}"
    size, bpp = (SCREEN_W, SCREEN_H), 16
    try:
        with open(f"{base}/virtual_size") as fh:
            w, h = fh.read().strip().split(",")
            size = (int(w), int(h))
        with open(f"{base}/bits_per_pixel") as fh:
            bpp = int(fh.read().strip())
    except (OSError, ValueError):
        pass  # VERIFY on the real Pi
    return size, bpp


def _pack(img, bpp: int) -> bytes:
    """Pack an RGB image into framebuffer bytes (RGB565 LE for 16bpp)."""
    img = img.convert("RGB")
    if bpp != 16:
        return img.convert("RGBX").tobytes()  # 32bpp  # VERIFY byte order
    rgb = img.tobytes()
    out = bytearray(len(rgb) // 3 * 2)
    j = 0
    for i in range(0, len(rgb), 3):
        v = ((rgb[i] & 0xF8) << 8) | ((rgb[i + 1] & 0xFC) << 3) | (rgb[i + 2] >> 3)
        out[j] = v & 0xFF          # little-endian  # VERIFY
        out[j + 1] = (v >> 8) & 0xFF
        j += 2
    return bytes(out)


def _find_touch(list_devices, InputDevice):
    for path in list_devices():
        try:
            name = InputDevice(path).name.lower()
        except OSError:
            continue
        if "touch" in name or "ads7846" in name or "xpt2046" in name:
            return path
    return None


def make_display(cfg: DisplayConfig) -> Display:
    backend = cfg.backend
    if backend == "auto":
        backend = "framebuffer" if os.path.exists(cfg.fb_device) else "console"
    if backend == "none":
        return Display()
    if backend == "console":
        return ConsoleDisplay()
    if backend == "preview":
        return PreviewDisplay(cfg.preview_path)
    if backend == "framebuffer":
        try:
            return FramebufferDisplay(cfg)
        except Exception as exc:  # noqa: BLE001 - fall back rather than crash the service
            log.warning("Framebuffer display unavailable (%s); using console", exc)
            return ConsoleDisplay()
    log.warning("Unknown display backend %r; using console", backend)
    return ConsoleDisplay()
