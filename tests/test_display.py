"""Tests for the screen UI: rendering, touch hit-testing, backend selection."""
import chess

from chessnood.boards.base import ConnectionState
from chessnood.config import DisplayConfig
from chessnood.display import (
    SCREEN_H,
    SCREEN_W,
    BUTTON_RECT,
    ConsoleDisplay,
    Display,
    PreviewDisplay,
    UiModel,
    make_display,
    point_in_button,
    render,
)


def test_render_produces_full_size_image():
    img = render(UiModel(ConnectionState.CONNECTED, "Du bist am Zug",
                         "Mach deinen Zug.", chess.Board()))
    assert img.size == (SCREEN_W, SCREEN_H)


def test_render_with_highlight_and_no_board():
    # must not raise when board is None or squares are highlighted
    render(UiModel(ConnectionState.SCANNING, "Suche das Brett …", "", None))
    render(UiModel(ConnectionState.CONNECTED, "Computer hat gezogen", "",
                   chess.Board(), [chess.G1, chess.F3]))


def test_point_in_button():
    x0, y0, x1, y1 = BUTTON_RECT
    assert point_in_button((x0 + x1) // 2, (y0 + y1) // 2)
    assert not point_in_button(5, 5)


def test_make_display_backends(tmp_path):
    assert type(make_display(DisplayConfig(backend="none"))) is Display
    assert isinstance(make_display(DisplayConfig(backend="console")), ConsoleDisplay)
    assert isinstance(make_display(DisplayConfig(backend="preview")), PreviewDisplay)
    # auto with a missing framebuffer device falls back to the console
    cfg = DisplayConfig(backend="auto", fb_device=str(tmp_path / "nope-fb"))
    assert isinstance(make_display(cfg), ConsoleDisplay)


def test_preview_display_writes_png(tmp_path):
    out = tmp_path / "screen.png"
    disp = PreviewDisplay(str(out))
    disp.update(UiModel(ConnectionState.CONNECTED, "Du bist am Zug", "", chess.Board()))
    assert out.exists() and out.stat().st_size > 0


def test_new_game_handler_fires():
    fired = []
    disp = ConsoleDisplay()
    disp.on_new_game(lambda: fired.append(True))
    disp._fire_new_game()
    assert fired == [True]
