# Multi-mailbox sending

Reach Max keeps the existing prospecting pipeline and adds mailbox-specific
sender identities and runtime state. A single sender remains the default: when
`channels.email.mailboxes` is absent or empty, Reach Max derives one `default`
mailbox from `persona.email`, `persona.name`, and the existing email provider,
cap, warmup, and send-window settings.

## Configuration

Mailbox settings live in `reachmax.yaml` and contain no credentials:

```yaml
channels:
  email:
    provider: gmail
    require_approval: true
    send_interval_seconds: 15
    send_jitter_seconds: 4
    mailbox_strategy: least_used
    min_gap_per_mailbox_seconds: 120
    bounce_window_sends: 100
    failover_followups: false
    mailboxes:
      - id: sales_a
        label: Sales A
        provider: gmail
        address: alex@example.com
        from_name: Alex Rivera
        max_daily_sends: 40
        weight: 1
        enabled: true
```

Mailbox ids must be unique short slugs. Sender addresses must be unique and
valid, and every mailbox needs a real `from_name`, a supported provider, and a
positive daily cap. `instantly` remains a single-provider configuration and
cannot be combined with a `mailboxes` list.

The sender list is synced into `data/reachmax.db` at runtime. Removing an id
marks its row disabled so send history and mailbox events remain available.
Existing records receive the `default` mailbox id during the additive schema
migration.

## Credentials

Keep credentials out of YAML. Existing single-provider settings use the
`REACHMAX_*` names in `.env.example`. Per-mailbox SMTP credentials use the
mailbox id in uppercase, for example `REACHMAX_MAILBOX_SALES_A_SMTP_PASSWORD`
and `REACHMAX_MAILBOX_SALES_A_IMAP_PASSWORD`. Gmail and Microsoft OAuth tokens
belong under `data/tokens/<mailbox-id>.json`; that directory is local data and
must not be committed or exposed through the dashboard.

## Current implementation stage

The local copy has the mailbox configuration model, legacy default-mailbox
synthesis, database migration, and config-to-database sync. Provider
instantiation, mailbox selection, per-mailbox eligibility, sending, reply
routing, and dashboard controls are being implemented in subsequent phases.
Until those phases land, configure and use Reach Max as a single-mailbox
sender.
