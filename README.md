# sunnysecure

Autosecure Discord bot + mail automation: slash commands and Components V2 cards,
account-securing flows under `securing/`, mail.tm inbox polling, SQLite persistence,
a claim/giveaway flow, and a Windows launcher plus GitHub updater.

## Quick start (Windows)

```bat
git clone https://github.com/noahtilbury/Discord-Minecraft-AutoSecure.git
cd Discord-Minecraft-AutoSecure
start.bat
```

Double-click `start.bat` or run it from cmd. On the first run it creates `.venv`,
installs `requirements.txt`, and - because a fresh clone has no `config/config.json` -
creates that file from `config/config.json.example`, prints exactly what to fill in
and stops. Add your bot token and owners, run `start.bat` again, and the bot comes
online. See [First-time setup](#first-time-setup).

`start.bat` is plain batch on purpose: Windows ships with the PowerShell execution
policy set to `Restricted`, so any `.ps1` launcher fails with "running scripts is
disabled on this system" on a fresh machine.

## What is in the project

- `bot.py` - bot entrypoint (mail polling, cogs, gateway)
- `start.bat` - Windows launcher (venv + deps + run)
- `update_from_github.bat` - pulls updates, installs requirements, migrates the DB
- `ecosystem.config.cjs` - pm2 process config
- `cogs/` - slash command modules
- `securing/` - verification and account-security automation
- `mail/` - inbound mail (mail.tm) polling and provisioning
- `ui/` - Discord views, modals and Components V2 cards
- `shared/` - shared helpers
- `database/database.py` - SQLite persistence (the `.db` itself is not shipped)
- `minecraft/` - profile/ban/name lookups (ban checks need Node, see below)
- `assets/` - images referenced by the cards
- `config/` - runtime configuration (only the `.example` files are shipped)

## Prerequisites

- **Python 3.11+** - `start.bat` creates the local `.venv` for you
- **Git** - the updater pulls from the repo you cloned
- Optional: **Node.js 18+** only if you enable `autosecure.reject.check_hypixel_ban`
  or `check_donutsmp_ban`; those run `minecraft/ban_check/check.js`, so run
  `cd minecraft\ban_check && npm install` once (its `node_modules/` is gitignored)
- Optional: **pm2** (`npm install -g pm2`) to keep the bot running

## First-time setup

### 1. Create the Python environment

`start.bat` and `update_from_github.bat` do this automatically. By hand:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. Configure the bot

A fresh clone has no `config/config.json` (it is gitignored because it holds the bot
token), so `start.bat` copies `config/config.json.example` to `config/config.json` on
the first run and stops with instructions. By hand:

```powershell
copy config\config.json.example config\config.json
```

Then edit `config/config.json`. `tokens.bot_token` (Discord Developer Portal ->
Bot -> Reset Token) and `owners` (your Discord user ID) are the only values needed
to boot; the rest can stay as shipped:

- `owners`, `tokens.bot_token`, `tokens.skytools_key`, `tokens.donut_key`
- `discord.logs_channel`, `discord.accounts_channel`, `discord.censored_logs_channel`
- `mail.*` - provider, mail.tm settings, webhooks, OTP bridge, `otp_login_email`
  (the fallback login address for the temp-mailbox OTP path; empty = use the secured
  account's own address)
- `claims.*` - see [Claims](#claims)
- `coldproxy.*`, `proxy.*` - only if you use proxies
- `autosecure.*` - rejection and behaviour settings

`config/bot.json` covers behaviour and looks:

- `enabled_commands` - `true`/`false` for all commands, or an object with `real`
  (per-cog on/off), `fake` and `aliases` (command names)
- `embeds` - verification text and the `components_v2` card styling
- `presence` - `status`, `activity_text`, `activity_type`
- `post_verification`, `refresh_posts` - follow-up actions and periodic reposts

Config is read at startup: restart the bot after editing, and keep the JSON valid
(an invalid file prints the exact parse error and exits).

### 3. Run it

```bat
start.bat
```

or, with the environment already set up:

```powershell
.venv\Scripts\python.exe bot.py
```

### 4. Keep it running (pm2)

`ecosystem.config.cjs` resolves `.venv` relative to itself, so it survives moving or
cloning the folder:

```powershell
npm install -g pm2
pm2 start ecosystem.config.cjs
pm2 logs autosecure-bot
```

## Claims

When a secure succeeds, the bot posts an `@everyone` ping and one Components V2 card
with a single `Claim` button (unless `claims.enabled` is `false`, in which case the hit
channel only gets the usual hit embed).

`config/config.json` -> `claims`:

| Key | Meaning |
| --- | --- |
| `enabled` | Turn the claim card on/off. |
| `claim_button_label` | Text on the button (cosmetic; still one button). |
| `claim_role_id` | Role required to press `Claim`. Empty = claims disabled. |
| `require_minecraft_username` | `true` = pressing `Claim` opens a modal asking for the secured account's Minecraft username; the claim only goes through when it matches the stored name, or when the account has no Minecraft name. `false` = first person to press wins. |
| `claim_requires_verified`, `claim_notes_prompt` | Reserved for the verification-gated/notes variants. |

What a claim does:

1. The presser's role is checked and the claim row is written. A later presser gets
   "already claimed by ..." instead of a second copy.
2. The public card is edited in place: `Claimed by @user` (a real mention) and a
   disabled, greyed `Claimed` button, so it stops being clickable for everyone.
3. The claimer gets one DM with the Microsoft email/password, the temporary mailbox
   credentials, the recovery code and the authenticator secret. Nothing secret is ever
   posted in the hit channel; a blocked DM is reported ephemerally instead.

Claim buttons live in the bot's memory: after a restart, cards posted earlier stop
responding.

## Verification cards (Components V2)

The verification flow uses Minelink-style Components V2 cards (`ui/theme.py`) - a
`Container` with heading, body and buttons inside the card, which classic embeds cannot
do. The look is configured in `config/bot.json` under `embeds.components_v2`; every key
is optional and anything you leave out keeps the current design:

```json
"components_v2": {
    "colors": {
        "success": "#52A435",
        "error": "#ED4245",
        "warning": "#FEE75C",
        "info": "#5865F2"
    },
    "footer": "",
    "heading_emoji": "\ud83d\udd37",
    "verify_button": { "label": "Verify", "emoji": "\u2705", "style": "success" }
}
```

- `colors` accepts `"#RRGGBB"`, `"0xRRGGBB"` or a decimal integer.
- `footer` is the small line under the verify prompt. It ships empty (no footer);
  set it to `"-# Minecraft Verification"` to get Discord subtext back, or any other
  string to render it as-is.
- `verify_button`: `label`, optional `emoji` (`""` removes it) and one of `primary`,
  `secondary`, `success`, `danger`.
- The card text is `embeds.verify_card` (`title`, `description`, `thumbnail`); if that
  block is empty the card falls back to `embeds.verification.default`.
- `thumbnail` may be a URL or `attachment://minelink.png` for a file in `assets/`
  (the `/send embed` command attaches it automatically).

## Updating an install

Run `update_from_github.bat` inside the project folder (double-click, or from cmd).

1. Prints branch/remote/commit. A folder without `.git` (a ZIP download) skips git
   entirely and takes the ZIP path described below.
2. Fetches `origin` and compares your copy with GitHub.
3. Lists the new commits and asks before changing anything.
4. Pulls with `--ff-only`; if local edits block it, it explains how to keep them or
   how to force GitHub's version (`RESET` prompt).
5. Creates `.venv` if missing, installs `requirements.txt`, re-runs the DB migrations.
6. Creates `config/config.json` from the example on a first run, never overwrites an
   existing one.
7. Tells you whether the bot still needs a restart.

**ZIP installs.** A copy extracted from GitHub's ZIP has no `.git`, so the updater
downloads the latest source archive from the repository and copies it over the folder
instead, keeping `config/config.json`, the database, `logs/` and `.venv/`. It cannot
compare commits, so `/check` exits `10` and a normal run always refreshes to the
latest `main`. The newer updater arrives as `update_from_github.bat.new`; swap it in
with `move /y update_from_github.bat.new update_from_github.bat` after the window
closes.

| Flag | Meaning |
| --- | --- |
| *(none)* | Interactive: asks before pulling, waits for a key at the end. |
| `/check` | Only check. Exit code `10` = update available, `0` = up to date. |
| `/y` | Unattended: no confirmation prompt, no final pause. |

Exit codes: `0` ok/up to date, `2` no git or not a repo, `3` fetch failed,
`4` Python/venv problem, `5` pull blocked by local changes, `7` ZIP download or copy
failed, `10` update available.

`config/`, `config/refresh_posts_state.json`, the SQLite database and `logs/` are
untracked, so updates never touch them.

## Publishing this project

`origin` is `https://github.com/noahtilbury/Discord-Minecraft-AutoSecure.git` and
`update_from_github.bat` ships that URL in its hints, plus a warning if a copy still
tracks the old `waitno01/sunnysecure` repo. To publish a change:

1. Check nothing sensitive is about to be committed - `config/config.json`, `*.db`,
   `.venv/` and `logs/` are already covered:

   ```bash
   git status --short
   git check-ignore -v config/config.json database/database.db
   ```

2. Commit and push (drop `set-url` unless the remote is wrong):

   ```bash
   git remote set-url origin https://github.com/noahtilbury/Discord-Minecraft-AutoSecure.git
   git remote -v
   git add -A
   git commit -m "your change"
   git push origin main
   ```

   Installations that already exist update with `update_from_github.bat`; a first
   push of a brand-new repo needs `-u` once (`git push -u origin main`).

Anyone running `update_from_github.bat` then sees those commits and pulls them with one
keypress. Keep everything users need on `main` and never force-push it - installs that
are behind must be able to fast-forward.

## Troubleshooting

- **"running scripts is disabled on this system"** - you ran a PowerShell script
  such as a `.ps1` launcher; use `start.bat` instead, which needs no PowerShell.
- **"Missing bot config: config/config.json"** - `start.bat` creates that file on the
  first run and says what to fill in. If it is still missing, the copy failed: do it
  by hand with `copy config\config.json.example config\config.json`.
- **"has no bot token yet"** - `tokens.bot_token` in `config/config.json` is empty.
  Paste the token from the Discord Developer Portal, save, run `start.bat` again.
- **"Another autosecure-bot instance is already running"** - another copy holds
  `logs/bot.lock`. Stop that copy, or delete the lock file if that process is gone.
- **Missing module errors** - reinstall into the venv you run:
  `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
- **Commands or views misbehave** - check `config/config.json` and `config/bot.json`
  first: token, guild/channel ids, `enabled_commands`, `claims.claim_role_id`.
- **Mail does not arrive** - the default provider is mail.tm; confirm `mail.provider`,
  the mail.tm settings and any webhook/OTP bridge values.
- **Mail webhook says 404 `Unknown Webhook`** - the webhook in
  `mail.discord_webhook_all` / `discord_webhook_otp` was deleted. The bot checks it
  once at startup, warns in the console, and posts mail to `discord.logs_channel`
  instead. Create a new webhook in the channel and paste its URL into the config.
- **"Unknown Integration" on an old verify or mail card** - that is Discord telling
  you the component's interaction is no longer valid (expired token or a bot
  restart). Mail cards now carry persistent buttons and the verify result falls back
  to a DM, so both keep working; older messages posted before this update still need
  to be re-sent with the command.
- **Ban checks always fail** - `npm install` in `minecraft/ban_check` (Node.js 18+).
- **Updater in a ZIP copy** - a folder without `.git` updates by downloading the latest
  source archive (Git is not needed); `config/`, the database, `logs/` and `.venv/` are
  kept. **"could not be applied cleanly"** - local edits conflict with the update;
  commit them or type `RESET` (your `config/` and database are kept).
- **Logs** - `logs/securing.log`.

## Security note

This project handles account verification and security mail. Treat `config/`, the
database and `logs/` as sensitive, and never commit or share real tokens, webhook URLs,
mailbox credentials or the secured-account data.

## Project layout

```
sunnysecure/
  bot.py                    # bot entrypoint
  start.bat                 # Windows launcher (venv + deps + run)
  update_from_github.bat    # updater (pull + deps + migrations)
  ecosystem.config.cjs      # pm2 process config
  requirements.txt          # Python deps
  config/
    config.json(.example)   # tokens, owners, mail, proxy, claims, autosecure
    bot.json(.example)      # commands, embeds, presence, refresh posts
  cogs/                     # slash command modules
  securing/                 # verification / security automation
  mail/                     # mail.tm polling and provisioning
  ui/                       # views, modals, Components V2 cards
  shared/                   # shared helpers
  database/database.py      # SQLite persistence
  minecraft/                # profile / ban / name lookups
  assets/                   # card images
  logs/                     # runtime logs + bot.lock (created at runtime)
  .venv/                    # Python environment (created by start.bat)
```

## Packaging it for someone else

Ship the tracked files (a `git clone` or the zip GitHub generates) and nothing else.
Do not include `.venv/`, `logs/`, `__pycache__/`, `database/*.db`,
`config/config.json` or `config/refresh_posts_state.json` - all of it is gitignored, so
a plain clone already excludes them. The recipient runs `start.bat` once to build
`.venv` and get `config/config.json` created from the example, fills in their own token
and ids, and runs `start.bat` again.
