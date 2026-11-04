# Reach Max: implementation spec for Codex

Paste this whole file into Codex (or save it as `REACH_MAX_SPEC.md` in the repo root and tell Codex to follow it).

---

## 0. Start from a fresh copy of Harvey (nothing is installed yet)

The user has **not downloaded Harvey yet**, so there is no existing install to protect. Reach Max is built from a **fresh copy** of the upstream repo, in its own folder, and the upstream repo is never modified.

**Step 0, before anything else:**

1. Create a new empty working folder and clone Harvey into a folder named `reachmax`:
   ```
   git clone https://github.com/ethanplusai/harvey.git reachmax
   cd reachmax
   rm -rf .git && git init && git add -A && git commit -m "Import Harvey (MIT) as Reach Max baseline"
   ```
   (Or fork `ethanplusai/harvey` on GitHub and rename the fork to `reachmax`. Either way, nothing is ever pushed to `ethanplusai/harvey`: never set it as a push remote.)
2. Create a virtual environment inside the folder (`python3.13 -m venv .venv`, activate it, `pip install -e .` after the rename).
3. All work, installs, tests and commits happen only inside `reachmax/`.
4. If a Harvey install already exists somewhere on the machine (for example the user downloads it later), treat that folder as read-only and never touch it.

**Keep Reach Max separate from any future Harvey install** (so both can run on one machine without clashing):

- Dashboard default port **5556** (Harvey uses 5555).
- Own data folder, database file (`data/reachmax.db`), dashboard token file, and token folder (`data/tokens/`), all inside `reachmax/`.
- Own Docker image, container and Compose project name (`reachmax`), and its own health-check port.
- Only `REACHMAX_*` environment variables are read. Do not read `HARVEY_*`.
- Reach Max never opens, writes to, or migrates any other Harvey folder's database or config.

**Final check Codex must report:** the upstream repo is unmodified (no remote named `origin` pointing at `ethanplusai/harvey` with push access, and the only history in `reachmax/` is the new local one).

---

## 1. Goal

Create **Reach Max**, a fork of `ethanplusai/harvey` (MIT license), that does everything Harvey does today and adds **multi-mailbox sending**.

The finished product must:

1. Send cold email from **many mailboxes** (Gmail, Microsoft 365, SMTP/IMAP) instead of one.
2. Keep the **same dashboard** as a **master view** (totals across all mailboxes) and add a **per-mailbox view** of the same screens.
3. Keep Harvey's client-finding pipeline exactly as it is (discover, profile, enrich, verify, write). Only the **sending layer** changes: one pool of leads, spread across all mailboxes so total outreach volume goes up while each mailbox stays within its own safe limit.
4. Send in a **12-hour window, 05:00 to 17:00** (not 08:00 to 17:00), configurable globally and per mailbox.
5. Send emails that read like a **normal personal email** (one person writing to one person: their specific pain point, then how we can help), not like a business or marketing email. The legal compliance footer stays (see section 3).
6. Keep a **15-second global send pace** across all mailboxes, with a separate **daily cap per mailbox**.

Everything else about Harvey stays the same: the approval ladder, the pre-send gate, signals, cohorts, skills library, Claude/Codex brain, SQLite "facts as rows" model.

---

## 2. How to work

