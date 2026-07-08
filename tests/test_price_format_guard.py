"""Regression guard: PRICE values must not use mangling fixed-decimal formatting.

Background. Low-priced coins were rendered with hardcoded ``:.2f`` / ``:.4f`` /
``:,.0f`` formatting that collapsed sub-cent prices ("$0.0001584" -> "$0.0002"
or "$0.00"). The fix routes every price through
``src.core.utils.format_price`` (magnitude-aware) or the ``PriceFormatter``
service (exact exchange tick size). This guard AST-scans ``src/`` and fails if a
PRICE-named identifier is ever again formatted with a *mangling* fixed-decimal
spec, so the regression cannot silently return.

Scope of detection — DELIBERATELY targets the mangle range only:
  * Flags ``:.Nf`` / ``:,.Nf`` with **N <= 4** on a price-named expression.
    Four decimals cannot represent sub-cent coins (which need 6-8), so this is
    exactly the precision band that mangles.
  * Does NOT flag ``:.6f`` / ``:.8f`` — those are intentional high-precision
    diagnostics (SL-propagation deltas, price-divergence observers) where fixed
    high precision is correct and does not mangle.
  * Excludes non-price quantities (pnl / pct / qty / equity / leverage / ...).

If a NEW site is genuinely meant to be fixed-precision and price-named, add it to
``ALLOWLIST`` with a one-line reason — but the default answer is "use
format_price".

SCOPE NOTE: this guard scans f-strings (``ast.JoinedStr``), which is where ~all
price formatting lives. It does NOT inspect loguru brace-template strings
(``log.info("...{price:.2f}...", price=...)``) — those are plain string literals
whose ``{field}`` precision can't be tied to a kwarg by AST cheaply. The handful
of loguru price-logs in the tree were audited and fixed by hand; keep new
operator-facing price logs going through ``format_price`` on the kwarg value.
"""

import ast
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"

# A *mangling* fixed-decimal float spec: ".0f".."4f", optionally grouped (",.2f").
# N>=5 is intentionally NOT matched (does not mangle; used by hi-precision logs).
_MANGLE_SPEC = re.compile(r"^,?\.[0-4]f$")

# Identifier word-parts that mark a PRICE.
PRICE_TOKENS = {
    "price", "sl", "tp", "entry", "exit", "mark", "liq", "liquidation",
    "stoploss", "takeprofit", "target", "bid", "ask", "nsl",
    "fill", "vwap", "poc", "midpoint", "liquidity", "swing",
}
# Compound substrings that mark a price even when word-part splitting hides it
# (e.g. "stop_loss" -> {stop, loss}, "fib_key_level" -> {fib, key, level}).
PRICE_COMPOUNDS = (
    "stop_loss", "take_profit", "stoploss", "takeprofit", "trailing_stop",
    "fib_key_level", "liquidity_level", "support_level", "resistance_level",
    "_price",
)
# Word-parts that mean it is NOT a price (override; wins over PRICE_TOKENS).
# "confidence"/"prox"/"distance" guard against entry_*_confidence, sl_prox,
# trail_distance etc. — quantities that merely share a price-ish prefix.
# ("trail" is intentionally NOT a price token: trailing_stop_PRICE is caught
# by "price", while trail_distance / min_trail are distances, not prices.)
EXCLUSION_TOKENS = {
    "pnl", "pct", "percent", "qty", "quantity", "equity", "balance",
    "notional", "leverage", "margin", "bps", "ratio", "score", "conf",
    "confidence", "width", "dist", "distance", "prox", "proximity",
    "atr", "rsi", "adx", "amount", "cost", "fee", "vol", "count", "rate",
    "change", "usd", "delta", "strength", "consumed", "consumption",
    # depth = orderbook size (not price); cap/mult = TP-cap multipliers;
    # fraction/frac/gap = ratio constants (e.g. SL_TP_MIN_GAP_FRACTION).
    "depth", "cap", "mult", "fraction", "frac", "gap",
}

# Intentional, reviewed exceptions: (relative_path, identifier). Keep minimal.
ALLOWLIST: set[tuple[str, str]] = set()


