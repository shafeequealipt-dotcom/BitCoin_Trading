"""Phase 6 — Brain CLI credential pre-flight tests.

Verifies the new ``_get_credential_expiry_seconds`` and
``_ensure_credentials_fresh`` helpers added in
``src/brain/claude_code_client.py``.

The helpers exist so brain calls don't race the OAuth expiry boundary.
Pre-Layer-1 observation showed silent 90 s subprocess hangs when a
call was issued within a few minutes of credential expiry — the CLI
attempts mid-call refresh and stalls. The pre-flight check refreshes
the token BEFORE the subprocess spawn so the call itself sees a fresh
credential.

See ``dev_notes/phase0_issue_brain_credential.md`` and
``dev_notes/phase6_brain_credential_report.md``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.brain import claude_code_client as ccc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_creds_file(tmp_path: Path):
    """A credentials file with 1 hour of TTL."""
    creds_path = tmp_path / "credentials.json"
    expires_ms = int((time.time() + 3600) * 1000)
    creds_path.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "fresh-token",
            "refreshToken": "fresh-refresh",
            "expiresAt": expires_ms,
            "subscriptionType": "max",
        }
    }))
    return creds_path


@pytest.fixture
def near_expiry_creds_file(tmp_path: Path):
    """A credentials file with 60 seconds of TTL — under threshold."""
    creds_path = tmp_path / "credentials.json"
    expires_ms = int((time.time() + 60) * 1000)
    creds_path.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "near-expiry-token",
            "refreshToken": "old-refresh",
            "expiresAt": expires_ms,
            "subscriptionType": "max",
        }
    }))
    return creds_path


@pytest.fixture
def expired_creds_file(tmp_path: Path):
    """A credentials file already past expiry."""
    creds_path = tmp_path / "credentials.json"
    expires_ms = int((time.time() - 60) * 1000)
    creds_path.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "expired-token",
            "refreshToken": "old-refresh",
            "expiresAt": expires_ms,
        }
    }))
    return creds_path


@pytest.fixture
def make_client():
    """Factory that builds a ClaudeCodeClient with a patched credentials path
    and a stubbed _try_token_refresh, skipping the subprocess + diagnostics
    tax in __init__.
    """
    def _build(creds_path: Path, refresh_succeeds: bool = True):
        with patch.object(ccc, "_CREDENTIAL_PATH", creds_path), \
             patch.object(ccc.ClaudeCodeClient, "_log_diagnostics", lambda self: None), \
             patch.object(ccc.ClaudeCodeClient, "_validate_setup", lambda self: None), \
             patch.object(ccc.ClaudeCodeClient, "_find_claude", lambda self: "/usr/bin/claude"), \
             patch.object(ccc.ClaudeCodeClient, "_build_env", lambda self: {}):
            client = ccc.ClaudeCodeClient(timeout_seconds=30)
        # Patch the credential path on the instance scope as well — the helpers
        # read the module-level constant.
        client._refresh_calls = 0

        def _fake_refresh() -> bool:
            client._refresh_calls += 1
            if refresh_succeeds:
                # Bump expiry by 1h, like the real refresh would.
                creds = json.loads(creds_path.read_text())
                creds["claudeAiOauth"]["expiresAt"] = int(
                    (time.time() + 3600) * 1000
                )
                creds_path.write_text(json.dumps(creds))
                return True
            return False

        client._try_token_refresh = _fake_refresh  # type: ignore[method-assign]
        return client
    return _build


# ---------------------------------------------------------------------------
# _get_credential_expiry_seconds
# ---------------------------------------------------------------------------


class TestGetExpirySeconds:
    def test_returns_positive_for_future_expiry(
        self, fresh_creds_file, make_client
    ) -> None:
        client = make_client(fresh_creds_file)
        with patch.object(ccc, "_CREDENTIAL_PATH", fresh_creds_file):
            ttl = client._get_credential_expiry_seconds()
        assert ttl is not None
        # ~1h ahead, so 3590-3600s
        assert 3500 < ttl <= 3600

    def test_returns_negative_for_expired(
        self, expired_creds_file, make_client
    ) -> None:
        client = make_client(expired_creds_file)
        with patch.object(ccc, "_CREDENTIAL_PATH", expired_creds_file):
            ttl = client._get_credential_expiry_seconds()
        assert ttl is not None
        assert ttl < 0

    def test_returns_none_for_missing_file(
        self, tmp_path, fresh_creds_file, make_client
    ) -> None:
        client = make_client(fresh_creds_file)
        missing = tmp_path / "does_not_exist.json"
        with patch.object(ccc, "_CREDENTIAL_PATH", missing):
            ttl = client._get_credential_expiry_seconds()
        assert ttl is None

    def test_returns_none_for_malformed_json(
        self, tmp_path, fresh_creds_file, make_client
    ) -> None:
        client = make_client(fresh_creds_file)
        bad = tmp_path / "broken.json"
        bad.write_text("{not json")
        with patch.object(ccc, "_CREDENTIAL_PATH", bad):
            ttl = client._get_credential_expiry_seconds()
        assert ttl is None


# ---------------------------------------------------------------------------
# _ensure_credentials_fresh
# ---------------------------------------------------------------------------


class TestEnsureCredentialsFresh:
    def test_fresh_credentials_no_refresh(
        self, fresh_creds_file, make_client
    ) -> None:
        """1h TTL > 5min threshold → no refresh."""
        client = make_client(fresh_creds_file)
        with patch.object(ccc, "_CREDENTIAL_PATH", fresh_creds_file):
            ok = client._ensure_credentials_fresh(min_remaining_seconds=300)
        assert ok is True
        assert client._refresh_calls == 0

    def test_near_expiry_triggers_refresh(
        self, near_expiry_creds_file, make_client
    ) -> None:
        """60s TTL < 5min threshold → refresh fires."""
        client = make_client(near_expiry_creds_file, refresh_succeeds=True)
        with patch.object(ccc, "_CREDENTIAL_PATH", near_expiry_creds_file):
            ok = client._ensure_credentials_fresh(min_remaining_seconds=300)
        assert ok is True
        assert client._refresh_calls == 1

    def test_failed_refresh_inside_margin_raises(
        self, near_expiry_creds_file, make_client
    ) -> None:
        """Phase 3 (Brain credentials): refresh failure INSIDE the margin
        now raises CredentialRefreshError so the caller aborts before
        spawning a doomed subprocess. Was: returned False and let the
        caller proceed."""
        from src.core.exceptions import CredentialRefreshError

        client = make_client(near_expiry_creds_file, refresh_succeeds=False)
        # Patch sleep so the 3-attempt backoff ladder doesn't slow the test.
        with patch.object(ccc, "_CREDENTIAL_PATH", near_expiry_creds_file), \
             patch.object(ccc.time, "sleep"):
            with pytest.raises(CredentialRefreshError):
                client._ensure_credentials_fresh(min_remaining_seconds=300)
        # 3 attempts (the new default) — was 1.
        assert client._refresh_calls == client._credential_refresh_max_attempts

    def test_missing_creds_file_no_refresh_no_error(
        self, tmp_path, fresh_creds_file, make_client
    ) -> None:
        """If the file is gone, the helper returns True (let the live path
        surface the issue) and does not attempt a refresh."""
        client = make_client(fresh_creds_file)
        missing = tmp_path / "does_not_exist.json"
        with patch.object(ccc, "_CREDENTIAL_PATH", missing):
            ok = client._ensure_credentials_fresh(min_remaining_seconds=300)
        assert ok is True
        assert client._refresh_calls == 0


# ---------------------------------------------------------------------------
# Heartbeat attributes
# ---------------------------------------------------------------------------


class TestHeartbeatAttributes:
    def test_attributes_exist_at_construction(
        self, fresh_creds_file, make_client
    ) -> None:
        """The Phase 6 fix adds ``_last_call_attempt_time`` and
        ``_last_response_time``. The watchdog reads these.
        """
        client = make_client(fresh_creds_file)
        assert hasattr(client, "_last_call_attempt_time")
        assert hasattr(client, "_last_response_time")
        assert hasattr(client, "_last_call_time")  # legacy alias still there
        # All three start populated (assume-healthy at construction).
        assert client._last_call_attempt_time > 0
        assert client._last_response_time > 0
        assert client._last_call_time > 0
