# sunnysecure

Autosecure Discord bot + mail automation workspace.

This is not a single script. The main pieces are:

- `bot.py` - autosecure Discord bot entrypoint
- `start.ps1` - Windows quick-start wrapper for `bot.py`
- `ecosystem.config.cjs` - pm2 production process config for `bot.py`
- `config/` - runtime configuration
- `cogs/` - bot command modules
- `securing/` - verification / security automation
- `mail/` - inbound mail handling, currently mail.tm polling
- `ui/` - Discord UI views and modals
- `shared/` - shared helpers
- `.venv/` - Python environment (expected to exist before running)

---

## What it does

- Runs a Discord bot with slash commands and interaction UI views
- Verifies/automates account security flows through the `securing/` path
- Polls mail.tm inboxes for inbound mail (OTP / security mail), by default
- Persists state in a local SQLite database via `database/database.py`

The bot is configured primarily through:

- `config/config.json`
- `config/bot.json`

---

## Prerequisites

- **Python 3.11+** (the project uses a local `.venv`, which `start.ps1` and `update_from_github.bat` create for you)
- **Git**, so updates can be pulled from GitHub
- **Dependencies** from `requirements.txt`

On Windows, `start.ps1` is the easiest way to launch the bot - it creates the venv, installs the dependencies and runs the bot.

---

## First-time setup

### 1. Create the Python environment

From the folder root:

`start.ps1` (and `update_from_github.bat`) do this automatically. To do it by hand:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If you already have `.venv` in the folder, skip the `venv` creation step and just install requirements into it.

Optional - only if you turn on `autosecure.reject.check_hypixel_ban` or
`check_donutsmp_ban`: those run `minecraft/ban_check/check.js` through Node, so install
Node.js 18+ and then

```powershell
cd minecraft\ban_check
npm install
```

Its `node_modules/` is gitignored, so it is not part of the download.

### 2. Configure the bot

A fresh clone has no `config/config.json` (it is gitignored because it holds the bot token). Create it from the template:

```powershell
copy config\config.json.example config\config.json
```

Then edit `config/config.json` and `config/bot.json` for the target server/owner.

The sensitive bits are in `config/config.json`:

- `owners`
- `tokens.bot_token`
- `discord.logs_channel`, `discord.accounts_channel`, `discord.censored_logs_channel`
- `mail.discord_webhook_all`
- `mail.discord_webhook_otp`
- `mail.otp_bridge_urls` / `mail.otp_bridge_url` / `mail.otp_bridge_token` if you use an OTP bridge
- `mail.provider`
- `mail.otp_login_email` - the fallback login address used when the temp-mailbox OTP
  path needs one; leave it empty to use the secured account's own address
- `mail.mailtm.*`
- `coldproxy.*` and `proxy.*` if you use proxies
- `autosecure.*` rejection/behavior settings

`config/bot.json` is mostly bot behavior and embeds:

- `enabled_commands` - either `true`/`false` for every command, or an object with `real`
  (per-cog on/off), `fake` and `aliases` (command names)
- `embeds` - verification text plus the `components_v2` card styling
- `presence` - `status`, `activity_text`, `activity_type` (`playing`, `watching`, ...)
- `post_verification` - `actions` run after a successful verification
- `refresh_posts` - `interval_hours` and the `posts` to refresh

**Do not ship this folder with real tokens and webhook URLs still in it unless the next person is meant to use those exact values.** If you are making this downloadable by others, replace the secrets first or make the config steps explicit for them.

### 3. Run it

Quick Windows start (creates `.venv`, installs `requirements.txt`, then runs the bot):

```powershell
.\start.ps1
```

Or directly, if the environment is already set up:

```powershell
.venv\Scripts\python.exe bot.py
```

`start.ps1` keeps the window open when something fails, so the error stays readable.

### 4. Running as a persistent process

The project includes `ecosystem.config.cjs` for pm2. It resolves `.venv` relative to the
folder it lives in, so it works after moving or cloning the project:

```powershell
npm install -g pm2
pm2 start ecosystem.config.cjs
pm2 logs autosecure-bot
```

---

## Updating an install (`update_from_github.bat`)

Double-click `update_from_github.bat` (or run it from cmd) inside the project folder.
It keeps its window open when it finishes, so you can always read what happened.

What it does, in order:

1. Finds git, checks that the folder is a git checkout and prints the branch, remote and commit.
2. Fetches `origin` and compares your copy with GitHub.
3. Shows the new commit list, then asks before changing anything (skip with `/y`).
4. Pulls with `--ff-only`. If local edits block the update it stops and explains
   how to keep them or how to force GitHub's version (`RESET` prompt).
5. Creates `.venv` if it is missing, installs `requirements.txt`, and re-runs the
   database migrations.
6. Creates `config/config.json` from `config/config.json.example` on a first run
   and never overwrites an existing config.
7. Tells you whether the bot still needs a restart.

Flags:

| Flag | Meaning |
| --- | --- |
| *(none)* | Interactive updater; asks before pulling and waits for a key at the end. |
| `/check` | Only check. Exit code `10` = update available, `0` = up to date. |
| `/y` | Unattended: no confirmation prompt, no final pause. |

