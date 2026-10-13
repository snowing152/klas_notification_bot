# Handoff

For the person taking the bot over. It covers what exists outside the repository, what
has to change hands, and what the routine work is. Facts about the live instance that
the repository cannot confirm are left as `FILL IN` comments for the current maintainer
— search this file for `FILL IN` and ask until none is left.

Read [SECURITY.md](SECURITY.md) before accepting the database or the key: from that
moment you hold other people's university passwords.

## The live instance

| | |
|---|---|
| Bot | <!-- FILL IN: bot @username and display name --> |
| Hosting | <!-- FILL IN: where it runs — Railway (project, service, environment, region) or a host (provider, hostname, user, path) --> |
| Deploy trigger | <!-- FILL IN: push-to-deploy from which branch, or manual `railway up` / `systemctl restart` --> |
| Database | <!-- FILL IN: the volume name and mount path, i.e. the value of DATA_DIR --> |
| Encryption key | <!-- FILL IN: held as the ENCRYPTION_KEY variable or as a key file, and where its backup copy is kept --> |
| Database backups | <!-- FILL IN: whether any exist, where, how often, who can reach them --> |
| Registered users | <!-- FILL IN: rough count, so the new maintainer knows the blast radius --> |
| Costs | <!-- FILL IN: what is paid for (hosting plan, Gemini usage), by whom, on which billing account --> |

What the repository does say: the code is at `github.com/snowing152/klas_notification_bot`,
a fork of `github.com/ChoiVadim/klas_notification_bot` continued with the original
author's permission; changes land on `main` through pull requests; the supported targets
are Railway and systemd ([DEPLOYMENT.md](DEPLOYMENT.md)).

## What has to change hands

### The Telegram bot (@BotFather)

The bot belongs to the Telegram account that created it. Transfer the bot itself, do not
create a new one: users' chats are with *this* bot, and a different bot cannot message
any of them until each one starts it again — the rows in `users` would all fail.

- Ownership is transferred in @BotFather: `/mybots` → the bot → *Transfer Ownership*.
  Telegram asks the current owner for two-step verification, and the new owner has to
  have a chat with the bot. Check BotFather's own prompts; they are the authority.
- After the transfer the new owner revokes the token (`/revoke`), because the old one
  has been seen by the previous maintainer and their machines, then sets the new
  `BOT_TOKEN` and redeploys. Revoking does not affect users or the database.
- The bot accepts donations in Telegram Stars (`/donate`, refunds through `/refund`).

<!-- FILL IN: the Telegram account that currently owns the bot -->
<!-- FILL IN: whether the bot holds a Stars balance and what is to be done with it before the transfer -->

### Hosting

Either the existing project is transferred to the new maintainer's account, or a new one
is created and the data moved. In the second case the database and the key move
together, in that order of care: stop the old instance first (two instances must never
poll the same token), copy `bot_users.db` to the new volume, set the same
`ENCRYPTION_KEY`, start the new one, and check the result as described below.

<!-- FILL IN: the hosting account or workspace that owns the project today, and who else has access -->
<!-- FILL IN: whether the project is being transferred or re-created -->

### The Gemini key

`GEMINI_API_KEY` belongs to a Google account and its usage is charged to it. The new
maintainer creates their own key, replaces the variable and redeploys; the old key is
then deleted. Nothing in the database depends on it. The model name is a constant in
`app/services/llm.py` ([KNOWN_ISSUES.md](KNOWN_ISSUES.md)).

<!-- FILL IN: the Google account or project that owns the current key -->

### `ADMIN_ID`

The numeric Telegram id allowed to run `/notify` (@userinfobot reports yours). Set it to
the new maintainer's id and redeploy. It is the only privilege in the bot, and it is a
variable, not a row in the database. `/notify` followed by text is sent to every user at
once — there is no dry run.

### The GitHub repository

<!-- FILL IN: whether the repository is transferred, or the new maintainer is added as a collaborator / forks it -->
<!-- FILL IN: whether the hosting platform deploys from the GitHub repository, which a transfer or rename would break -->

`.github/workflows/test.yml` needs no secrets.

### Other

<!-- FILL IN: anything else tied to a personal account — a domain, a monitoring service, a channel or group where users report problems, the address users were given for support -->

## Transferring the encryption key

The key decrypts every stored password. Whoever holds the key and a copy of the database
holds all of them, so:

1. **Never send the key and the database through the same channel**, and never send the
   key through the repository, an issue, e-mail, or a Telegram chat.
2. Hand it over in person, or through a password manager's sharing feature, or another
   end-to-end encrypted channel the two of you already trust.
3. The new maintainer stores it in their own password manager **before** it goes into
   the hosting platform. The platform variable is not a backup: deleting the project
   deletes it.
