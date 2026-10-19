import asyncio
import logging
import re

import httpx

from minecraft.retry import TransientMCError, with_retries

# Minecraft Java names: 3-16 characters of [A-Za-z0-9_].
JAVA_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")

log = logging.getLogger(__name__)


async def _get_profile_once(ssid: str) -> dict | None:
    async with httpx.AsyncClient(timeout=30.0) as session:
        response = await session.get(
            url="https://api.minecraftservices.com/minecraft/profile",
            headers={"Authorization": f"Bearer {ssid}"},
        )

        if response.status_code in (408, 425, 429, 500, 502, 503, 504):
            raise TransientMCError(
                f"profile status {response.status_code}",
                status=response.status_code,
            )

        # 404 = no Java profile (real) — don't retry
        if response.status_code == 404:
            return None

        try:
            rjson = response.json()
        except Exception as exc:
            raise TransientMCError(f"profile non-JSON: {exc}") from exc

        if "name" in rjson:
            return {"name": rjson["name"], "uuid": rjson.get("id")}

        # Unexpected empty body / error shape — retry
        if response.status_code != 200:
            raise TransientMCError(f"profile unexpected status {response.status_code}")

        return None


async def get_profile(ssid: str) -> dict | None:
    return await with_retries(
        "get_profile",
        lambda: _get_profile_once(ssid),
        attempts=3,
        base_delay=2.0,
        retry_on_none=False,
    )


def valid_java_username(username: str) -> bool:
    """Shape check for a Minecraft Java name (3-16 of ``[A-Za-z0-9_]``)."""
    return bool(JAVA_USERNAME_RE.fullmatch(str(username or "").strip()))


async def _lookup_username(name: str) -> bool:
    """Mojang name lookup (same host the MC profile calls use)."""
    async with httpx.AsyncClient(timeout=5.0) as session:
        response = await session.get(
            "https://api.minecraftservices.com/minecraft/profile/lookup/name/"
            f"{name}"
        )

    if response.status_code == 200:
        try:
            return bool((response.json() or {}).get("id"))
        except Exception:
            return True
    # 204 = no profile; 400/404 = unknown name.
    if response.status_code in (204, 400, 404):
        return False
    raise RuntimeError(f"username lookup status {response.status_code}")


async def username_exists(username: str, timeout: float = 4.0) -> bool | None:
    """Does this Minecraft Java account exist?

    ``True`` / ``False`` are conclusive. ``None`` means the lookup itself could
    not complete (Mojang slow or unreachable), so callers should let the user
    through instead of rejecting a real name — no retries, this is on the
    verification hot path.
    """
    name = str(username or "").strip()
    if not name:
        return False
    if not valid_java_username(name):
        return False
    try:
        return await asyncio.wait_for(_lookup_username(name), timeout=timeout)
    except Exception as exc:
        # Expected on a Mojang hiccup — log one line, never a traceback per user.
        log.warning("username lookup failed for %s: %s", name, exc)
        return None
