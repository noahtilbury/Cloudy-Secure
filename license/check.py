r"""Public license check and activation API.

This is the only surface the launcher and bot use:

* ``license_check(key)`` — returns a ``LicenseVerdict`` used to decide whether
  the bot may start.
* ``license_activate(key, activation_token, install_id, install_digest)`` —
  one-time activation flow run once after the buyer pastes their key.

Single-use enforcement: the SHA-256 of the key is stored at activation time.
On every subsequent check the stored hash is compared against the supplied
key.  A different key — even one that is validly formatted — is rejected.
The key itself is never stored in plaintext.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import TypedDict

from license.crypt import (
    ActivationToken,
    LicenseActivationError,
    LicenseError,
    LicenseKeyFormatError,
    derive_install_digest,
    is_valid_key_format,
    validate_license_key_format,
)
from license.storage import ActivationState, key_sha256

from license import LICENSE_TTL_DAYS

TTL_SECONDS = int(LICENSE_TTL_DAYS * 86400)


class LicenseVerdict(TypedDict, total=False):
    valid: bool
    invalid: bool
    expired: bool
    revoked: bool
    tampered: bool
    missing: bool
    local_state_missing: bool
    key_format_invalid: bool
    key_mismatch: bool
    detail: str
    expires_in_days: float
    installed: bool
    issued_at: int | None
    expires_at: int | None
    revoked_at: int | None


def license_check(key: str | None = None) -> LicenseVerdict:
    """Validate whether the bot may start.

    *key* is optional.  On first run the buyer pastes their key and the machine
    is not yet activated; a key is required.  On subsequent launches the key is
    not re-entered and not stored; only the stored activation state is checked
    (revoked / expired / tampered / ok).

    When a key IS supplied on a subsequent launch it is verified against the
    stored SHA-256 hash — single-use binding, one key per machine.

    Returns a ``LicenseVerdict``.  The caller gates on ``verdict["valid"]``.
    """
    r: LicenseVerdict = {
        "valid": False,
        "invalid": False,
        "expired": False,
        "revoked": False,
        "tampered": False,
        "missing": False,
        "local_state_missing": False,
        "key_format_invalid": False,
        "key_mismatch": False,
        "detail": "",
        "expires_in_days": 0.0,
        "installed": False,
        "issued_at": None,
        "expires_at": None,
        "revoked_at": None,
    }

    state = ActivationState()

    # -- No activation yet: key required -------------------------------------
    if not state.is_activated():
        has_key = bool(key and isinstance(key, str) and key.strip())
        if not has_key:
            r["missing"] = True
            r["detail"] = "No license key was provided."
            return r

        key = key.strip()
        if not is_valid_key_format(key):
            r["key_format_invalid"] = True
            r["detail"] = "The license key format is invalid."
            return r

        r["local_state_missing"] = True
        r["detail"] = (
            "This machine has not been activated. "
            "Run start.bat and enter the license key when prompted."
        )
        return r

    # -- Activation exists: validate stored state ---------------------------
    if not state.verify_tamper():
        r["tampered"] = True
        r["detail"] = "Local activation state has been tampered with."
        return r

    data = state.load()
    now = int(time.time())

    # Revoked?
    revoked_at = data.get("revoked_at")
    if revoked_at is not None:
        try:
            if int(float(revoked_at)) >= now:
                r["revoked"] = True
                r["detail"] = "This license has been revoked."
                r["revoked_at"] = int(float(revoked_at))
                return r
        except (TypeError, ValueError):
            pass

    # Expired?
    expires_at = data.get("expires_at")
    if expires_at is not None:
        try:
            if int(float(expires_at)) < now:
                r["expired"] = True
                r["detail"] = "This license has expired."
                r["expires_at"] = int(float(expires_at))
                r["issued_at"] = int(float(data.get("issued_at") or 0))
                return r
        except (TypeError, ValueError):
            pass

    # Single-use key binding: if a key was supplied, it must be the same key
    # that activated this machine (compare SHA-256 hashes).
    if key and isinstance(key, str) and key.strip():
        try:
            key_bytes = validate_license_key_format(key)
            stored_hash = data.get("key_sha256", "")
            if stored_hash and not hmac.compare_digest(key_sha256(key_bytes), stored_hash):
                r["key_mismatch"] = True
                r["invalid"] = True
                r["detail"] = "This license key is not the one bound to this machine."
                return r
            # Also verify the install digest matches (machine binding).
            stored_id = data.get("install_id", "")
            stored_digest = data.get("install_digest", "")
            if stored_id and stored_digest:
                expected_digest = derive_install_digest(stored_id, key_bytes)
                if not hmac.compare_digest(expected_digest, stored_digest):
                    r["key_mismatch"] = True
                    r["invalid"] = True
                    r["detail"] = "This license key is not bound to this machine."
                    return r
        except LicenseKeyFormatError:
            r["key_format_invalid"] = True
            r["detail"] = "The license key format is invalid."
            return r

    # All clear.
    r["valid"] = True
    r["installed"] = True
    r["detail"] = "License is valid."
    try:
        remaining = max(0.0, int(float(expires_at)) - now)
    except (TypeError, ValueError):
        remaining = 0.0
    r["expires_in_days"] = round(remaining / 86400.0, 3)
    r["issued_at"] = int(float(data.get("issued_at") or 0))
    r["expires_at"] = int(float(expires_at))
    r["revoked_at"] = int(float(revoked_at)) if revoked_at else None
    return r


def license_activate(
    key: str,
    activation_token: ActivationToken,
    install_id: str,
    install_digest: str,
) -> dict[str, object]:
    """One-time activation for a newly issued key.

    Called once, right after the buyer pastes their license key for the first
    time.  Verifies the activation token, stores public state, discards the
    key.  Raises ``LicenseError`` subclasses on failure.
    """
    if not key or not isinstance(key, str) or not key.strip():
        raise LicenseKeyFormatError("No license key was provided.")

    key = key.strip()
    validate_license_key_format(key)

    if not isinstance(activation_token, ActivationToken):
        raise LicenseActivationError("activation_token must be an ActivationToken")

    state = ActivationState()
    now = int(time.time())

    state.activate(
        license_key=key,
        install_id=install_id,
        install_digest=install_digest,
        activation_token=activation_token,
        ttl_seconds=TTL_SECONDS,
        now=now,
    )

    return {
        "installed": True,
        "install_id": install_id.strip(),
        "install_digest": install_digest.strip(),
        "issued_at": now,
        "expires_at": now + TTL_SECONDS,
        "expires_in_days": LICENSE_TTL_DAYS,
    }


def build_activation_token(
    key: str,
    *,
    issuer: str = "autosecure-buyer",
    nonce: str | None = None,
    ttl_seconds: int = 300,
) -> ActivationToken:
    """Create a one-time activation token using *key* (buyer side, ephemeral).

    Runs ONCE during activation, then the key is discarded.  Does NOT let a
    buyer generate licenses — it only produces a signed proof they hold the key.
    """
    key_bytes = validate_license_key_format(key)
    if nonce is None:
        nonce = hashlib.sha256(os.urandom(32)).hexdigest()
    return ActivationToken.create(
        issuer=issuer, nonce=nonce, key_material=key_bytes, ttl_seconds=ttl_seconds
    )


def needs_activation() -> bool:
    """True when the machine has no activation state yet."""
    return not ActivationState().is_activated()
