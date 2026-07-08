"""Bybit authentication helper: HMAC-SHA256 signing and credential validation.

While pybit handles signing internally, this module provides:
- Standalone signature generation for custom/raw requests
- Credential validation on startup
"""

import hashlib
import hmac
import time

from src.core.exceptions import AuthenticationError
from src.core.logging import get_logger

log = get_logger("trading")


class BybitAuth:
    """Bybit HMAC-SHA256 authentication helper.

    Args:
        api_key: Bybit API key.
        api_secret: Bybit API secret.
    """

    def __init__(self, api_key: str, api_secret: str) -> None:
        if not api_key or not api_secret:
            raise AuthenticationError(
                "Bybit API key and secret are required",
                details={"api_key_set": bool(api_key), "api_secret_set": bool(api_secret)},
            )
        self.api_key = api_key
        self.api_secret = api_secret

    def generate_signature(self, timestamp: int, recv_window: int, params: str = "") -> str:
        """Generate HMAC-SHA256 signature for Bybit V5 API.

        The signing string format is: timestamp + api_key + recv_window + params

        Args:
            timestamp: Unix milliseconds.
            recv_window: Receive window in ms.
            params: Query string or JSON body string.

        Returns:
            Hex-encoded HMAC-SHA256 signature.
        """
        sign_str = f"{timestamp}{self.api_key}{recv_window}{params}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def validate_credentials(self, session: object) -> bool:
        """Validate API credentials by making a lightweight API call.

        Calls get_wallet_balance via pybit session. Returns True if
        retCode == 0, False otherwise.

        Args:
            session: pybit.unified_trading.HTTP instance.

        Returns:
            True if credentials are valid.

        Raises:
            AuthenticationError: If authentication explicitly fails.
        """
        import asyncio

        try:
            response = await asyncio.to_thread(
                session.get_wallet_balance,  # type: ignore[union-attr]
                accountType="UNIFIED",
            )
            ret_code = response.get("retCode", -1)
            if ret_code == 0:
                log.info("Bybit credentials validated successfully")
                return True
            ret_msg = response.get("retMsg", "Unknown error")
            if ret_code in (10003, 10004, 33004):
                raise AuthenticationError(
                    f"Invalid Bybit API credentials: {ret_msg}",
                    details={"retCode": ret_code, "retMsg": ret_msg},
                )
            log.warning(
                "Credential validation returned retCode={code}: {msg}",
                code=ret_code,
                msg=ret_msg,
            )
            return False
        except AuthenticationError:
            raise
        except Exception as e:
            log.error("Credential validation failed: {err}", err=str(e))
            raise AuthenticationError(
                f"Failed to validate Bybit credentials: {e}",
                details={"error": str(e)},
            )
