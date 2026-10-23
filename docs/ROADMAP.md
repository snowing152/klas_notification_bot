# Roadmap

Where the bot is going and in what order. The goal: a bot that is **reliable** (a missed
deadline notification is the one failure users remember), **cheap to run** (it has to stay
inside the included usage of a small hosting plan), and **useful** beyond the first
week of a semester.

This file is the plan, not the bug list. Bugs and limitations live in
[KNOWN_ISSUES.md](KNOWN_ISSUES.md); an item here links to its entry there when it fixes
one. Tick a box in the same change that does the work, and move anything dropped to the
[Not planned](#not-planned) list with a reason, so the question is not asked again.

Last reviewed: 2026-10-14.

## Principles

1. **Trust before features.** Notifications that are late, repeated or silent cost more
   than any new command earns. Reliability items go first.
2. **The bill is RAM.** The bot is idle between Telegram updates and a notification cycle;
   CPU and network are cents. Anything that raises resident memory or adds a service
   raises the bill. See [Running cost](#running-cost).
3. **Do not add load to KLAS.** KW's endpoints are unofficial and already drop
   connections under light load ([KLAS.md](KLAS.md)). A feature that multiplies requests
   per user per cycle needs a reason.
4. **One process, one SQLite file.** No second replica, no Redis, no Postgres, unless a
   specific problem cannot be solved without one.
5. **Every new KLAS endpoint comes from a captured browser request.** Never guessed
   ([KLAS.md](KLAS.md)).

## Running cost

The bot is a worker with no port. On Railway's Hobby plan the subscription is $5 a month
and includes $5 of resource usage; usage beyond that is billed on top. Rates, from
[Railway's pricing page](https://docs.railway.com/pricing/plans) at the time of writing:

| Resource | Price |
|---|---|
| RAM | $10 / GB / month |
| CPU | $20 / vCPU / month |
| Network egress | $0.05 / GB |
| Volume storage | $0.15 / GB / month |

Monthly usage is therefore about
`10 × average GB of RAM + 20 × average vCPU + 0.05 × egress GB + 0.15 × volume GB`.
An average of 0.3 GB of RAM is $3 a month on its own; the same bot at 0.15 GB is $1.50.
Average memory is the number to push down, and what a peak costs depends on how long it
lasts, not how high it is.

Keep the bill under the included usage:

- Set a usage alert or limit in the Railway dashboard, so a regression shows up as a
  notification and not as an invoice.
- Read the service's Metrics tab after each deploy that touches the notification loop,
  the caches or the dependencies.
- Gemini is billed to the Google account that owns `GEMINI_API_KEY`, not to Railway;
  see Phase 2.

<!-- FILL IN: the observed monthly average of RAM / CPU and the monthly usage budget, and the date they were read -->

## Phase 0 — Facts and safety nets

Cheap, no code, and everything else depends on them.

- [ ] Fill in the `FILL IN` markers in [HANDOFF.md](HANDOFF.md) and
      [SECURITY.md](SECURITY.md), starting with backups and the security contact.
- [ ] Decide and write down a database backup routine
      ([DATABASE.md](DATABASE.md#backups)). The file holds every user's credentials, so
      the routine also decides who may read the backups.
- [ ] Set the usage alert described above.
- [ ] Confirm that the builder the live service uses is the one `railway.json` pins; a
      dashboard setting and the file can disagree.

## Phase 1 — Reliability and honesty

Fixes for things users can already hit. Ordered by how badly they bite.

- [ ] **The assistant's history outlives `/unregister`.** One line in
      `auth.delete_all_user_data`; the project rule that a per-user store is cleared there
      is currently broken
      ([entry](KNOWN_ISSUES.md#the-assistants-chat-history-outlives-unregister)).
- [ ] **A restart in the middle of `/register` hands the password to the assistant.**
      Decide between persisting the FSM step (never the typed secrets) and a guard in
      `common.other_message` for users who have no row yet
      ([entry](KNOWN_ISSUES.md#a-restart-loses-a-registration-in-progress)).
- [ ] **Registration prompts swallow commands**, and there is no `/cancel`
      ([entry](KNOWN_ISSUES.md#registration-prompts-swallow-commands)).
- [ ] **Tell the user when `/show` is incomplete** instead of showing a list that is
      silently short ([entry](KNOWN_ISSUES.md#show-does-not-say-when-its-list-is-incomplete)).
- [ ] **Alert the admin when a cycle fails for everyone**, for several cycles in a row.
      Today only the log shows that KLAS changed; a message to `ADMIN_ID` costs nothing
      and replaces an external monitor.
- [ ] **Check once on startup**, not after the first 30-minute sleep, so a deploy does not
      delay reminders ([entry](KNOWN_ISSUES.md#nothing-is-checked-for-the-first-30-minutes-after-a-start)).
      Needs care to stay cheap when the process is restarted repeatedly.
- [ ] **Do not drop a reminder that quiet hours held**
      ([entry](KNOWN_ISSUES.md#a-reminder-held-by-quiet-hours-is-not-queued)).
- [ ] **Pace and record `/notify`**
      ([entry](KNOWN_ISSUES.md#notify-has-no-pacing-and-no-record)).
- [ ] **Move the English-only literals into `app/strings.py`** with the add-string skill
      ([entry](KNOWN_ISSUES.md#some-replies-are-english-only)).
- [ ] **Make the Gemini model name a setting** so retiring a model is a variable change,
      not a deploy ([entry](KNOWN_ISSUES.md#the-gemini-model-name-is-hard-coded)).

## Phase 2 — Footprint

Aim: lower average RAM and a bounded Gemini bill, without losing a feature. Measure first,
change second; every item here needs a before and after number.

- [x] **Measure.** The `Notification cycle finished` line now ends with the cycle's
      duration and memory, and `Startup memory` gives the baseline
      ([DEPLOYMENT.md](DEPLOYMENT.md#logs)). Memory peaks are most likely the
      notification cycle (five users at a time, many requests each) or `/qr` and `/info`
      image work; read a few days of these lines before choosing the next item.
- [ ] **Find the baseline.** Check what is imported at startup (the Gemini SDK, Pillow,
      SQLAlchemy) and whether `google-genai` can be imported only when a key is set.
- [ ] **Bound the in-memory dictionaries.** `todos._items_cache`, `chat_history_storage`
      and the anti-spam timestamps gain an entry per user and never lose one. Small each,
      but unbounded; evict on read or on a timer.
- [ ] **Cap the assistant per user per day.** The anti-spam limit is one message every
      two seconds, which bounds nothing over a day. Every message also resends the whole
      knowledge base in the prompt; keep it short or trim it.
- [ ] **Thin the notification cycle.** A user costs a login plus up to five requests per
      subject every 30 minutes. Candidates: a longer interval inside quiet hours, and
      skipping the per-subject reads that cannot produce a message. Do not trade
      correctness for requests: the 1h and 2h reminders must still be on time.
- [ ] **Drop `bs4==0.0.2`** from `requirements.txt`: it is a stub that only pulls in
      `beautifulsoup4`, which is already pinned. (`pycryptodome` is used by `kw.py` and
      `qr.py`; keep it.)
- [ ] **Keep image files off the hot path.** The ID photo is cached under the working
      directory, not `DATA_DIR`, so Railway fetches it again after every deploy
      ([entry](KNOWN_ISSUES.md#a-cached-id-photo-is-never-refreshed)).

## Phase 3 — Usefulness

New behaviour, picked for value per request made to KLAS. Each needs the browser
capture first ([KLAS.md](KLAS.md#when-klas-changes)); none of the endpoints is known to
work yet.

Ranked, highest first:

1. [ ] **Daily digest.** One message at a chosen hour: what is due in the next 48 hours,
       in one place. Built from data the cycle already reads, so no extra requests.
2. [ ] **"Done" and per-subject mute.** A button on a reminder that stops further
       reminders for that assignment, and a switch to silence a whole subject. The
       `sent_notifications` table already gives the identity to key on
       ([NOTIFICATIONS.md](NOTIFICATIONS.md)).
3. [ ] **Calendar export.** `/show` as an `.ics` file. Generated locally, so no KLAS
       traffic and no stored data.
4. [ ] **New grade posted.** `/info` already reads grades on request; a notification
       needs a stored previous value per user and a decision about where to keep it.
5. [ ] **Course notices from KLAS.** Highest value for students, highest cost: a new
       endpoint, a new category and a new per-subject request in every cycle.
6. [ ] **Library loans due.** `kupis.kw.ac.kr` is already reached for `/search`; a due
       date reminder needs a logged-in loans endpoint.
7. [ ] **Cafeteria menu at a chosen time**, opt-in. Reuses the cached menu; no KLAS
       load.

Anything that adds a per-user table needs `delete_all_user_data` to clear it and a line in
[SECURITY.md](SECURITY.md#what-is-stored).

## Phase 4 — Upkeep

- [ ] A guarded `ADD COLUMN` in `_apply_data_fixes`, so a column can reach existing
      users ([entry](KNOWN_ISSUES.md#a-new-column-cannot-reach-an-existing-database)).
- [ ] A key-rotation script
      ([entry](KNOWN_ISSUES.md#there-is-no-way-to-rotate-the-encryption-key)).
- [ ] Encrypt the library phone number, or document why it stays plain
      ([entry](KNOWN_ISSUES.md#the-library-phone-number-is-stored-unencrypted)).
- [ ] Move the systemd restart limit to `[Unit]` and rotate `logs/kwbot.log`
      (self-hosting only; [entry](KNOWN_ISSUES.md#the-systemd-restart-limit-is-not-applied)).
- [ ] Tests that need an encryption key should set their own, so a clean checkout is green
      ([entry](KNOWN_ISSUES.md#five-tests-need-an-encryption-key)).
- [ ] Re-check the whole bot against a real account at the start of each semester
      ([HANDOFF.md](HANDOFF.md#start-of-a-semester)).

## Not planned

Written down so they are not proposed again without new information.

- **A web dashboard or any exposed port.** It would need hosting, auth and a domain for no
  gain over Telegram.
- **A database server.** SQLite on a volume is enough for this load, and a second service
  is a second RAM bill.
- **Hashing stored passwords.** The bot logs into KLAS as the user, so passwords must be
  recoverable ([SECURITY.md](SECURITY.md)).
- **Following the host `TZ`.** All date logic is Seoul time on purpose
  ([TIMEZONE.md](TIMEZONE.md)).
