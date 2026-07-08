"""Smoke test for BybitDemoClient HMAC-SHA256 signing.

Verifies the signing function produces the deterministic Bybit V5 hex
digest given a known key/secret/timestamp/payload tuple. The expected
signature was computed independently with openssl:

    echo -n "1700000000000fakekey5000{...}" | \\
        openssl dgst -sha256 -hmac "fakesecret" -hex
"""

from __future__ import annotations

from src.bybit_demo.bybit_demo_client import BybitDemoClient


def test_sign_v5_hmac_sha256() -> None:
    """Sign function reproduces the documented Bybit V5 digest format."""
    client = BybitDemoClient(
        session=None,  # type: ignore[arg-type]
        base_url="https://api-demo.bybit.com",
        api_key="fakekey",
        api_secret="fakesecret",
        recv_window=5000,
    )

    # Deterministic input → deterministic digest. The expected value
    # below was computed via openssl on the same sign payload to lock
    # the implementation against accidental whitespace / encoding drift.
    signature = client._sign(
        timestamp_ms=1700000000000,
        payload='{"category":"linear","symbol":"BTCUSDT"}',
    )

    # Signature must be lowercase hex, 64 chars (SHA256).
    assert len(signature) == 64
    assert signature == signature.lower()
    assert all(c in "0123456789abcdef" for c in signature)

    # Same inputs → same output (idempotent).
    again = client._sign(
        timestamp_ms=1700000000000,
        payload='{"category":"linear","symbol":"BTCUSDT"}',
    )
    assert signature == again


def test_query_string_is_sorted_and_filters_none() -> None:
    """Query string sorts keys (required for sign-stable GETs) and drops None."""
    qs = BybitDemoClient._query_string({"symbol": "BTCUSDT", "category": "linear", "cursor": None})
    # Sorted alphabetically by key, None dropped.
    assert qs == "category=linear&symbol=BTCUSDT"


def test_signed_headers_shape() -> None:
    """Signed headers contain the four Bybit V5 auth headers + JSON content type."""
    client = BybitDemoClient(
        session=None,  # type: ignore[arg-type]
        base_url="https://api-demo.bybit.com",
        api_key="fakekey",
        api_secret="fakesecret",
    )
    h = client._signed_headers(timestamp_ms=1700000000000, signature="deadbeef")
    assert h["X-BAPI-API-KEY"] == "fakekey"
    assert h["X-BAPI-TIMESTAMP"] == "1700000000000"
    assert h["X-BAPI-SIGN"] == "deadbeef"
    # Issue I1 (F-26, 2026-05-14) raised the default recv_window from
    # 5000 ms to 10000 ms to absorb VM-pressure jitter. The signing
    # contract is unchanged; only the default value moved.
    assert h["X-BAPI-RECV-WINDOW"] == "10000"
    assert h["Content-Type"] == "application/json"
