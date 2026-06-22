"""Engine wrapper: the random-mover fallback keeps the system running without
a Stockfish binary (so it works on a Mac, or before the Pi is set up)."""
import chess

from chessnood.config import EngineConfig
from chessnood.engine import Engine


def _missing_engine() -> Engine:
    # a path that cannot be started -> Engine falls back to random moves
    return Engine(EngineConfig(path="/nonexistent/stockfish-xyz"))


def test_falls_back_to_random_when_binary_missing():
    eng = _missing_engine()
    move = eng.best_move(chess.Board())
    assert move in set(chess.Board().legal_moves)
    eng.close()


def test_fallback_best_move_is_always_legal_across_a_game():
    eng = _missing_engine()
    board = chess.Board()
    for _ in range(20):
        if board.is_game_over():
            break
        move = eng.best_move(board)
        assert move in set(board.legal_moves)
        board.push(move)
    eng.close()


def test_configure_on_fallback_is_a_noop(tmp_path):
    eng = _missing_engine()
    # reconfiguring a non-running engine must not raise
    eng.configure(EngineConfig(skill_level=20, elo_limit=1500))
    assert eng.best_move(chess.Board()) in set(chess.Board().legal_moves)
    eng.close()


def test_close_is_idempotent():
    eng = _missing_engine()
    eng.close()
    eng.close()  # must not raise


class _DyingEngine:
    """Stand-in whose play() raises, like a crashed/terminated Stockfish."""

    def play(self, board, limit):
        raise RuntimeError("engine process died")

    def quit(self):
        pass


def test_engine_crash_mid_game_falls_back_to_random():
    eng = _missing_engine()
    eng._engine = _DyingEngine()              # pretend a real engine was running, then died
    move = eng.best_move(chess.Board())
    assert move in set(chess.Board().legal_moves)   # still produced a legal move
    assert eng._engine is None                # dead engine dropped; later moves use fallback
    eng.close()
