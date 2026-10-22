"""Offline startup smoke test.

Reproduces everything the bot does up to (and including) the line that used
to crash in ``on_ready`` — WITHOUT connecting to Discord:

  * imports bot.py (config load, compat shim, DiscordBot construction)
  * loads every cog
  * adds the three persistent views (LinkAccountView is the exact crash site)
  * builds every Components-V2 card in ui/theme.py

Run:  ./.venv/Scripts/python.exe smoketest_startup.py

Exit code 0 means the whole start-up path is clean: the config loads, every cog
loads, the persistent views register and every Components-V2 card builds.  It
never connects to Discord.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def main() -> int:
    import bot as b

    print("[ok] bot.py imported (config + shim + DiscordBot built)")

    # 1. Every cog
    await b.bot.load_cogs()
    loaded = sorted(
        getattr(cog, "qualified_name", str(cog)) for cog in b.bot.cogs.values()
    )
    print(f"[ok] cogs loaded: {', '.join(loaded)}")

    # 2. The exact three lines that used to raise
    #    TypeError: Container.__init__() got an unexpected keyword argument
    #    'accent_colour'
    from ui.buttons.claim_button import ClaimPersistentView
    from ui.buttons.create_ticket import CreateTicketView
    from ui.buttons.link_account import LinkAccountView

    b.bot.add_view(LinkAccountView())
    b.bot.add_view(CreateTicketView())
    b.bot.add_view(ClaimPersistentView())
    print("[ok] persistent views registered (LinkAccountView no longer crashes)")

    # 3. Every Components-V2 card builder
    import ui.theme as theme

    async def cb(_interaction):  # noqa: ANN001
        return None

    cards = {
        "verify_card": theme.verify_card(on_verify=cb),
        "username_found_card": theme.username_found_card("Steve", on_email=cb),
        "checking_card": theme.checking_card(),
        "being_verified_card": theme.being_verified_card("Steve"),
        "authenticator_card": theme.authenticator_card("42"),
        "otp_card": theme.otp_card("user@example.com", on_submit=cb),
        "success_card": theme.success_card("Steve"),
        "fail_card": theme.fail_card(
            "Failed", "nope", field_label="Reason", field_value="x", on_retry=cb
        ),
        "timeout_card": theme.timeout_card(reason="later", on_retry=cb),
    }
    for name in cards:
        print(f"[ok] card built: {name}")

    print("\nSMOKE TEST PASSED — startup path is clean on py-Cord", __import__("discord").__version__)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
