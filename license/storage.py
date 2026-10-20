r"""Persistent, machine-local license state stored in LOCALAPPDATA\Autosecure\License.

The original license key is never stored in plaintext.  Only the SHA-256 hash
of the key, the machine install digest, timestamps and tamper nonce are held.

Single-use enforcement: the key SHA-256 hash is stored at activation time.
On every subsequent check the stored hash is compared against the supplied
key, so only the exact key that activated this machine can keep it running.
A different key — even one that is validly formatted — is rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

from license.crypt import (
    ActivationToken,
    _b64e,
    _b64d,
    validate_license_key_format,
    ActivationTokenExpired,
    ActivationTokenMismatch,
    LicenseActivationError,
)

STATE_FILE = "installed.json"
TOKEN_HASH_FILE = "activation_hashes.json"
TAMPER_FILE = "tamper_nonce.bin"


def _state_dir() -> Path:
    from license import _default_state_dir

    return _default_state_dir()


def key_sha256(key_material: bytes) -> str:
    """SHA-256 of the raw key bytes — used for single-use binding."""
    return hashlib.sha256(key_material).hexdigest()


class ActivationState:
    """Read/write access to machine-local activation state."""

    def __init__(self, directory: Path | str | None = None) -> None:
        if directory is None:
            directory = _state_dir()
        self.directory = Path(directory)
        self._ensure()

    def _ensure(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"cannot create license state directory {self.directory}: {exc}"
            ) from exc

    @property
    def _state_path(self) -> Path:
        return self.directory / STATE_FILE

    @property
    def _token_hashes_path(self) -> Path:
        return self.directory / TOKEN_HASH_FILE

    @property
    def _tamper_path(self) -> Path:
        return self.directory / TAMPER_FILE

    def load(self) -> dict[str, Any]:
        try:
            raw = self._state_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise TypeError("activation state must be a dict")
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(self._state_path)

    # ------------------------------------------------------------------
    # Token hash index (replay detection)
    # ------------------------------------------------------------------

    def _token_hashes(self) -> dict[str, int]:
        try:
            raw = self._token_hashes_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_token_hashes(self, hashes: dict[str, int]) -> None:
        tmp = self._token_hashes_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(hashes, indent=2), encoding="utf-8")
        tmp.replace(self._token_hashes_path)

    def remember_token_hash(self, token_hash: str) -> None:
        h = self._token_hashes()
        h[token_hash] = int(time.time())
        self._write_token_hashes(h)

    def has_seen_token_hash(self, token_hash: str, *, max_age_seconds: int = 86400) -> bool:
        h = self._token_hashes()
        seen = h.get(token_hash)
        if seen is None:
            return False
        return (int(time.time()) - seen) < max_age_seconds

    # ------------------------------------------------------------------
    # Activation lifecycle
    # ------------------------------------------------------------------

    def is_activated(self) -> bool:
        d = self.load()
        return bool(d.get("install_id") and d.get("install_digest"))

    def is_revoked(self) -> bool:
        d = self.load()
        v = d.get("revoked_at")
        if not v:
            return False
        try:
            return float(v) >= int(time.time())
        except (TypeError, ValueError):
            return False

    def is_expired(self) -> bool:
        d = self.load()
        v = d.get("expires_at")
        if not v:
            return False
        try:
            return float(v) < int(time.time())
        except (TypeError, ValueError):
            return False

    def activated_install_id(self) -> str:
        return str(self.load().get("install_id") or "")

    def activated_install_digest(self) -> str:
        return str(self.load().get("install_digest") or "")

    def activated_key_sha256(self) -> str:
        return str(self.load().get("key_sha256") or "")

    def activated_expires_at(self) -> int | None:
        v = self.load().get("expires_at")
        try:
            return int(float(v)) if v else None
        except (TypeError, ValueError):
            return None

    def activated_revoked_at(self) -> int | None:
        v = self.load().get("revoked_at")
        try:
            return int(float(v)) if v else None
        except (TypeError, ValueError):
            return None

    def activated_issued_at(self) -> int | None:
        v = self.load().get("issued_at")
        try:
            return int(float(v)) if v else None
        except (TypeError, ValueError):
            return None

    def activate(
        self,
        license_key: str,
        install_id: str,
        install_digest: str,
        activation_token: ActivationToken,
        *,
        ttl_seconds: int,
        now: int | None = None,
    ) -> dict[str, Any]:
        """One-time activation — stores state, discards the key.

        Stores the SHA-256 of the key for single-use binding: only this exact
        key can keep the machine activated on every subsequent launch.
        """
        key_bytes = validate_license_key_format(license_key)

        try:
            activation_token.verify(key_bytes)
        except ActivationTokenExpired as exc:
            raise LicenseActivationError("activation token expired") from exc
        except ActivationTokenMismatch as exc:
            raise LicenseActivationError("activation token does not match the provided key") from exc

        token_hash = hashlib.sha256(activation_token.serialize().encode("ascii")).hexdigest()
        if self.has_seen_token_hash(token_hash):
            raise LicenseActivationError("this activation token has already been used")

        now = now or int(time.time())
        data: dict[str, Any] = {
            "install_id": install_id.strip(),
            "install_digest": install_digest.strip(),
            "key_sha256": key_sha256(key_bytes),
            "issued_at": now,
            "expires_at": now + ttl_seconds,
            "revoked_at": None,
            "tamper_nonce": _b64e(os.urandom(16)),
            "last_token_hash": token_hash,
            "last_activation_at": now,
        }
        self.save(data)
        self.remember_token_hash(token_hash)
        self._tamper_path.write_bytes(_b64d(data["tamper_nonce"]))

        del key_bytes
        del activation_token
        return data

    def revoke(self) -> None:
        d = self.load()
        d["revoked_at"] = int(time.time())
        self.save(d)

    def clear(self) -> None:
        """Wipe all local activation state (buyer-initiated reset)."""
        for p in (self._state_path, self._token_hashes_path, self._tamper_path):
            try:
                p.unlink()
            except (FileNotFoundError, OSError):
                pass

    def tamper_nonce(self) -> bytes:
        try:
            return self._tamper_path.read_bytes()
        except FileNotFoundError:
            raw = self.load().get("tamper_nonce", "")
            if raw:
                try:
                    return _b64d(raw)
                except Exception:
                    return b""
            return b""

    def verify_tamper(self) -> bool:
        """Return True if local state appears untampered."""
        stored = self.tamper_nonce()
        if not stored:
            return True
        json_nonce = self.load().get("tamper_nonce", "")
        try:
            json_nonce_bytes = _b64d(json_nonce) if json_nonce else b""
        except Exception:
            json_nonce_bytes = b""
        return hmac.compare_digest(stored, json_nonce_bytes)


# ---------------------------------------------------------------------------
# High-level helpers
# ---------------------------------------------------------------------------


def get_activation_state() -> dict[str, Any]:
    """Public read-only snapshot of local activation state (no secrets)."""
    state = ActivationState()
    return {
        "activated": state.is_activated(),
        "revoked": state.is_revoked(),
        "expired": state.is_expired(),
        "install_id": state.activated_install_id(),
        "install_digest": state.activated_install_digest(),
        "issued_at": state.activated_issued_at(),
        "expires_at": state.activated_expires_at(),
        "revoked_at": state.activated_revoked_at(),
        "tamper_ok": state.verify_tamper(),
    }


def reset_activation() -> None:
    """Wipe local activation state."""
    ActivationState().clear()
