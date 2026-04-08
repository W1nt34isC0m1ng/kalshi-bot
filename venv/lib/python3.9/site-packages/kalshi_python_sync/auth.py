# coding: utf-8

"""
Kalshi-specific authentication for the API client.

This module provides RSA-PSS signature-based authentication
for Kalshi's trading API.
"""

import time
import base64
from typing import cast
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.backends import default_backend


class KalshiAuth:
    """Kalshi authentication handler for API requests."""

    def __init__(self, key_id: str, private_key_pem: str):
        """Initialize Kalshi authentication.

        Args:
            key_id: Your Kalshi API key ID
            private_key_pem: Your RSA private key in PEM format (as string)
        """
        self.key_id = key_id

        # Load private key from PEM string
        private_key_bytes: bytes
        if isinstance(private_key_pem, str):
            private_key_bytes = private_key_pem.encode()
        else:
            private_key_bytes = private_key_pem  # type: ignore

        loaded_key = serialization.load_pem_private_key(
            private_key_bytes,
            password=None,
            backend=default_backend()
        )

        if not isinstance(loaded_key, RSAPrivateKey):
            raise ValueError("Private key must be an RSA key")

        self.private_key = loaded_key

    def create_auth_headers(self, method: str, url: str) -> dict:
        """Create Kalshi authentication headers for a request.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL (can be full URL or just path)

        Returns:
            Dictionary of authentication headers to add to request
        """
        current_time_milliseconds = int(time.time() * 1000)
        timestamp_str = str(current_time_milliseconds)

        # Extract path from URL
        if url.startswith('http'):
            from urllib.parse import urlparse
            parsed = urlparse(url)
            path = parsed.path
        else:
            path = url.split('?')[0]

        # Create message to sign: timestamp + method + path
        msg_string = timestamp_str + method.upper() + path

        # Sign the message using RSA-PSS
        message = msg_string.encode('utf-8')
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        signature_b64 = base64.b64encode(signature).decode('utf-8')

        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": signature_b64,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_str,
        }