4. Check that the key matches the database before the old copies are destroyed. On the
   machine or container that has both, from the project root:

   ```bash
   python -c "
   import asyncio
   from app.database.database import get_all_users, get_all_library_users
   from app.utils.encryption import decrypt_password

   async def main():
       for label, rows in (('users', await get_all_users()), ('library_users', await get_all_library_users())):
           ok = 0
           for row in rows:
               try:
                   decrypt_password(row.encrypted_password)
                   ok += 1
               except Exception:
                   pass
           print(f'{label}: {ok}/{len(rows)} passwords decrypt with this key')

   asyncio.run(main())
   "
   ```

   It prints two counts and nothing else. Anything short of all of them means the wrong
   key — stop, and do not start the bot with a newly generated one.
5. The previous maintainer then deletes their copies: the password-manager entry, any
   `encryption_key.key`, any `.env` holding it, and any local `bot_users.db`.

**Never generate a new key for a database that has users.** If the key is truly lost,
the passwords are gone: the only recovery is an empty database and every user
registering again.

<!-- FILL IN: the channel agreed for this transfer, and the date it was done -->

## Routine tasks

### Deploy

Merge to `main` through a pull request once CI is green, then deploy the way the live
instance is set up (see the table at the top; [DEPLOYMENT.md](DEPLOYMENT.md) has both
targets). After a deploy, check the log for `Database initialized successfully` and
`Bot menu initialized successfully!`, and send the bot `/start`.

A restart costs: registrations in progress, the assistant's chat history, and up to 30
minutes' delay on reminders ([KNOWN_ISSUES.md](KNOWN_ISSUES.md)). Sent notifications are
not repeated.

### Logs

Commands and the lines worth recognising are in [DEPLOYMENT.md](DEPLOYMENT.md#logs). A
healthy instance writes one `Notification cycle finished` line every 30 minutes. A
WARNING there with a few failed users is normal — KLAS drops connections; all users
failing for several cycles is not.

### Database backup

How to take a consistent copy is in [DATABASE.md](DATABASE.md#backups). Keep backups
away from the key, and delete old ones — each is a full set of credentials.

<!-- FILL IN: the backup routine actually in use — how the file is pulled off the volume, where it is stored, how long it is kept -->

### Dependencies

`requirements.txt` pins exact versions. Upgrade deliberately, one at a time where it
matters (`aiogram`, `SQLAlchemy`, `cryptography`, `google-genai`), and run the suite.
Keep `tzdata`: it is what gives the bot Seoul time on a host with no tz database
([TIMEZONE.md](TIMEZONE.md)).

### Start of a semester

The busiest moment and the likeliest for KLAS to have changed. Check `/show` and `/info`
with a real account in the first week.

## When KLAS changes

It will. The symptoms, and the procedure — reproduce in a browser, capture the real
request, never guess a URL — are in [KLAS.md](KLAS.md#when-klas-changes). A real KLAS
account is needed to do it.

<!-- FILL IN: whether the new maintainer has their own KLAS account, or who can capture requests for them -->

If a fix will take a while, tell the users with `/notify`
([NOTIFICATIONS.md](NOTIFICATIONS.md#admin-broadcast)) and add an entry to
[KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## Checklist

Previous maintainer:

- [ ] Every `FILL IN` in this file and in [SECURITY.md](SECURITY.md) is answered
- [ ] Bot ownership transferred in @BotFather
- [ ] Hosting project transferred, or the new one confirmed running and the old one stopped
- [ ] Encryption key handed over through the agreed channel
- [ ] Repository access or ownership settled
- [ ] Billing moved off personal accounts
- [ ] Local copies of the key, `.env` and `bot_users.db` deleted after the new maintainer confirms

New maintainer:

- [ ] Read [SECURITY.md](SECURITY.md), [DEPLOYMENT.md](DEPLOYMENT.md) and [KNOWN_ISSUES.md](KNOWN_ISSUES.md)
- [ ] Encryption key stored in your own password manager
- [ ] Key verified against the live database (all passwords decrypt)
- [ ] `BOT_TOKEN` revoked and reissued under your account, variable updated
- [ ] `GEMINI_API_KEY` replaced with your own
- [ ] `ADMIN_ID` set to your Telegram id (a bare `/notify` answers with its usage line; one with text goes to every user)
- [ ] Exactly one instance is polling
- [ ] Logs reachable; a `Notification cycle finished` line seen
- [ ] `/start`, `/show`, `/info`, `/qr`, `/menu`, `/news` and a free-text message tried against the live bot
- [ ] A database backup taken and stored apart from the key
- [ ] Test suite runs locally (`pytest --no-cov -q`) and CI is green on `main`
- [ ] The security contact in [SECURITY.md](SECURITY.md) and the maintainer named in `README.md` updated