def _word_parts(name: str) -> set[str]:
    """Split an identifier into lowercase word-parts (underscores + camelCase)."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return {p for p in spaced.lower().split("_") if p}


def _spec_str(fmt_spec) -> str:
    """Return the static format-spec string, or '' if dynamic/absent."""
    if fmt_spec is None:
        return ""
    parts: list[str] = []
    for v in getattr(fmt_spec, "values", []):
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            parts.append(v.value)
        else:
            return ""  # dynamic spec — out of scope
    return "".join(parts)


def _leaf_name(node) -> str:
    """Best-effort leaf identifier of a formatted expression."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else ""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
        return _leaf_name(node.value)
    if isinstance(node, ast.BoolOp):  # e.g. `getattr(...) or 0.0`
        return _leaf_name(node.values[0])
    if isinstance(node, ast.Call):
        # getattr(obj, "attr_name", default) -> the attribute-name string
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
        ):
            return _leaf_name(node.args[1])
        return _leaf_name(node.args[0]) if node.args else _leaf_name(node.func)
    if isinstance(node, ast.BinOp):
        return _leaf_name(node.left)
    return ""


def _violations_in_code(code: str, path: str = "<test>") -> list[tuple[str, int, str, str]]:
    """Return (path, lineno, identifier, spec) for each mangling price format."""
    out: list[tuple[str, int, str, str]] = []
    tree = ast.parse(code)
    for js in ast.walk(tree):
        if not isinstance(js, ast.JoinedStr):
            continue
        vals = js.values
        for i, node in enumerate(vals):
            if not isinstance(node, ast.FormattedValue):
                continue
            spec = _spec_str(node.format_spec)
            if not _MANGLE_SPEC.match(spec):
                continue
            # Skip percentages: a literal '%' immediately follows the field
            # (e.g. f"{sl_pct:.2f}%" — a rate, not a price).
            nxt = vals[i + 1] if i + 1 < len(vals) else None
            if (
                isinstance(nxt, ast.Constant)
                and isinstance(nxt.value, str)
                and nxt.value.lstrip().startswith("%")
            ):
                continue
            name = _leaf_name(node.value)
            parts = _word_parts(name)
            name_l = name.lower()
            is_price = bool(parts & PRICE_TOKENS) or any(
                c in name_l for c in PRICE_COMPOUNDS
            )
            if is_price and not (parts & EXCLUSION_TOKENS):
                out.append((path, node.lineno, name, spec))
    return out


# ── Self-tests: the detector itself must be correct ──────────────────

def test_guard_flags_mangling_price_formats():
    assert _violations_in_code('x = f"sl=${entry_price:.2f}"')
    assert _violations_in_code('x = f"{pos.mark_price:.4f}"')
    assert _violations_in_code('x = f"POC=${a.poc_price:,.0f}"')
    assert _violations_in_code('x = f"{sl_price:.2f}"')
    # Compound / word-part-split names and .get('key') leaves:
    assert _violations_in_code('x = f"{pos.stop_loss:.2f}"')
    assert _violations_in_code('x = f"{sig.suggested_take_profit:,.2f}"')
    assert _violations_in_code('x = f"{a.fib_key_level:,.0f}"')
    assert _violations_in_code("x = f\"{d.get('take_profit_price', 0):.2f}\"")


def test_guard_ignores_nonprice_and_hiprecision():
    assert not _violations_in_code('x = f"{pnl_pct:.2f}"')
    assert not _violations_in_code('x = f"{qty:.6f}"')
    assert not _violations_in_code('x = f"{equity:,.2f}"')
    assert not _violations_in_code('x = f"{rr_ratio:.2f}"')
    assert not _violations_in_code('x = f"{change:+.1f}"')
    # High-precision diagnostics are allowed (do not mangle):
    assert not _violations_in_code('x = f"{new_sl:.6f}"')
    assert not _violations_in_code('x = f"{local_price:.6f}"')
    # Percent fields are rates, not prices:
    assert not _violations_in_code('x = f"rec_sl_pct={_rec_sl:.2f}%"')
    assert not _violations_in_code('x = f"{tp_pct:.2f}% of price"')


# ── The actual repo guard ────────────────────────────────────────────

def test_no_mangling_price_formatting_in_src():
    violations: list[str] = []
    for py in _SRC.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        rel = str(py.relative_to(_SRC.parent))
        try:
            code = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            found = _violations_in_code(code, rel)
        except SyntaxError:
            continue
        for (_p, lineno, name, spec) in found:
            if (rel, name) in ALLOWLIST:
                continue
            violations.append(f"{rel}:{lineno}  {name}:{spec}")

    assert not violations, (
        "Mangling fixed-decimal price formatting found — route through "
        "src.core.utils.format_price (or PriceFormatter). Offenders:\n  "
        + "\n  ".join(sorted(violations))
    )
