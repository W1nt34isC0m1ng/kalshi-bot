from __future__ import annotations

import base64
import pathlib
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class KalshiSigner:
    def __init__(self, private_key_path: str, api_key_id: str):
        self.api_key_id = api_key_id
        key_bytes = pathlib.Path(private_key_path).read_bytes()
        self.private_key: rsa.RSAPrivateKey = serialization.load_pem_private_key(
            key_bytes,
            password=None,
            backend=default_backend(),
        )

    def sign(self, timestamp_ms: str, method: str, path: str) -> str:
        message = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")