Exit codes: `0` ok / up to date, `2` no git or not a repo, `3` fetch failed,
`4` Python/venv problem, `5` pull blocked by local changes, `10` update available (`/check`).

Your `config/` files, the SQLite database, `config/refresh_posts_state.json` and `logs/`
are untracked, so updates never touch them.

---

## Publishing this on GitHub

### First publish

1. Create an **empty** repository on GitHub (no README, no .gitignore - this project
   already has one).
2. Check that no secrets are about to be committed. `config/config.json` (bot token,
   webhooks), `database/*.db`, `.venv/` and `logs/` are already covered by `.gitignore`:

   ```bash
   git status --short
   git check-ignore -v config/config.json database/database.db
   ```

   If `config/config.json` shows up, stop and fix `.gitignore` before pushing.
3. Point the folder at **your own** repository and push:

   ```bash
   # brand-new checkout: add the remote
   git remote add origin https://github.com/<your-user>/<your-repo>.git
   # existing checkout (this one is on waitno01/sunnysecure): repoint it
   git remote set-url origin https://github.com/<your-user>/<your-repo>.git
   git remote -v          # confirm origin is YOUR repo
   git add -A
   git commit -m "Claim flow: mention claimer, IGN gate, @everyone ping, DM cleanup"
   git push -u origin main
   ```

   If the new repo was created *with* a README/.gitignore, GitHub will reject the first
   push (`fetch first`). Fix it once with:

   ```bash
   git pull --rebase origin main   # or: git push -u origin main --force  (only on a fresh repo)
   ```
4. On GitHub: **Settings > General** - describe the repo, then **Releases > Draft a new
   release** with a tag such as `v1.0.0` so users can see what changed.

### Shipping an update

```bash
# ...make your changes...
git add .
git commit -m "Fix claim flow and add Components V2 config"
git push origin main
git tag v1.0.1
git push --tags
```

Anyone running `update_from_github.bat` then sees those commits listed and pulls them
with one keypress. Keep everything users need on `main`: the updater pulls that branch.

Do not force-push `main` - installs that are behind would fail to fast-forward.

### How someone else installs it

```bash
git clone https://github.com/<your-user>/<your-repo>.git
cd <your-repo>
copy config\config.json.example config\config.json   # then fill in the bot token etc.
.\start.ps1
```

Whichever repo you push to becomes the update source: `update_from_github.bat` pulls the
`origin` remote of the clone it is run in, so a user who cloned your repo gets your
commits.

Later they just run `update_from_github.bat`.

---

## Configuring the verification cards (Components V2)

The verification flow uses Minelink-style Components V2 cards (`ui/theme.py`): a green
`Container` with the heading, body and buttons inside the card. Classic embeds cannot
put a button inside the card, which is why these cards are not `discord.Embed`s.

The look is configured in `config/bot.json` under `embeds.components_v2`. Every key is
optional - anything you leave out keeps the current design exactly as it is:

```json
"components_v2": {
    "colors": {
        "success": "#52A435",
        "error": "#ED4245",
        "warning": "#FEE75C",
        "info": "#5865F2"
    },
    "footer": "-# Minecraft Verification",
    "heading_emoji": "\ud83d\udd37",
    "verify_button": { "label": "Verify", "emoji": "\u2705", "style": "success" }
}
```

- `colors` accepts `"#RRGGBB"`, `"0xRRGGBB"` or a decimal integer. `success` is used by
the verification/secure cards, `error` by failures and timeouts, `warning` by warnings.
- `footer` is the small `-# ...` line under the verify prompt.
- `verify_button`: `label`, optional `emoji` (`""` removes it) and one of
`primary`, `secondary`, `success`, `danger`.
- The verify card text itself is `embeds.verify_card` (`title`, `description`, `thumbnail`).
If that block is empty the card falls back to `embeds.verification.default`.
- `thumbnail` may be a URL, or `attachment://minelink.png` for a file in `assets/` (the
`/send embed` command attaches it automatically).

Config is read when the bot starts, so restart it after editing.

---

## Claims

When a secure succeeds, the bot posts one Components V2 card and (unless
`claims.enabled` is `false`) an `@everyone` ping above it announcing that an
account is up for grabs. The card carries a single `Claim` button.

`config/config.json` -> `claims`:

| Key | Meaning |
| --- | --- |
| `enabled` | Turn the claim card on/off. When `false` the hit channel only gets the usual hit embed. |
| `claim_button_label` | Text on the button (still one button; the label is cosmetic). |
| `claim_role_id` | Role required to press `Claim`. Empty = claims disabled. |
| `require_minecraft_username` | `true` = pressing `Claim` opens a modal asking for the secured account's Minecraft username. The claim only goes through when it matches the stored name, or when the secured account has no Minecraft name (an open account). `false` = the old behaviour, first person to press wins. |
| `claim_requires_verified`, `claim_notes_prompt` | Reserved for the verification-gated/notes variants. |

What a claim does:

1. The presser's role is checked and the account's claim row is written. A second
   presser gets "already claimed by ..." instead of a second copy.
