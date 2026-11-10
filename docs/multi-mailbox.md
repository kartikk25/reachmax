# Multi-mailbox sending

Reach Max keeps the existing prospecting pipeline and adds mailbox-specific
sender identities and runtime state. With no configured `mailboxes` list it
continues using one synthesized `default` mailbox.

The heartbeat remains 15 minutes by default, independently of the global
send pace. Set `usage.heartbeat_interval_seconds` for a shorter cycle; when
omitted, `usage.heartbeat_interval_minutes` controls the heartbeat.

## Configure a pool

Add entries under `channels.email.mailboxes` in `reachmax.yaml` (or your local
override). These example addresses are placeholders; configure only real
mailboxes whose identities you control.

```yaml
channels:
  email:
    provider: gmail
    require_approval: true
    send_interval_seconds: 15
    send_jitter_seconds: 4
    mailbox_strategy: least_used # round_robin | least_used | weighted
    min_gap_per_mailbox_seconds: 120
    bounce_window_sends: 100
    failover_followups: false
    send_window:
      start: "05:00"
      end: "17:00"
      weekdays_only: true
      timezone_mode: recipient # recipient | mailbox | fixed
    mailboxes:
      - id: sales_a
        label: Sales A
        provider: gmail
        address: alex@example.com
        from_name: Alex Rivera
        max_daily_sends: 30
        weight: 1
        enabled: true
```

Follow-ups stay with the mailbox that sent the first message by default. If
`failover_followups` is explicitly enabled, a queued sequence follow-up can
move to another eligible mailbox when its assigned mailbox is unavailable.
Because the replacement mailbox did not send the original message, Reach Max
clears the old provider thread headers and sends the follow-up as a standalone
email. Incoming reply drafts remain assigned to the mailbox that received the
reply.

Each entry needs a unique short id, a unique valid sender address, a real
`from_name`, a supported provider (`gmail`, `microsoft`, or `smtp`), and a
positive daily cap. An optional mailbox `send_window` overrides the global
window. Use `timezone_mode: mailbox` with a mailbox IANA time zone, or
`timezone_mode: fixed` with a fixed IANA time zone. Replies bypass cold-email
windows and warmup caps. A mailbox remains sticky to its prospect and company;
queued follow-ups wait if that sender is paused or ineligible.

The default cold-email cap is 30 per mailbox, not a provider-approved safe
volume. The sender drains all due, approved emails each cycle, subject to
mailbox caps, send windows, and the configured short inter-send pacing.
Follow-ups keep their existing schedule and minimum delay. The legacy
Instantly campaign provider uses its own campaign pacing. Warmup caps apply
only when `warmup.start_date` is set.

The sender list is synced into `data/reachmax.db`. Removing or disabling an id
preserves its history. Existing records receive the `default` mailbox id when
the mailbox columns are first added. New records stay unassigned until the
sender selects a mailbox.

## Credentials and OAuth

Keep credentials out of YAML. SMTP/IMAP credentials use mailbox-specific
`.env` names such as `REACHMAX_MAILBOX_SALES_A_SMTP_PASSWORD`; see
`.env.example`. Gmail and Microsoft OAuth tokens are isolated at
`data/tokens/<mailbox-id>.json`. Back up the database and token directory
together. Neither secrets nor tokens are returned by the dashboard API.

Use `reachmax gmail auth --mailbox sales_a` or
`reachmax microsoft auth --mailbox sales_b` for OAuth. Existing single-sender
commands still work without `--mailbox`.

## Commands and dashboard

- `reachmax mailbox add` interactively adds a mailbox to the active YAML file.
- `reachmax mailbox list` shows runtime state and daily sends.
- `reachmax mailbox test <id>` checks inbox access and submits a tagged
  self-test message to that mailbox's own address. It never contacts prospects.
- `reachmax mailbox pause <id> [--reason ...]` and `resume <id>` control a
  sender; `remove <id>` disables it in YAML while preserving history.
- The dashboard Mailboxes tab shows health, capacity, send windows, and recent
  events. Its Test action sends a uniquely tagged internal message to the
  mailbox itself after checking inbox access; it never uses prospect data or
  contacts a prospect. The mailbox selector filters mailbox-aware screens.

Multi-mailbox cold sends are blocked until both
`compliance.postal_address` and `compliance.opt_out_line` are configured.
The dashboard shows the missing requirement. Reply handling remains global:
replies and opt-outs stop company outreach regardless of receiving mailbox.

OAuth against real accounts, provider-specific SMTP relay behavior, and live
deliverability have not been exercised by the offline test suite. Run each
mailbox connection test before enabling sending, then review every draft with
the default approval ladder.
