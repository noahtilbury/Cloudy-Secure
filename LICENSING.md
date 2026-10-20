# Licensing — sunnysecure

## Overview

sunnysecure uses a **one-time-install activation** model with a 7-day license
TTL (configurable).  The developer issues each buyer a private license key
once; the buyer activates their machine by pasting that key when they first
run `start.bat`.  After activation, the bot validates the stored activation
state on every launch and refuses to connect to Discord if the license is
invalid, expired, revoked, or tampered with.

**The private signing keys and the key-generation tool never ship to buyers.**
They live only on the developer's machine (or a private licensing server).

## What ships to buyers

- `start.bat` — launcher that installs prerequisites, prompts for the key on
  first run, and gates the bot start.
- `bot.py` — the Discord bot, which re-validates the license before
  `bot.start()` connects.
- `license/` — the public licensing package (NO private keys, NO key generator).
- `license_gate.py` — the launcher/bot gate module (NO private keys).
- `requirements.txt`, `config/`, `cogs/`, etc. — the rest of the bot.

## What does NOT ship to buyers

- `dev_keygen.py` — the developer-only license generator (excluded by `.gitignore`
  and by not being imported anywhere in the distributed code).
- Any file containing a raw private license key.
- The buyer's activation state is stored on their machine, not in the project.

## Architecture

### Cryptography

- Algorithm: **HMAC-SHA256**, 256-bit (32-byte) keys, base64url-encoded.
- Each license key is a random 32-byte value, base64url-encoded to a 43-char
  string (e.g. `kTB2gwazt7wT9t4U5GAvmsgmqeQ35Udb_bDLyk9LCNo`).
- The **install digest** binds a key to a specific `install_id` (machine):
  `HMAC-SHA256(key_bytes, install_id)`.  Without the key, an attacker cannot
  forge a matching digest for a different machine.
- The **install code** is a short human-verifiable confirmation code
  (`AUTOSEC-XXXXXX`) the buyer reports back on first activation.

### Activation flow (buyer side)

1. Buyer runs `start.bat` for the first time.
2. `start.bat` detects no activation state in `%LOCALAPPDATA%\Autosecure\License`.
3. Buyer is prompted to paste their license key.
4. `license_gate.py activate` builds an ephemeral **activation token** signed
   with the key, derives the install digest from the key + machine install_id,
   stores public state (`installed.json`), and discards the key.
5. On every subsequent launch, `license_check()` validates the stored state
   (revoked / expired / tampered / ok).  The key is NOT required again.

### Storage (buyer's machine)

- Directory: `%LOCALAPPDATA%\Autosecure\License` (default; override with
  `AUTOSECURE_LICENSE_DIR`, but never redirect back into the project folder).
- Files:
  - `installed.json` — public activation record (install_id, install_digest,
    issued_at, expires_at, revoked_at, tamper_nonce, last_token_hash).
  - `activation_hashes.json` — hashes of used activation tokens (replay
    detection; NOT the tokens themselves).
  - `tamper_nonce.bin` — 16 random bytes for tamper detection.
- The original license key is **never stored on disk**.  It is held ephemerally
  in memory during activation and then discarded.

### TTL

- Default: **7 days**.
- Configurable via `AUTOSECURE_LICENSE_TTL_DAYS` environment variable (days).
- Set in `start.bat` (hardcoded `7` by default, overridable via the env var
  before repacking) and honored by `license/__init__.py` and `license/check.py`.

## Developer workflow

### Issue a license for a buyer

```
python dev_keygen.py --install-id <unique-id> [--seed <hex>] --output issued.json
```

- `--install-id`: a unique identifier for the buyer's machine (e.g. a UUID you
  generate, or the machine fingerprint the buyer sends you).  If you don't have
  one, use a placeholder and let `license_gate.activate` derive the machine id
  automatically on first run (it uses a hardware fingerprint).
- `--seed` (optional): a deterministic 32-byte seed so the same install_id
  always yields the same key.  Useful if you want to re-issue the same key.
- `--output`: writes the full issued license JSON (contains the private key!)
  to a file.  Delete this file when done.

The tool prints a summary to stderr showing:
- `install_id` — send to buyer
- `license_key` — send to buyer (PRIVATE — never share the issued.json file)
- `install_code` — buyer confirms this on first run
- `expires_at` — when the license expires

### Send the buyer

Send the buyer:
1. `license_key` — the private key (paste it into a secure channel; don't email
   it in plaintext if you can avoid it).
2. `install_id` — the public install id (so their activation is bound to the
   right machine).
3. `install_code` — optional confirmation code.

### Revoke a license

