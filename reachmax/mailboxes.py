"""Mailbox configuration and runtime registry.

This module centralizes the bridge between the existing single-mailbox
configuration and the multi-mailbox sending model. Provider instances are
added in the provider-refactor phase; the registry here keeps configuration
and persisted mailbox state synchronized first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytz

from reachmax.config import (MailboxConfig, ReachMaxConfig, SendWindowConfig,
                             WarmupConfig, load_mailbox_env, operator_timezone)
from reachmax.paths import TOKEN_DIR


def mailbox_token_path(mailbox_id: str) -> Path:
    """Private OAuth token path for one account; ids cannot escape data/tokens."""
    mailbox_id = str(mailbox_id or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", mailbox_id):
        raise ValueError("mailbox id must be a short slug")
    return TOKEN_DIR / f"{mailbox_id}.json"


def configured_mailboxes(config: ReachMaxConfig) -> list[MailboxConfig]:
    """Return configured senders, synthesizing the legacy ``default`` sender.

    With no ``channels.email.mailboxes`` list, existing Reach Max setups keep
    their single sender address, provider, send cap, warmup and window.
    """
    email = config.channels.email
    if email.mailboxes:
        return list(email.mailboxes)
    values = dict(
        id="default",
        label="Default mailbox",
        provider=email.provider,
        address=config.persona.email,
        from_name=config.persona.name,
        max_daily_sends=email.max_daily_sends,
        warmup=email.warmup,
        send_window=email.send_window,
        enabled=email.enabled,
        max_bounce_rate=email.max_bounce_rate,
    )
    # Preserve all existing single-mailbox configurations exactly as loaded.
    # Their settings were validated by the legacy config model; stricter
    # mailbox validation applies to newly configured pool entries.
    return [MailboxConfig.model_construct(**values)]


@dataclass
class MailboxRegistry:
    """Config-backed mailbox inventory; database state remains authoritative."""

    config: ReachMaxConfig
    env: object | None = None
    mailboxes: list[MailboxConfig] = field(init=False)
    by_id: dict[str, MailboxConfig] = field(init=False)

    def __post_init__(self) -> None:
        self.mailboxes = configured_mailboxes(self.config)
        self.by_id = {mailbox.id: mailbox for mailbox in self.mailboxes}

    async def sync(self, state) -> None:
        """Sync YAML settings into SQLite without deleting mailbox history."""
        await state.sync_mailboxes(self.mailboxes)

    def get(self, mailbox_id: str) -> MailboxConfig | None:
        return self.by_id.get(mailbox_id)

    def provider(self, mailbox_id: str):
        """Create an isolated native provider client for one configured id."""
        mailbox = self.get(mailbox_id)
        if mailbox is None or not mailbox.enabled:
            return None
        from reachmax.integrations.mail_provider import get_mail_provider
        legacy = not bool(self.config.channels.email.mailboxes)
        env = (self.env if legacy and self.env is not None else
               load_mailbox_env(mailbox.id, mailbox.provider, legacy=legacy))
        return get_mail_provider(
            self.config, env, mailbox=mailbox,
            token_file=mailbox_token_path(mailbox.id))

    def effective_cap(self, mailbox_id: str, today: date | None = None) -> int:
        mailbox = self.get(mailbox_id)
        if mailbox is None:
            return 0
        return mailbox.warmup.daily_cap(mailbox.max_daily_sends, today)

    def window(self, mailbox_id: str) -> SendWindowConfig | None:
        mailbox = self.get(mailbox_id)
        return (mailbox.send_window if mailbox and mailbox.send_window is not None
                else self.config.channels.email.send_window)

    def timezone_for(self, mailbox_id: str, recipient=None) -> str:
        window = self.window(mailbox_id)
        mailbox = self.get(mailbox_id)
        if window is None:
            return operator_timezone(self.config)
        mode = getattr(window, "timezone_mode", "recipient")
        if mode == "fixed":
            return window.timezone
        if mode == "mailbox":
            return window.timezone or getattr(mailbox, "timezone", "") or operator_timezone(self.config)
        return (getattr(recipient, "timezone", "") or operator_timezone(self.config))

    def eligible(self, mailbox_id: str, now: datetime, recipient=None, *,
                 status: str = "active", sent_today: int = 0,
                 last_sent_at: datetime | None = None,
                 cooldown_until: datetime | None = None) -> bool:
        """Apply state, mailbox cap, per-mailbox gap, cooldown, and local window."""
        mailbox = self.get(mailbox_id)
        if mailbox is None or not mailbox.enabled or status not in {"active", "warming"}:
            return False
        if now.tzinfo is None:
            now_utc = pytz.utc.localize(now)
        else:
            now_utc = now.astimezone(pytz.utc)
        operator_tz = pytz.timezone(operator_timezone(self.config))
        today = now_utc.astimezone(operator_tz).date()
        cap = self.effective_cap(mailbox_id, today)
        if cap <= 0 or sent_today >= cap:
            return False
        if cooldown_until is not None and now < cooldown_until:
            return False
        gap = self.config.channels.email.min_gap_per_mailbox_seconds
        if last_sent_at is not None and now - last_sent_at < timedelta(seconds=gap):
            return False
        window = self.window(mailbox_id)
        if window is None:
            return True
        zone = self.timezone_for(mailbox_id, recipient)
        return in_window_utc(now, zone, window)

    def pick(self, candidates: list[str], sent_today: dict[str, int] | None = None,
             last_sent_at: dict[str, datetime | None] | None = None,
             strategy: str | None = None) -> str | None:
        """Choose among already-eligible ids using the configured strategy."""
        sent_today = sent_today or {}
        last_sent_at = last_sent_at or {}
        if not candidates:
            return None
        strategy = strategy or self.config.channels.email.mailbox_strategy
        if strategy == "round_robin":
            ordered = sorted(candidates)
            previous = getattr(self, "_last_picked", "")
            picked = next((mid for mid in ordered if mid > previous), ordered[0])
            self._last_picked = picked
            return picked
        def score(mid):
            cap = max(1, self.effective_cap(mid))
            used = sent_today.get(mid, 0) / cap
            if strategy == "weighted":
                used /= max(1, self.get(mid).weight)
            last = last_sent_at.get(mid)
            return used, last or datetime.min, mid
        return min(candidates, key=score)


def in_window_utc(now: datetime, zone: str, window: SendWindowConfig) -> bool:
    """Use the established DST-aware send-window implementation."""
    from reachmax.send_window import in_window
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    return in_window(now, zone or "UTC", window)

