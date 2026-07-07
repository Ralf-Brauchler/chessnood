"""Read-only display UI: a calm, plain-language status panel.

The **board LEDs remain the primary move indicator** for the player (the user's
father, who does not read algebraic notation). This 3.5" screen is a calm,
plain-language status panel — and a *visual* highlighted board that doubles as a
fallback should LED-over-BLE turn out not to work on the Pro. It never shows
coordinates like "e2-e4".

A new game is started simply by resetting the pieces to the start position; the
screen has **no touch input** (the resistive panel on the MHS-3.5 is unreliable,
and an accidental tap must never wipe a game in progress).

Rendering is pure Pillow and produces an in-memory image; only the output sink
differs per backend, so the exact look can be previewed on a Mac
(``chessnood preview``) long before the Pi is wired up.

⚠️  The framebuffer byte layout and rotation for the MHS-3.5 display are
best-effort and flagged ``# VERIFY`` until confirmed on the real Pi — same
philosophy as ``boards/protocol.py``.
"""
from __future__ import annotations

import logging
import os
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

# Colours: dark, high-contrast, low-glare for a living room.
_BG = (16, 20, 24)
_FG = (236, 239, 242)
_MUTED = (150, 158, 168)
_LIGHT_SQ = (186, 190, 198)
_DARK_SQ = (92, 99, 110)
_HILITE = (242, 191, 64)
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

    # footer hint: a new game is started simply by resetting the pieces
    # (no touch/button -- the resistive touch panel is unreliable on this board)
    draw.line((12, 254, SCREEN_W - 12, 254), fill=(40, 46, 54), width=1)
    hint = "Neue Partie: alle Figuren in die Grundstellung stellen"
    _centred(draw, hint, _fit_font(draw, hint, SCREEN_W - 24, 20, 13), _MUTED,
             (0, 260, SCREEN_W, 312))
    return img


# --- backends -------------------------------------------------------------

class Display:
    """Base / no-op display. ``backend: none`` uses this directly."""

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
    """Draw to the SPI TFT framebuffer. Output only -- no touch input.

    Framebuffer geometry/byte order is best-effort and flagged ``# VERIFY``.
    """

    def __init__(self, cfg: DisplayConfig) -> None:
        self._cfg = cfg
        self._fb = cfg.fb_device
        self._fb_size, self._bpp = _probe_framebuffer(self._fb)

    def update(self, model: UiModel) -> None:
        img = render(model)
        if self._cfg.rotate:
            img = img.rotate(-self._cfg.rotate, expand=True)  # VERIFY direction
        try:
            with open(self._fb, "wb") as fh:
                fh.write(_pack(img, self._bpp))
        except OSError as exc:
            log.debug("Framebuffer write failed: %s", exc)


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