Edit the buyer's activation state on their machine (if you have access) or wait
for TTL expiry.  For server-side revocation, see "Server-side gating" below.

## Buyer install flow

1. Unzip the distribution to a folder.
2. Double-click `start.bat`.
3. `start.bat` installs Python (if needed), creates a virtualenv, installs
   `requirements.txt`.
4. If `config/config.json` is missing, it's copied from the example and the
   launcher pauses for the buyer to fill in their bot token and owner ID.
5. If no activation state exists, the buyer is prompted to paste their license
   key.
6. On success, the bot starts.  On failure, the bot does not connect and the
   launcher prints the reason and exits.

## Server-side gating (optional, recommended for production)

The current implementation is **client-side**: the buyer's machine holds the
activation state and the bot checks it locally.  This is fine for a 7-day TTL
and revocation via local state edits, but a determined attacker with admin
access to their machine can modify `installed.json` to extend expiry or fake
a valid state.

For stronger control, replace `license_gate.activate` and `license_gate.check`
with calls to your own licensing server:

- **Activation**: buyer pastes key → your server verifies the key format, derives
  the install digest, records the activation (install_id, key_hash, issued_at,
  expires_at, revoked), and returns an activation token or confirmation.
- **Validation**: on every launch, the bot calls your server with the stored
  install_id and gets back `{valid, expired, revoked, tamper}`.
- **Revocation**: you set `revoked=true` on the server; the next launch the bot
  gets `revoked` and refuses to start.

The cryptographic design (HMAC-SHA256, per-install digests, one-time activation
tokens) supports this cleanly: the server holds the private keys and the buyer
only ever sees public-form data.

## Limitations (honest Assessment)

1. **Client-side state is inspectable.**  A buyer with admin access can read
   `installed.json` and see the install_id, install_digest, timestamps, and
   tamper_nonce.  They cannot forge a valid state without the private key (the
   digest is HMAC-SHA256 under the key), but they CAN:
   - edit `expires_at` to a future date (the bot checks it, but a modified file
     bypasses that check).
   - delete `tamper_nonce.bin` and `installed.json` to force a re-activation
     prompt (they still need a valid key to get past it).
   - copy `installed.json` from one machine to another — but the install_digest
     won't match the other machine's install_id, so the bot rejects it.
2. **No online check by default.**  The bot does not phone home.  revocation
   and expiry are enforced only by local state.  For revocation to take effect,
   the buyer must re-run `start.bat` (which re-checks local state) or you must
   use server-side gating.
3. **TTL is short (7 days) by design.**  A short TTL means buyers must renew
   frequently, which is the point.  If you want longer TTL, set
   `AUTOSECURE_LICENSE_TTL_DAYS` before repacking.  Don't make it too long or
   the renewal cycle loses its teeth.
4. **The activation token is one-time but not server-validated.**  The token
   replay detection is local (token hashes).  A buyer could extract the token
   from memory during activation and replay it on another machine before the
   first activation completes — but the install_digest binding prevents using
   it on a different machine.
5. **`dev_keygen.py` must stay off the distributed build.**  If you accidentally
   include it, buyers can generate their own keys.  The `.gitignore` and the
   fact that nothing imports it help, but verify your distribution zip before
   sending it to buyers.

## Files

- `dev_keygen.py` — developer-only license generator.
- `license/__init__.py` — public package init; re-exports non-secret API.
- `license/crypt.py` — HMAC-SHA256 helpers, key format validation, activation
  token create/verify.  NO private keys.
- `license/storage.py` — `ActivationState`: read/write machine-local activation
  state in `%LOCALAPPDATA%\Autosecure\License`.  NO private keys stored.
- `license/check.py` — `license_check(key)` and `license_activate(...)`: the
  public check/activation API.  NO private keys.
- `license_gate.py` — launcher/bot gate: `gate_check(key)`, `gate_activate(key)`,
  `gate_status()`, `gate_reset()`.  CLI via `python license_gate.py check|activate|status|reset`.
- `start.bat` — launcher: prerequisites, config check, license prompt/gate,
  bot start.
- `bot.py` — calls `gate_check("")` before `bot.start()`.
- `.gitignore` — excludes `dev_keygen.py`, `issued_license*.json`, and license
  state files from version control.

## Environment variables

- `AUTOSECURE_LICENSE_TTL_DAYS` — TTL in days (default 7, must be >= 1).
- `AUTOSECURE_LICENSE_DIR` — override the activation state directory (default
  `%LOCALAPPDATA%\Autosecure\License`).  NEVER set this to a path inside the
  project folder.
- `AUTOSECURE_LICENSE_GATE` — set to `0` to disable the gate for developer
  testing (buyers never touch this).
