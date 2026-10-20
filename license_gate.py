"""License gate used by start.bat and bot.py.

This module is the single place the launcher/bot talks to the licensing
package.  It hides the activation flow, the storage paths, and the verdict
details behind a small CLI and programmatic API.

Command line usage (called from start.bat):

    python license_gate.py check --key "<paste>"        # prints verdict JSON
    python license_gate.py activate --key "<paste>"     # one-time activation
    python license_gate.py reset                        # wipe local state
    python license_gate.py status                       # print current state

Programmatic usage (imported by bot.py):

    from license_gate import (
        gate_check,        # (key_str) -> dict
        gate_activate,     # one-time activation
        gate_status,       # read-only current state
        GATE_REQUIRED,     # bool (always True here; can be toggled by env for testing)
        GateError,
    )
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from license import STATE_DIR, LICENSE_TTL_DAYS
from license.check import (
    LicenseVerdict,
    license_activate,
    license_check,
    needs_activation,
    build_activation_token,
    ActivationToken,
)
from license.crypt import (
    ActivationTokenError,
    ActivationTokenExpired,
    ActivationTokenMismatch,
    LicenseActivationError,
    LicenseError,
    LicenseKeyFormatError,
    LicenseTamperedError,
    validate_license_key_format,
    derive_install_digest,
)
from license.storage import ActivationState

# ---------------------------------------------------------------------------
# Global switch: set AUTOSECURE_LICENSE_GATE=0 to disable the gate for
# developer testing.  Buyers never touch this.
# ---------------------------------------------------------------------------
_GATE_ENABLED = os.environ.get("AUTOSECURE_LICENSE_GATE", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)


# ---------------------------------------------------------------------------
# Public programmatic API
# ---------------------------------------------------------------------------

class GateError(Exception):
    """Raised when the license gate refuses to proceed."""


def gate_check(key: str | None = None) -> dict[str, Any]:
    """Check the license and return a machine-readable verdict.

    Raises ``GateError`` when the bot may NOT start.  Returns the verdict dict
    when the bot may start.

    *key* is the license key string the buyer was issued.  On first run it is
    required; on subsequent launches it is optional (the stored activation is
    validated instead).
    """
    if not _GATE_ENABLED:
        return {
            "valid": True,
            "detail": "License gate disabled (AUTOSECURE_LICENSE_GATE=0).",
            "installed": False,
        }

    verdict: LicenseVerdict = license_check(key)

    if verdict.get("valid"):
        return dict(verdict)

    raise GateError(json.dumps({
        "verdict": verdict,
        "message": f"Bot start blocked: {verdict.get('detail', 'unknown reason')}",
    }, indent=2))


def gate_activate(key: str, *, nonce: str | None = None, install_id: str | None = None) -> dict[str, Any]:
    """One-time activation.  Use only on first run after the buyer pastes the key.

    On first run the buyer has the key, so we can build the activation token
    locally and activate immediately.  The install_digest is derived from the
    key itself (the same way dev_keygen derives it), so the stored binding is
    consistent with what the developer issued.

    In a server-gated deployment this function would be replaced by a call to
    your licensing server; see docs for that path.

    Returns the activation record.  Raises ``GateError`` on failure.
    """
    if not _GATE_ENABLED:
        return {
            "activated": False,
            "detail": "License gate disabled — no activation performed.",
        }

    if not needs_activation():
        raise GateError("This machine is already activated.  Use 'status' to inspect.")

    # Derive the install binding from the key the buyer just pasted.  This must
    # match what the developer computed with the same key + install_id.
    key_bytes = validate_license_key_format(key)
    iid = install_id or _current_install_id()
    digest = derive_install_digest(iid, key_bytes)

    token = build_activation_token(key, nonce=nonce)
    return license_activate(
        key,
        activation_token=token,
        install_id=iid,
        install_digest=digest,
    )


def gate_status() -> dict[str, Any]:
    """Read-only current activation state (no secrets)."""
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
        "ttl_days": LICENSE_TTL_DAYS,
        "state_dir": str(STATE_DIR),
    }


def gate_reset() -> None:
    """Wipe local activation state (buyer-initiated reset)."""
    from license.storage import reset_activation

    reset_activation()


# ---------------------------------------------------------------------------
# Install-id / install-digest derivation (developer-issued key used once)
# ---------------------------------------------------------------------------

def _current_install_id() -> str:
    """Return the install_id that should be bound to this machine.

    By default this is a machine fingerprint so a key issued for one buyer
    cannot be activated on another machine.  In a server-gated deployment this
    would be chosen by the server.  Here we derive a stable id from the
    machine's disk serials + MACs so it survives reinstalls on the same
    hardware, but a fresh hardware combo yields a new id (which is fine:
    the developer issues a key per machine anyway).
    """
    import hashlib
    import platform
    from pathlib import Path

    parts: list[bytes] = []

    def _safe_read(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError:
            return b""

    # Windows: use volume serial + MAC-based stable id.
    if sys.platform == "win32":
        try:
            import uuid

            macs: list[bytes] = []
            for iface in uuid.getnode(),:
                macs.append(iface.to_bytes(6, "big"))
            parts.append(b"mac:" + b"|".join(macs))
        except Exception:
            pass
        try:
            # Volume serial of the system drive.
            import subprocess

            out = subprocess.run(
                ["cmd", "/c", "wmic", "logicaldisk", "where", "DeviceID='C:'",
                 "get", "VolumeSerialNumber", "/value"],
                capture_output=True, text=True, timeout=10,
            ).stdout or ""
            for line in out.splitlines():
                if line.strip().lower().startswith("volumeserialnumber="):
                    parts.append(b"vol:" + line.split("=", 1)[1].strip().encode())
                    break
        except Exception:
            pass
        try:
            # Machine SID-ish fallback.
            parts.append(b"os:" + platform.node().encode())
        except Exception:
            pass
    else:
        parts.append(b"os:" + platform.node().encode())

    raw = hashlib.sha256(b"|".join(parts) if parts else b"autosecure-fallback").hexdigest()
    return f"machine-{raw[:16]}"


def _current_install_digest_from_state() -> str:
    """Return the stored install_digest, or a placeholder if not yet activated.

    Retained for reference; the activate path now derives the digest from the
    key the buyer pasted (see gate_activate).
    """
    state = ActivationState()
    existing = state.activated_install_digest()
    if existing:
        return existing
    return ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> int:
    p = argparse.ArgumentParser(
        prog="license_gate.py",
        description="License check / activation gate for sunnysecure.",
    )
    sub = p.add_subparsers(dest="command")

    cp = sub.add_parser("check", help="Check a license key and print verdict JSON.")
    cp.add_argument("--key", default=None, help="License key string (or read from stdin if omitted).")

    ap = sub.add_parser("activate", help="One-time activation (first run only).")
    ap.add_argument("--key", default=None, help="License key string (or read from stdin if omitted).")
    ap.add_argument("--nonce", default=None, help="Optional activation nonce.")

    sp = sub.add_parser("status", help="Print current activation state (no secrets).")
    rp = sub.add_parser("reset", help="Wipe local activation state.")

    args = p.parse_args()

    def _read_key() -> str:
        # ``--key ""`` is a legitimate invocation (subsequent runs: validate the
        # stored activation, no key required).  Distinguish "flag omitted"
        # (None -> read stdin) from "flag passed as empty string" ("" -> use it),
        # otherwise an empty key would block forever on stdin.
        if args.key is not None:
            return args.key.strip()
        return sys.stdin.read().strip()

    if args.command == "check":
        key = _read_key()
        try:
            verdict = gate_check(key)
            print(json.dumps(verdict, indent=2, default=str))
            return 0
        except GateError as exc:
            print(json.dumps({"blocked": True, "error": str(exc)}, indent=2), file=sys.stderr)
            return 1
    elif args.command == "activate":
        key = _read_key()
        try:
            record = gate_activate(key, nonce=args.nonce)
            print(json.dumps(record, indent=2, default=str))
            return 0
        except GateError as exc:
            print(json.dumps({"blocked": True, "error": str(exc)}, indent=2), file=sys.stderr)
            return 1
        except LicenseError as exc:
            print(json.dumps({"blocked": True, "error": f"activation failed: {exc}"}, indent=2), file=sys.stderr)
            return 1
    elif args.command == "status":
        status = gate_status()
        print(json.dumps(status, indent=2, default=str))
        return 0 if status.get("activated") else 1
    elif args.command == "reset":
        gate_reset()
        print(json.dumps({"reset": True, "state_dir": str(STATE_DIR)}, indent=2))
        return 0
    else:
        p.print_help()
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