1. **Read the repo first.** Read `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `pyproject.toml`, `harvey.yaml`, `.env.example`, and the code in `harvey/` (especially `main.py`, `state.py`, `gate.py`, `dashboard.py`, `agents/sender.py`, `agents/handler.py`, `integrations/` mail providers, `web/`). File names in this spec come from the README; adapt to what the code actually contains.
2. **Run the existing test suite before changing anything** and record the baseline result. Do not leave the suite red at any phase.
3. **Work in phases** (section 14). One commit per phase with a clear message. Run `pytest` after each phase.
4. **Single-mailbox compatibility is mandatory.** If there is no `mailboxes:` config, Reach Max must behave exactly like Harvey with one mailbox (synthesize a single `default` mailbox from the existing `channels.email` config).
5. If the spec conflicts with what the code does, make the smallest sensible choice, and list the deviation in `CHANGELOG.md` and in your final report.
6. Do not add new heavy dependencies. Prefer the standard library and what `pyproject.toml` already uses.

---

## 3. Non-negotiables (do not weaken these)

These already exist in Harvey. Multi-mailbox must not bypass any of them.

- **CAN-SPAM compliance.** Every cold email keeps a working opt-out line and the sender's **physical postal address** (`compliance.opt_out_line`, `compliance.postal_address`). Truthful subject line. Accurate From name and address. No fake "Re:" or "Fwd:" subjects. No pretending to a prior relationship.
- **In multi-mailbox mode, cold email must not send at all** until `compliance.postal_address` and an opt-out line are set. (Harvey currently only warns. Make this a hard block for cold sends, and show the reason on the dashboard.)
- **Each mailbox sends as its own real identity** (its own address and real `from_name`). No spoofed headers, no forged display names, no reply-to tricks.
- **Opt-outs, bounces, replies, and `compliance.do_not_contact` are global.** If someone opts out or replies via any mailbox, no mailbox ever emails them or anyone at their company again (until a human undoes it). This must hold across mailboxes.
- **Approval ladder stays.** `require_approval: true` stays the default. Autopilot behavior, "Held" emails, and the pre-send gate (`gate.py`) apply to every mailbox unchanged.
- **Bot disclosure rule stays:** if a prospect asks whether they are talking to an AI, Harvey answers truthfully.
- **Secrets never reach the dashboard or the API.** No passwords, OAuth tokens, or app passwords in any API response, log line, or CSV export.
- **Keep the MIT `LICENSE`**, keep the original copyright notice, and credit `ethanplusai/harvey` in the README ("Reach Max is a fork of Harvey").

---

## 4. Rename: Harvey to Reach Max (in the new copy only)

- Python package `harvey/` to `reachmax/`. Update all imports, `pyproject.toml` (project name, entry point), `Dockerfile`, `docker-compose.yml`, `.github/workflows`, tests, docs, and scripts.
- CLI command `harvey` to `reachmax`. Same subcommands (`run`, `dashboard`, `signals`, `discover`, `profile`, `train`, `status`, `usage`, `outbox`, `export`, `companies`, `sending`, `warmup`, `trulyinbox`, `retry-writes`, `gmail`, `microsoft`, `setup`, `install`).
- Config files: `harvey.yaml` to `reachmax.yaml`, `harvey.local.yaml` to `reachmax.local.yaml`. No fallback to the old names (this is a fresh copy, see section 0).
- Data: default DB path `data/reachmax.db`, inside the copy. Never read or write any other Harvey install's `data/harvey.db`.
- Environment variables: new `REACHMAX_*` names only. Do not read `HARVEY_*` (so the two products never share settings).
- Dashboard default port 5556 (update the Dockerfile health check, Compose file, README and `reachmax dashboard` output to match).
- User-facing text: product name "Reach Max" in the CLI banner, dashboard title, sidebar, setup wizard, `CLAUDE.md`, and README. The persona/agent text in `prompts/` can say "Reach Max". Remove the Glengarry quote from the CLI banner if it is only a Harvey flourish; keep it in the README if you want.
- Run a repo-wide search for `harvey`/`Harvey`/`HARVEY` at the end and confirm every remaining hit is intentional (license, attribution, changelog).

---

## 5. Data model

All schema changes go through the existing migration mechanism in `state.py`. Migrations must be **additive and idempotent**, and safe to run on an existing Harvey database.

### New table: `mailboxes`

| column | notes |
|---|---|
| `id` | short slug, primary key (e.g. `mb1`, `default`) |
| `label` | human name shown in the dashboard |
| `address` | the sending email address |
| `from_name` | real display name used in From |
| `provider` | `gmail` / `microsoft` / `smtp` |
| `status` | `active` / `warming` / `paused` / `error` / `disabled` |
| `paused_reason` | text, nullable (e.g. "bounce rate 4.1% over threshold", "auth failed") |
| `paused_at`, `paused_by` | `system` or `user` |
| `max_daily_sends` | integer cap (before warmup ramp) |
| `warmup_start_date` | date, nullable |
| `send_window_json` | optional per-mailbox override of the global window |
| `weight` | integer, default 1 (for weighted distribution) |
| `last_sent_at`, `last_error`, `last_error_at` | |
| `created_at`, `updated_at` | |

Config (YAML) is the source of truth for non-secret settings; the table holds runtime state (status, counters, errors). On startup, sync YAML into the table (add new, update changed, mark removed ones `disabled`, never delete rows that have send history).

### New columns (nullable, backfilled to the `default` mailbox for legacy rows)

- `outbox.mailbox_id`
- `outbox.provider_message_id`, `outbox.thread_key` (whatever the providers need for threading; reuse existing fields if present)
- `conversations.mailbox_id`
- `prospects.mailbox_id` (sticky assignment, see section 7)
- `companies.mailbox_id` (sticky at company level, see section 7)
- `actions.mailbox_id` (nullable; log lines that concern a specific mailbox)
- A small `mailbox_events` table: `(id, mailbox_id, kind, detail, created_at)` with kinds like `sent`, `bounce`, `paused`, `resumed`, `auth_error`, `rate_limited`. Use it for health stats and the dashboard activity feed.

Add indexes on `outbox(mailbox_id, status, send_at)` and `mailbox_events(mailbox_id, created_at)`.

---

## 6. Configuration

Add this to `reachmax.yaml` (documented in `reachmax.yaml` itself and `docs/multi-mailbox.md`):

```yaml
channels:
  email:
    provider: gmail            # legacy single-mailbox setting still works
    require_approval: true
    send_interval_seconds: 15  # GLOBAL pace: at most one cold email leaves every N seconds, across all mailboxes
    send_jitter_seconds: 4     # random +/- jitter around the interval so pacing is not mechanical
    mailbox_strategy: least_used   # round_robin | least_used | weighted
    min_gap_per_mailbox_seconds: 120  # one mailbox never sends twice within this gap
    send_window:
      start: "05:00"
      end: "17:00"
      weekdays_only: true
      timezone_mode: recipient   # recipient | mailbox | fixed
      # timezone: "America/New_York"   # used when timezone_mode: fixed
    max_bounce_rate: 0.03        # applied PER MAILBOX
    bounce_window_sends: 100     # rolling window used for the per-mailbox bounce rate
    failover_followups: false    # if a mailbox is paused, do follow-ups wait (false) or move to another mailbox (never recommended)
    mailboxes:
      - id: mb1
        label: "Sales A"
        provider: gmail
        address: "alex@getexample.com"
        from_name: "Alex Rivera"
        max_daily_sends: 40
        warmup: { start_date: "2026-11-01", min_days: 14, daily_increase: 5 }
        weight: 1
        enabled: true
      - id: mb2
        label: "Sales B"
        provider: microsoft
        address: "sam@tryexample.com"
        from_name: "Sam Patel"
        max_daily_sends: 40
        send_window: { start: "06:00", end: "16:00" }   # optional override
        enabled: true
      - id: mb3
        label: "Support SMTP"
        provider: smtp
        address: "jo@example-mail.com"
        from_name: "Jo Kim"
        max_daily_sends: 30
        enabled: true
