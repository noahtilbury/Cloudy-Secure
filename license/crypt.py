r"""Cryptographic primitives for licensing — no private key material here.

Only format validation, digest derivation, and signed-activation-token
verification live here.  The private key is never stored or transferred by
this module; it is handed to the buyer once by the developer and is then only
used ephemerally during activation.

This module intentionally does NOT contain:
* a key generator usable by buyers
* the developer's long-term signing secret
* any code that can manufacture a valid license from public inputs
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

__all__ = [
    "LicenseError",
    "LicenseKeyFormatError",
    "LicenseActivationError",
    "LicenseExpiredError",
    "LicenseRevokedError",
    "LicenseTamperedError",
    "_b64e",
    "_b64d",
    "derive_install_digest",
    "derive_install_code",
    "validate_license_key_format",
    "ActivationToken",
    "ActivationTokenError",
    "ActivationTokenExpired",
    "ActivationTokenMismatch",
    "parse_activation_token",
    "verify_activation_token",
    "create_activation_token",
]

KEY_BYTE_LENGTH = 32  # 256-bit keys


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    s = str(s)
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LicenseError(Exception):
    pass


class LicenseKeyFormatError(LicenseError, ValueError):
    pass


class LicenseActivationError(LicenseError):
    pass


class LicenseExpiredError(LicenseError):
    pass


class LicenseRevokedError(LicenseError):
    pass


class LicenseTamperedError(LicenseError):
    pass


# ---------------------------------------------------------------------------
# Key format validation
# ---------------------------------------------------------------------------


def validate_license_key_format(key: str) -> bytes:
    """Validate that *key* is a well-formed base64url 256-bit private key.

    Returns the raw key bytes.  Raises ``LicenseKeyFormatError`` if invalid.
    """
    if not key or not isinstance(key, str):
        raise LicenseKeyFormatError("license key must be a non-empty string")
    raw = key.strip()
    if not raw:
        raise LicenseKeyFormatError("license key must be non-empty")
    try:
        data = _b64d(raw)
    except Exception as exc:
        raise LicenseKeyFormatError("license key is not valid base64url") from exc
    if len(data) != KEY_BYTE_LENGTH:
        raise LicenseKeyFormatError(
            f"license key must decode to {KEY_BYTE_LENGTH} bytes (got {len(data)})"
        )
    return data


def is_valid_key_format(key: str) -> bool:
    try:
        validate_license_key_format(key)
        return True
    except LicenseError:
        return False


# ---------------------------------------------------------------------------
# Install binding
# ---------------------------------------------------------------------------


def derive_install_digest(install_id: str, key_material: bytes) -> str:
    """Public digest binding a license key to a single install."""
    if not isinstance(install_id, str) or not install_id.strip():
        raise LicenseKeyFormatError("install_id must be a non-empty string")
    return hmac.new(
        key_material, install_id.strip().encode("utf-8"), hashlib.sha256
    ).hexdigest()


def derive_install_code(install_id: str, key_material: bytes) -> str:
    """Short human-verifiable confirmation code."""
    if not isinstance(install_id, str) or not install_id.strip():
        raise LicenseKeyFormatError("install_id must be a non-empty string")
    raw = hmac.new(
        key_material,
        f"install-code-v1:{install_id.strip()}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"AUTOSEC-{raw[:6].hex().upper()}"


# ---------------------------------------------------------------------------
# Activation token — one-time-use proof
# ---------------------------------------------------------------------------


class ActivationTokenError(LicenseError):
    pass


class ActivationTokenExpired(ActivationTokenError):
    pass


class ActivationTokenMismatch(ActivationTokenError):
    pass


class ActivationToken:
    """A short-lived, signed one-time token proving the buyer has the key."""

    __slots__ = ("iss", "nonce", "issued_at", "expires_at", "signature")

    def __init__(self, *, iss, nonce, issued_at, expires_at, signature):
        self.iss = iss
        self.nonce = nonce
        self.issued_at = issued_at
        self.expires_at = expires_at
        self.signature = signature

    @classmethod
    def create(cls, issuer, nonce, *, key_material, ttl_seconds=300, now=None):
        now = now or int(time.time())
        expires_at = now + ttl_seconds
        payload = {"iss": issuer, "n": nonce, "i": now, "e": expires_at}
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        sig = hmac.new(key_material, body.encode("ascii"), hashlib.sha256).hexdigest()
        return cls(iss=issuer, nonce=nonce, issued_at=now, expires_at=expires_at, signature=sig)

    def serialize(self) -> str:
        payload = {"iss": self.iss, "n": self.nonce, "i": self.issued_at, "e": self.expires_at}
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        return f"{self.signature}.{body}"

    @classmethod
    def deserialize(cls, raw: str) -> "ActivationToken":
        if not raw or not isinstance(raw, str):
            raise ActivationTokenError("activation token must be a non-empty string")
        try:
            signature, body = raw.split(".", 1)
        except ValueError as exc:
            raise ActivationTokenError("activation token must be 'signature.body'") from exc
        if not signature or not body:
            raise ActivationTokenError("activation token parts must be non-empty")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ActivationTokenError("activation token body is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ActivationTokenError("activation token body must be a JSON object")
        try:
            iss = str(payload["iss"])
            nonce = str(payload["n"])
            issued_at = int(payload["i"])
            expires_at = int(payload["e"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ActivationTokenError("activation token body is missing required fields") from exc
        return cls(iss=iss, nonce=nonce, issued_at=issued_at, expires_at=expires_at, signature=signature)

    def verify(self, key_material: bytes) -> None:
        if int(time.time()) > self.expires_at:
            raise ActivationTokenExpired("activation token has expired")
        payload = {"iss": self.iss, "n": self.nonce, "i": self.issued_at, "e": self.expires_at}
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        expected = hmac.new(key_material, body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, self.signature):
            raise ActivationTokenMismatch("activation token signature does not match")

    def is_expired(self) -> bool:
        return int(time.time()) > self.expires_at


def parse_activation_token(raw: str) -> ActivationToken:
    return ActivationToken.deserialize(raw)


def verify_activation_token(token: ActivationToken, key_material: bytes) -> None:
    token.verify(key_material)


def create_activation_token(issuer, nonce, *, key_material, ttl_seconds=300, now=None):
    return ActivationToken.create(issuer, nonce, key_material=key_material, ttl_seconds=ttl_seconds, now=now)
