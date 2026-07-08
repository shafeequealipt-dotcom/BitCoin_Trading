"""Tests for MCP authentication."""

from src.mcp.auth import MCPAuth


class TestMCPAuth:
    def test_valid_token(self):
        auth = MCPAuth("secret123")
        assert auth.validate_token("secret123") is True

    def test_invalid_token(self):
        auth = MCPAuth("secret123")
        assert auth.validate_token("wrong") is False

    def test_empty_config_allows_all(self):
        auth = MCPAuth("")
        assert auth.validate_token("anything") is True

    def test_constant_time_comparison(self):
        auth = MCPAuth("secret123")
        # Both should work (hmac.compare_digest handles different lengths)
        assert auth.validate_token("secret123") is True
        assert auth.validate_token("x") is False