```

Rules:

- **Secrets are not in YAML.** SMTP/IMAP passwords come from `.env` using a per-mailbox prefix, e.g. `MAILBOX_MB3_SMTP_HOST`, `MAILBOX_MB3_SMTP_PORT`, `MAILBOX_MB3_SMTP_USER`, `MAILBOX_MB3_SMTP_PASSWORD`, `MAILBOX_MB3_IMAP_HOST`, and so on. Document them in `.env.example`.
- **OAuth tokens (Gmail/Microsoft) are stored per mailbox** under `data/tokens/<mailbox_id>.json` (file mode 600, gitignored, and included in the Docker volume and the "back up these files" docs).
- Validation at startup: unique ids, valid provider, address looks like an email, address unique across mailboxes, caps are positive integers, window times parse, `end > start`. Fail with a message that names the bad entry (match Harvey's existing "name the entry" style).
- `provider: instantly` stays **single-mailbox legacy mode** (Instantly does its own rotation). If `provider: instantly` and `mailboxes:` are both set, refuse to start with a clear error.
- Config hot-reload: follow whatever mechanism Harvey already uses for `harvey.local.yaml` edits (it picks changes up on the next cycle). Mailbox add/remove/enable changes should be picked up the same way.

---

## 7. Mailbox registry and assignment (the core feature)

Create `reachmax/mailboxes.py` (or `integrations/mailboxes.py`) containing:

- A `MailboxRegistry` that loads mailboxes, builds one provider client per mailbox (reuse the existing Gmail/Microsoft/SMTP provider classes; refactor them so credentials/token path are **constructor arguments**, not module-level singletons or a single global env), and exposes `eligible(now, recipient)`, `pick(prospect)`, `record_send`, `record_bounce`, `pause`, `resume`.
- **Eligibility** for a mailbox at time `now` for a given recipient. All of these must be true:
  - `status` is `active` or `warming` (not `paused`/`error`/`disabled`)
  - `now` is inside its send window (the mailbox override if set, else the global window), evaluated per `timezone_mode` (see section 8)
  - sent-today count is below its **effective daily cap**
  - at least `min_gap_per_mailbox_seconds` since its last send
  - it is not in a rate-limit/backoff cooldown
- **Effective daily cap** = `min(max_daily_sends, warmup ramp cap)`. Reuse Harvey's existing warmup ramp (`min_days`, then `+daily_increase` per day) but compute it **per mailbox** from that mailbox's own `warmup_start_date`. Replies are never subject to the cold-email cap (as in Harvey today).
- **Sent-today counting** uses the configured timezone (`usage.quiet_hours.timezone`), not UTC midnight.

### Distribution strategies

- `least_used`: choose the eligible mailbox with the lowest (sent_today / effective_cap). Tie-break by oldest `last_sent_at`.
- `round_robin`: rotate through eligible mailboxes.
- `weighted`: like `least_used` but scaled by `weight`.

### Stickiness rules (important)

1. **First email decides the mailbox.** When a prospect's first email is about to send, pick a mailbox and **store `mailbox_id` on the prospect and on the company**.
2. **Same company, same mailbox.** If any prospect at that company already has a mailbox, every other prospect at that company uses the same one. Two different mailboxes must never email people at the same company.
3. **Follow-ups use the same mailbox as the first email**, so threads stay threaded.
4. **Replies are read from, and answered from, the mailbox that sent the original.**
5. If the sticky mailbox is paused/errored/out of cap/outside its window, the follow-up **waits** (stays queued) unless `failover_followups: true`. Never silently switch a thread to another mailbox. If a reply draft cannot be sent because its mailbox is unavailable for more than 24 hours, escalate it to the human (the existing "handed back" flow) and show why.
6. A paused mailbox's queued first emails that have **not** yet been assigned can be picked up by other mailboxes. Emails already assigned stay with their mailbox.

---

## 8. Send window: 05:00 to 17:00

- Default `send_window` becomes `start: "05:00"`, `end: "17:00"` in the shipped `reachmax.yaml`. This must be a pure config default (the existing `send_window` logic already supports `start`, `end`, `weekdays_only`; do not rewrite it, extend it).
- `timezone_mode: recipient` (default): evaluate the window in the recipient's time zone (existing logic). `timezone_mode: mailbox`: evaluate in a per-mailbox `timezone`. `timezone_mode: fixed`: evaluate in one `timezone` for everyone.
- A per-mailbox `send_window` override replaces the global one for that mailbox only.
- `weekdays_only` keeps meaning Monday to Friday.
- Emails approved or due outside the window wait for the next valid slot (existing behavior). **Replies are never held by the window** (existing behavior).
- Show the effective window and "next send slot" for each mailbox on the dashboard.
- Add tests with times around 04:59, 05:00, 16:59, 17:00, a weekend, a DST change, and recipients in different time zones.

---

## 9. Sender and scheduler changes

Work in `agents/sender.py` and `main.py`.

### Global pacing loop

- Today the Sender drains due and approved items with human-like pacing. Find that pacing and make it a **global** pacing loop: at most **one cold email every `send_interval_seconds` (default 15) plus or minus `send_jitter_seconds`**, across **all** mailboxes combined.
- Each iteration: take the next due, approved, gate-passing outbox item, find or assign its mailbox (section 7), send via that mailbox's provider, record the result, then wait for the pacing interval.
- If no mailbox is eligible for an item, **skip it and try the next item** (do not block the whole queue). Items with nothing eligible stay queued.
- This is separate from the **heartbeat** (default 15 minutes, which decides what to do each cycle). Do not change the heartbeat default. Add an optional `usage.heartbeat_interval_seconds` that overrides `heartbeat_interval_minutes` when set, so a 15-second heartbeat is possible if the user wants it. If the sender drain already runs inside the heartbeat, make sure the drain loop keeps running for as long as there are eligible items, not just once per heartbeat.
- The pace must survive restarts: keep `last_global_send_at` in the DB/settings so a restart does not cause a burst.

### Send path

- Re-check eligibility **immediately before sending** (status, cap, window, gap) to avoid races. Wrap "assign, mark sent, increment counter" in a transaction so two overlapping cycles can never double-send or exceed a cap.
- Run the pre-send gate (`gate.py`) with the chosen mailbox's address as the From (the gate's "recipient matches database record" and domain-link checks use the sending address's domain; this must use the per-mailbox address, not a single global one).
- Append the compliance footer (opt-out line and postal address) exactly as Harvey does.
- On success: update `outbox`, `mailboxes.last_sent_at`, add a `mailbox_events` `sent` row.
- On failure: classify the error.
  - **Auth error:** mark mailbox `error`, set `paused_reason`, stop sending from it, show on dashboard. Do not retry in a loop.
  - **Rate limit / temporary provider error:** exponential backoff cooldown for that mailbox (for example 5, 15, 60 minutes), item stays queued.
  - **Permanent recipient error (hard bounce at send time):** mark the address invalid and cancel that prospect's queued sends (existing behavior).
- Never retry a send whose outcome is unknown without checking the Sent folder or message id first (avoid duplicates).

### Daily cap and ramp

- Enforce the cap per mailbox at send time (see section 7). Show "sent today / cap" on the dashboard.

---

## 10. Handler (reply and bounce) changes

Work in `agents/handler.py` and the mail providers.

- **Poll replies per mailbox.** Each cycle polls every enabled mailbox's inbox (reuse the provider's reply-fetch code, now parameterized by mailbox). A failure in one mailbox must not stop polling the others.
- **Dedup key includes the mailbox** (message id plus mailbox id) so identical message ids across accounts do not collide.
- Match each reply to the original outreach by thread/message headers; set `conversations.mailbox_id` to the mailbox that received it.
- **Stop-on-reply stays global:** a reply to any mailbox stops the sequence for that prospect and for everyone at that company, across all mailboxes.
- **Bounce detection per mailbox:** attribute each bounce to the mailbox that sent the original message. Mark the address `invalid`, cancel queued sends (existing behavior), and record a `bounce` `mailbox_events` row.
- **Per-mailbox bounce rate** = bounces / sends over the last `bounce_window_sends` sends (or the last 7 days, whichever has more data; pick one and document it). If it goes above `max_bounce_rate` (and there are at least 20 sends in the window so tiny samples do not trip it), **pause that mailbox only** with `paused_reason` and `paused_by: system`. Its queued items wait (section 7, rule 5).
- Keep Harvey's existing **global kill switch** for manual "Pause all sending" and for a global bounce threshold across all mailboxes (aggregate rate). The global switch overrides everything.
- Opt-out detection, legal-threat escalation, "Undo opt-out" and the do-not-contact list behave exactly as before and are shared across mailboxes.

---

## 11. Dashboard and API

The dashboard stays plain HTML/CSS/JS in `web/` (no build step, no framework). Keep the token auth, loopback-only binding, Host/Origin checks, and styling system exactly as they are. The Claude/Codex usage numbers are global (one subscription) and are not filtered by mailbox.

### Master view

- With **no mailbox selected**, every screen (Today, Outbox, Conversations, Activity, Campaigns, Companies/Contacts counts, Controls) shows **aggregate data across all mailboxes**, looking the same as Harvey's current dashboard. This is the default and must not regress.
- Add to Today: a compact **"Mailboxes" health strip** (one chip per mailbox: label, status color, sent today / cap, bounce rate). Clicking a chip switches to that mailbox's view.

### Mailbox selector

- A selector at the top of the sidebar: **"All mailboxes (Master)"** plus one entry per mailbox. The choice persists for the browser tab.
- When a mailbox is selected, **the same screens** show only that mailbox's data: its outbox items, its conversations, its activity, its sent/bounce/reply counts and trends, its contacts (by `mailbox_id`). Make the selected scope obvious in the page header (e.g. a pill: "Viewing: Sales A").

### New "Mailboxes" tab (System group)

A table with one row per mailbox:
- label, address, provider, status, paused reason
- sent today / effective cap, warmup progress (day X of Y), bounce rate, reply rate, last sent, next send slot, last error
- actions: **Pause / Resume**, **Test connection**, **Edit** (non-secret fields: label, from_name, cap, weight, window, enabled)
- "Add mailbox" opens a form for non-secret fields and shows the exact CLI command or `.env` lines to add secrets (the UI never accepts or displays passwords or tokens; OAuth is started from the CLI).

### Controls

- Keep the global "Pause all sending". Add **per-mailbox pause/resume**.

### API (in `dashboard.py`)

- `GET /api/mailboxes`: list with health fields (no secrets)
- `POST /api/mailboxes`: create from non-secret fields
- `PATCH /api/mailboxes/{id}`: edit non-secret fields
- `POST /api/mailboxes/{id}/pause`, `/resume`, `/test`
- Add an optional `mailbox_id` query parameter to every existing list/stat endpoint that the dashboard uses (Today stats, outbox, conversations, activity, contacts, campaigns). When absent, return the aggregate (current behavior). Reject unknown ids with 404.
- All endpoints stay behind the existing bearer token and Host/Origin checks. Add tests that a missing token still returns 401/403 for the new endpoints, and that no response contains credential fields.

### Outbox display

- Show which mailbox each email will send from or was sent from, in the Outbox detail pane and the Conversations thread.

---

## 12. Personal-style email (writing rules)

Goal: emails that read like a real person wrote one note to one person. Pain point first, then how we can help. No marketing voice.

- Ship `skills/email_rules.md` as the default for `writer.email_rules` in Reach Max (remember Harvey's built-in shape is email 1 under 75 words, one observation and one question, no pitch). The new rules:
  - Plain text only. No HTML, no logo, no images, no tracking links or pixels.
  - Written as one person to one person ("I", not "we at [Company]"). First name greeting (or the team greeting Harvey already uses for general addresses).
  - Opens with the **specific pain point** observed about that business, taken from confirmed signals (never invented; the FACTS rule stays: nothing about a prospect is invented).
  - Then **one or two plain sentences on how we can help**, concrete and modest. No buzzwords, no feature lists, no "solutions", no "leverage", no "synergy".
  - Ends with one easy question. At most one link in the whole email, and none in email 1 unless needed.
  - Short subject line, truthful, lowercase-friendly, no ALL CAPS, no "Re:", no emoji.
  - Signature is just the sender's first name (and role if configured). No corporate signature block, no banner.
  - Email 1 length target 60 to 110 words (must remain under the gate's 220-word cap).
  - Hard ban list from `email_frameworks.md` still applies.
- **The compliance footer is still appended by the system** (opt-out line and postal address). Make the footer compact and plain (two short plain-text lines at the bottom), so it does not look like a corporate email footer, but it must still be there and still be clear.
- Add a gate check that rejects any cold email missing the opt-out line or postal address in multi-mailbox mode.
- Keep `writer.humanizer` as an option.
- Update `prompts/` and the writer so the sequence (email 1, 2, 3) keeps this personal tone, and update tests/fixtures that assert the old shape only where the rules genuinely changed.

---

## 13. CLI additions

- `reachmax mailbox add` (interactive: id, label, address, from_name, provider, cap, window), writes non-secret fields to YAML and prints the `.env` lines needed
- `reachmax mailbox list` (status, sent today/cap, bounce rate)
- `reachmax mailbox test <id>` (checks auth, can send/receive; sends nothing to prospects)
- `reachmax mailbox pause <id> [--reason ...]` / `resume <id>` / `remove <id>` (remove = disable; keep history)
- `reachmax gmail auth --mailbox <id>` and `reachmax microsoft auth|test --mailbox <id>` (token stored at `data/tokens/<id>.json`)
- `reachmax warmup [domain] [--mailbox <id>|--all]`: run the existing SPF/DKIM/DMARC/age/blocklist/warmup readiness check per mailbox domain; with TrulyInbox connected, read each mailbox's warmup stats if the integration supports multiple accounts, otherwise report "unknown" for that mailbox
- `reachmax status`: add one line per mailbox
- Existing commands keep working with no mailbox flag.

---

## 14. Implementation phases (one commit each; tests green after each)

1. **Copy, baseline and rename.** Do step 0 (fresh clone into `reachmax/`). Run tests. Rename package/CLI/config/DB/env/port (section 4). All tests pass under the new name.
2. **Schema and config.** Migrations (section 5), config models and validation (section 6), `MailboxRegistry` skeleton, legacy single-config to `default` mailbox synthesis. Behavior still identical for existing users.
3. **Provider refactor.** Make Gmail/Microsoft/SMTP providers instantiable per mailbox (credentials/token path as arguments). Per-mailbox OAuth flows and CLI.
4. **Eligibility, caps, windows.** Per-mailbox warmup ramp and cap, send window default 05:00 to 17:00 plus per-mailbox override and timezone modes (sections 7 and 8).
5. **Assignment and stickiness.** Strategies, company-level and prospect-level stickiness, follow-up waiting rules.
6. **Sender pacing.** Global 15-second paced loop with jitter, transactional send path, error classification and backoff, restart-safe pacing (section 9).
7. **Handler.** Per-mailbox reply polling, dedup, bounce attribution, per-mailbox pause, global kill switch retained (section 10).
8. **Dashboard and API.** Mailbox selector, master view, Mailboxes tab, per-mailbox controls, API filters (section 11).
9. **Email style.** `skills/email_rules.md`, compact footer, new gate checks (section 12).
10. **Docs and polish.** README ("Reach Max, a fork of Harvey"), `docs/multi-mailbox.md`, `.env.example`, CHANGELOG, Docker volume notes, Docker run test, final `harvey` grep.

---

## 15. Tests to add (pytest, offline, no real network)

Use fake provider classes. Cover at least:

- Config validation: duplicate ids/addresses, bad times, `instantly` plus `mailboxes` conflict, missing secrets give a clear error.
- Legacy: config with no `mailboxes:` behaves as a single `default` mailbox and existing tests still pass.
- Migration: running migrations on a Harvey-shaped DB adds columns, backfills `default`, and is idempotent.
- Eligibility: status, cap, warmup ramp per mailbox, min gap, window edges (04:59, 05:00, 16:59, 17:00), weekends, DST, recipient time zones, per-mailbox window override.
- Assignment: `least_used`, `round_robin`, `weighted`; sticky by prospect; sticky by company (two prospects, same company, same mailbox); follow-up waits when sticky mailbox is paused; no failover unless enabled.
- Pacing: with N mailboxes and a fake clock, sends are never closer than the global interval minus jitter; no mailbox exceeds its cap or gap; pacing survives a simulated restart; one ineligible item does not block the queue.
- Concurrency: two overlapping drains cannot double-send or exceed a cap.
- Error handling: auth error pauses only that mailbox; rate limit backs off; unknown outcome does not duplicate.
- Handler: replies from mailbox A and B both stop sequences company-wide; dedup across mailboxes; one mailbox's polling failure does not stop the others; bounce attributed to the right mailbox; per-mailbox auto-pause at threshold with the 20-send minimum; global kill switch overrides.
- Compliance: cold email without postal address or opt-out line is blocked; opt-out through mailbox A blocks mailbox B; do-not-contact is honored by every mailbox.
- Gate: uses the per-mailbox From domain for link/domain checks.
- Dashboard API: master vs `mailbox_id` filtering, unknown id 404, auth required, no secret fields in any response.
- Email style: writer output for fixture prospects is plain text, within the word cap, has no HTML/logo/tracking, includes the compact footer.
- Frontend: at minimum, JS smoke checks that exist in the repo's testing approach (if none, skip and manually verify).

---

## 16. Acceptance criteria (the definition of done)

1. `pip install -e .`, `reachmax setup`, `reachmax run`, `reachmax dashboard` all work; the old test suite (renamed) plus the new tests pass.
2. Reach Max lives in its own `reachmax/` folder with its own git history, runs on port 5556 with its own database, tokens and config, and does not touch any other Harvey install. With no `mailboxes:` key, Reach Max behaves exactly like Harvey with one mailbox.
3. With three mailboxes configured, the Sender distributes sends across them, no mailbox exceeds its own daily cap or warmup cap, and global pacing stays at roughly 15 seconds (plus or minus the jitter) between cold sends.
4. Cold email only goes out between 05:00 and 17:00 (in the configured time zone mode), on weekdays when `weekdays_only` is true; replies are not held.
5. Follow-ups and replies always use the mailbox that sent the first email; two mailboxes never email the same company.
6. A reply, bounce, or opt-out through any mailbox is respected by all mailboxes.
7. Pausing or erroring one mailbox leaves the others sending; the global "Pause all sending" stops everything.
8. The dashboard's default view is the master view and matches Harvey's current screens; selecting a mailbox shows the same screens filtered to it; the Mailboxes tab shows health and controls; no secret ever appears in the UI, API, logs, or exports.
9. Generated emails read as plain, personal, pain-point-first notes, and still carry the opt-out line and postal address; cold email is blocked when either is missing.
10. README, `docs/multi-mailbox.md`, `.env.example`, CHANGELOG, and Docker docs are updated, and the LICENSE and attribution to Harvey are intact.

---

## 17. Out of scope (do not build)

- Lead scraping/discovery changes (keep as is).
- Instantly multi-mailbox (it has its own rotation).
- Automatic purchase or creation of domains or mailboxes.
- Any feature that disguises sender identity, forges headers, or removes the opt-out/postal-address footer.
- LinkedIn automation changes.

---

## 18. Final report Codex should return

- Confirmation that nothing points at or pushes to `ethanplusai/harvey` (paste `git remote -v`)
- Summary of each phase and the commit list
- Test results (before and after)
- Every deviation from this spec and why
- New config keys and CLI commands
- Known limitations and anything not verified without real mailboxes (for example live Gmail/Microsoft OAuth and TrulyInbox multi-account behavior)