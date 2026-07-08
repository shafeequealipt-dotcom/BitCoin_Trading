"""Pass-3 audit guard — _lc_spike_triggered must always return a 4-tuple.

The caller in _pf_apply_spine unpacks four values (_spk, _adv, _spk_atr,
_spk_mult). Three early-return guards previously returned 3-tuples, raising
ValueError for a buffer-less position. This pins the arity two ways: a runtime
call of the buffer-None path, and a source-AST check that EVERY return in the
method yields a 4-element tuple, so a future edit cannot silently re-break it.
"""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest

from src.workers.profit_sniper import ProfitSniper


@pytest.mark.asyncio
async def test_spike_triggered_buffer_none_returns_4tuple():
    s = ProfitSniper.__new__(ProfitSniper)  # buffer-None path touches no self attr
    result = await s._lc_spike_triggered(
        "BTCUSDT", {"buffer": None}, SimpleNamespace(), 100.0, True,
    )
    assert isinstance(result, tuple) and len(result) == 4, result
    assert result[0] is False  # not triggered


def test_every_spike_return_is_a_4tuple():
    """Static guard: all return statements in _lc_spike_triggered return a
    literal 4-element tuple (matches the declared signature + caller unpack)."""
    src = inspect.getsource(ProfitSniper._lc_spike_triggered)
    tree = ast.parse(src.strip())
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef))
    returns = [n for n in ast.walk(func) if isinstance(n, ast.Return)]
    assert returns, "expected at least one return"
    for r in returns:
        assert isinstance(r.value, ast.Tuple), f"non-tuple return: {ast.dump(r)}"
        assert len(r.value.elts) == 4, f"return is not a 4-tuple: {ast.dump(r)}"
