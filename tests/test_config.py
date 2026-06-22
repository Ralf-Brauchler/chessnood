"""Config loading, defaults, tolerance and resilient live-reload."""
import chess

from chessnood.config import (
    BoardConfig,
    Config,
    ConfigWatcher,
    EngineConfig,
)


def test_defaults_when_no_path():
    cfg = Config.load(None)
    assert cfg.board.backend == "usb"
    assert cfg.board.settle_ms == 1000
    assert cfg.engine.skill_level == 5
    assert cfg.game.human_color == "white"


def test_missing_file_falls_back_to_defaults(tmp_path):
    cfg = Config.load(tmp_path / "does-not-exist.yaml")
    assert cfg.engine.path == EngineConfig().path


def test_partial_config_keeps_other_defaults(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("engine:\n  skill_level: 12\nboard:\n  beeps: false\n")
    cfg = Config.load(p)
    assert cfg.engine.skill_level == 12       # overridden
    assert cfg.engine.move_time_ms == 800     # default kept
    assert cfg.board.beeps is False           # overridden
    assert cfg.board.backend == "usb"         # default kept


def test_human_color_bool():
    assert Config.from_dict({"game": {"human_color": "white"}}).game.human_color_bool == chess.WHITE
    assert Config.from_dict({"game": {"human_color": "black"}}).game.human_color_bool == chess.BLACK
    # anything not starting with "w" is black; case-insensitive
    assert Config.from_dict({"game": {"human_color": "Black"}}).game.human_color_bool == chess.BLACK


def test_unknown_keys_are_ignored_not_fatal(tmp_path):
    """A typo in config.yaml must not crash the appliance -- the board still starts."""
    p = tmp_path / "c.yaml"
    p.write_text("engine:\n  skill_levle: 9\n  threads: 2\nbogus_top_level: 1\n")
    cfg = Config.load(p)                       # would raise TypeError without filtering
    assert cfg.engine.threads == 2             # the good key still applies
    assert cfg.engine.skill_level == EngineConfig().skill_level  # the typo'd one is ignored


def test_invalid_yaml_falls_back_to_defaults(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("engine: : :\n  not valid yaml: [\n")
    cfg = Config.load(p)
    assert cfg.board.backend == BoardConfig().backend


def test_watcher_reloads_only_on_change(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("engine:\n  skill_level: 3\n")
    w = ConfigWatcher(p)
    assert w.current.engine.skill_level == 3
    changed, cfg = w.poll()
    assert changed is False and cfg.engine.skill_level == 3

    import os, time
    time.sleep(0.01)
    p.write_text("engine:\n  skill_level: 8\n")
    os.utime(p, None)
    changed, cfg = w.poll()
    assert changed is True and cfg.engine.skill_level == 8


def test_watcher_keeps_last_good_config_on_broken_reload(tmp_path):
    """A half-written/invalid file caught mid-save must not reset the service."""
    p = tmp_path / "c.yaml"
    p.write_text("engine:\n  skill_level: 7\n")
    w = ConfigWatcher(p)
    assert w.current.engine.skill_level == 7

    import os, time
    time.sleep(0.01)
    p.write_text("engine:\n  skill_level: [\n")  # malformed
    os.utime(p, None)
    changed, cfg = w.poll()
    assert changed is False                      # reload rejected
    assert cfg.engine.skill_level == 7           # last good value retained
    assert w.current.engine.skill_level == 7
