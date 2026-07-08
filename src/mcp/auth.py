"""Token authentication for SSE transport.

Uses constant-time comparison to prevent timing attacks.
"""

import hmac


class MCPAuth:
    """Token-based authentication for the SSE MCP transport.

    Args:
        token: Expected bearer token.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    def validate_token(self, provided_token: str) -> bool:
        """Validate a token using constant-time comparison.

        Args:
            provided_token: Token from the request.

        Returns:
            True if valid.
        """
        if not self._token:
            return True  # No token configured = no auth required
        return hmac.compare_digest(self._token, provided_token)

    def extract_token(self, request) -> str | None:
        """Extract bearer token from request headers or query params.

        Args:
            request: Starlette request object.

        Returns:
            Token string or None.
        """
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
        return request.query_params.get("token")
