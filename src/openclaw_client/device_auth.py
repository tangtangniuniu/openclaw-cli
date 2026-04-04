from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


DEFAULT_IDENTITY_PATH = Path.home() / ".openclaw" / "identity" / "device.json"
DEFAULT_DEVICE_AUTH_PATH = Path.home() / ".openclaw" / "identity" / "device-auth.json"


@dataclass(slots=True)
class DeviceIdentity:
    device_id: str
    public_key: str
    private_key_pem: str

    @classmethod
    def load(cls, path: Path = DEFAULT_IDENTITY_PATH) -> DeviceIdentity:
        raw = json.loads(path.read_text())
        public_key_pem = raw.get("publicKeyPem", "").encode()
        private_key_pem = raw.get("privateKeyPem", "")
        device_id = raw.get("deviceId")
        if not device_id or not public_key_pem or not private_key_pem:
            raise ValueError(f"Invalid device identity file: {path}")

        public_key = serialization.load_pem_public_key(public_key_pem)
        public_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        derived_device_id = hashlib.sha256(public_bytes).hexdigest()
        if device_id != derived_device_id:
            raise ValueError(f"Device identity fingerprint mismatch in {path}")

        return cls(
            device_id=device_id,
            public_key=_b64url(public_bytes),
            private_key_pem=private_key_pem,
        )

    def sign(self, payload: str) -> str:
        private_key = serialization.load_pem_private_key(
            self.private_key_pem.encode(),
            password=None,
        )
        if not isinstance(private_key, ed25519.Ed25519PrivateKey):
            raise ValueError("Unsupported private key type; expected Ed25519.")
        return _b64url(private_key.sign(payload.encode()))


def load_operator_token(path: Path = DEFAULT_DEVICE_AUTH_PATH) -> str | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    tokens: dict[str, Any] = raw.get("tokens", {})
    operator = tokens.get("operator", {})
    token = operator.get("token")
    return token if isinstance(token, str) and token else None


def build_signed_device(
    identity: DeviceIdentity,
    *,
    nonce: str,
    signed_at: int,
    client_id: str,
    client_mode: str,
    platform: str,
    role: str,
    scopes: list[str],
    signing_secret: str,
) -> dict[str, Any]:
    candidates = [
        _build_v2_payload(
            identity.device_id,
            client_id=client_id,
            client_mode=client_mode,
            role=role,
            scopes=scopes,
            signed_at=signed_at,
            signing_secret=signing_secret,
            nonce=nonce,
        )
    ]

    return {
        "id": identity.device_id,
        "publicKey": identity.public_key,
        "signatureCandidates": [identity.sign(payload) for payload in candidates],
        "signedAt": signed_at,
        "nonce": nonce,
    }


def _build_v2_payload(
    device_id: str,
    *,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: list[str],
    signed_at: int,
    signing_secret: str,
    nonce: str,
) -> str:
    return "|".join(
        [
            "v2",
            device_id,
            client_id,
            client_mode,
            role,
            ",".join(scopes),
            str(signed_at),
            signing_secret,
            nonce,
        ]
    )
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
