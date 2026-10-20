r"""Licensing backing for sunnysecure.

This package is distributed with the bot.  It contains NO private signing
keys, no key-generation secrets, and no ability for a buyer to manufacture a
valid license.  Its only job is:

* accept a license key string the buyer was issued by the developer
* validate it against machine-local activation state stored in
  LOCALAPPDATA\Autosecure\License
* gate startup before the Discord client ever connects

Developer-only tooling lives in dev_keygen.py at the repository root — that
file is never installed into the buyer's virtual environment and never imported
by start.bat or bot.py.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Configurable TTL — honours AUTOSECURE_LICENSE_TTL_DAYS.  Default 7 days.
# ---------------------------------------------------------------------------
_DEF = os.environ.get("AUTOSECURE_LICENSE_TTL_DAYS")
if _DEF is None:
    LICENSE_TTL_DAYS: float = 7.0
else:
    LICENSE_TTL_DAYS = float(_DEF)
if LICENSE_TTL_DAYS < 1:
    raise RuntimeError("AUTOSECURE_LICENSE_TTL_DAYS must be >= 1 (days)")


def _default_state_dir() -> Path:
    env = os.environ.get("AUTOSECURE_LICENSE_DIR", "")
    if env:
        return Path(env)
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        return Path(localappdata) / "Autosecure" / "License"
    return Path.home() / ".autosecure" / "license"


STATE_DIR: Path = _default_state_dir()

__all__ = [
    "LICENSE_TTL_DAYS",
    "STATE_DIR",
    "get_license_state_dir",
    "LicenseVerdict",
    "license_check",
    "license_activate",
    "get_activation_state",
    "reset_activation",
    "LicenseError",
    "LicenseKeyFormatError",
    "LicenseActivationError",
    "LicenseExpiredError",
    "LicenseRevokedError",
    "LicenseTamperedError",
]

from license.crypt import (
    LicenseError,
    LicenseKeyFormatError,
    _b64e,
    _b64d,
    derive_install_digest,
    derive_install_code,
)
from license.storage import (
    ActivationState,
    get_activation_state,
    reset_activation,
)
from license.check import (
    LicenseVerdict,
    license_activate,
    license_check,
)


def get_license_state_dir() -> Path:
    """Return the directory where activation state is stored."""
    return STATE_DIR.resolve()
