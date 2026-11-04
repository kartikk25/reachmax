"""Mailbox configuration and runtime registry.

This module centralizes the bridge between the existing single-mailbox
configuration and the multi-mailbox sending model. Provider instances are
added in the provider-refactor phase; the registry here keeps configuration
and persisted mailbox state synchronized first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from reachmax.config import MailboxConfig, ReachMaxConfig, load_mailbox_env
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