2. The public card is edited in place: `Claimed by @user` (a real mention) and a
   disabled, greyed `Claimed` button - so it stops being clickable for everyone.
3. The claimer gets one DM with the Microsoft email/password, the temporary
   mailbox credentials and the recovery code. Nothing secret is ever posted in
   the hit channel, so a blocked-DM claim is reported ephemerally instead.

---

## Startup behavior

- `bot.py` checks `logs/bot.lock` and refuses to start if another autosecure-bot instance already holds it and is alive. This is to avoid duplicating the Discord gateway, mail poller, and database writes.
- On start, `bot.py` starts mail polling first, then loads cogs, then connects to Discord.
- The mail path defaults to mail.tm polling. Local SMTP is only relevant when `mail.provider` is set to `"local"` and you have your own domain/MX setup.
- Logs go to `logs/securing.log`.

---

## Project structure

```
sunnysecure/
  bot.py                    # main bot entrypoint
  start.ps1                 # Windows launcher (venv + deps + run)
  update_from_github.bat    # pull updates, deps and DB migrations
  ecosystem.config.cjs      # pm2 process config
  requirements.txt          # Python deps
  config/
    config.json             # tokens, owners, mail, proxy, autosecure settings
    config.json.example     # template for a fresh install
    bot.json                # enabled commands, embeds, presence, posts
    bot.json.example        # template for a fresh install
  cogs/                  # bot command modules
  securing/              # security/verification automation
  mail/                  # mail.tm polling and inbox handling
  ui/                    # Discord UI views/modals
  shared/                # shared helpers
  database/
    database.py          # SQLite persistence
  logs/
    bot.lock             # single-instance lock
    securing.log         # runtime log
  .venv/                 # Python environment
```

---

## Sharing / distributing this folder

If someone else is going to download and run this:

1. Make sure `requirements.txt` and this README travel with the code.
2. Decide what to include from `logs/` and `.venv/`. A clean distributable usually excludes both; the next person creates their own venv and starts fresh.
3. Replace or blank out real tokens, webhook URLs, and other secrets in `config/config.json` before sharing, unless the recipient is explicitly meant to inherit them.
4. Note the single-instance lock in `logs/bot.lock` so the next person does not get confused by the "another instance already running" exit.

---

## If something fails

- **Bot refuses to start with an "already running" message** - another copy is holding `logs/bot.lock`. Either stop that copy, or delete `logs/bot.lock` if the old process is actually gone.
- **Missing module errors** - reinstall `requirements.txt` into the `.venv` you are using.
- **Bot starts but commands/views do not behave** - check `config/config.json` and `config/bot.json` first, especially tokens, guild/channel IDs, and the enabled commands.
- **Mail does not come through** - the default provider is mail.tm. Confirm `mail.provider`, the mail.tm settings, and any webhook/OTP bridge values are correct for the environment.
- **Updater says "This folder is not a git checkout"** - download the project with `git clone` instead of a ZIP; the updater needs `.git`.
- **Updater says the update "could not be applied cleanly"** - you have local edits that the update also changes. Commit them, or type `RESET` at the prompt to take GitHub's version (your `config/` and database are kept).
- **Bot exits with "Another autosecure-bot instance is already running"** - another copy holds `logs/bot.lock`; stop it or delete the lock file.
- **Cards look wrong after editing `config/bot.json`** - the bot reads that file at startup; restart it. Invalid JSON prints the exact parse error and exits.
- **Logs** - inspect `logs/securing.log`.

---

## Security note

This workspace deals with account verification and security-mail handling. Treat the config file, database, and logs as sensitive. Do not paste real tokens, webhook URLs, or mailbox credentials into shared contexts.

---

## Cleaning it for a friend

If you want to send this as a zip and have the other person install/run it from scratch, remove the stuff that is only useful to you locally:

- `.venv/` - the Python environment. The recipient should create their own.
- `logs/` - runtime logs and the `bot.lock` PID file. Not needed to start fresh.
- Every `__pycache__/` folder in the project code - regenerated automatically on first run.
- `database/database.db` and `database/ltc_wallet.db` - existing runtime data. Omit these if you want the recipient to start with a fresh DB.
- `.git/` - the git history. Usually not needed for a friend to run the project.

Keep the stuff the recipient actually needs:

- `bot.py`
- `start.ps1`
- `ecosystem.config.cjs`
- `requirements.txt`
- `config/`
- `cogs/`
- `securing/`
- `mail/`
- `ui/`
- `shared/`
- `database/database.py`
- `assets/`
- `README.md`

A minimal clean package usually looks like this:

```
sunnysecure-clean/
  bot.py
  start.ps1
  ecosystem.config.cjs
  requirements.txt
  README.md
  config/
  cogs/
  securing/
  mail/
  ui/
  shared/
  database/
    database.py
  assets/
```

Do not include `.venv`, `logs`, `__pycache__`, `.git`, or the DB files unless there is a specific reason to.

Before sending it, also make sure `config/config.json` does not contain real tokens or webhook URLs unless the recipient is explicitly meant to use those exact values.
