import aiosqlite
import pytest
from pydantic import ValidationError

from reachmax.config import EmailChannelConfig, MailboxConfig, load_config
from reachmax.mailboxes import MailboxRegistry, configured_mailboxes, mailbox_token_path
from reachmax.state import MIGRATIONS, StateManager


def mailbox(**overrides):
    return {
        "id": "sales_a",
        "label": "Sales A",
        "provider": "gmail",
        "address": "alex@example.com",
        "from_name": "Alex Rivera",
        "max_daily_sends": 40,
        **overrides,
    }


def test_mailbox_config_validates_sender_identity_and_limits():
    item = MailboxConfig(**mailbox())
    assert item.address == "alex@example.com"
    assert item.max_daily_sends == 40

    for broken in (
        mailbox(id="Bad ID"),
        mailbox(address="not-an-email"),
        mailbox(from_name=""),
        mailbox(max_daily_sends=0),
        mailbox(weight=0),
        mailbox(provider="instantly"),
    ):
        with pytest.raises(ValidationError):
            MailboxConfig(**broken)


def test_email_config_rejects_duplicate_mailboxes_and_instantly_pool():
    with pytest.raises(ValidationError, match="duplicate id 'sales_a'"):
        EmailChannelConfig(provider="gmail", mailboxes=[mailbox(), mailbox(address="sam@example.com")])
    with pytest.raises(ValidationError, match="duplicate sender address"):
        EmailChannelConfig(provider="gmail", mailboxes=[mailbox(), mailbox(id="sales_b")])
    with pytest.raises(ValidationError, match="cannot be used with provider: instantly"):
        EmailChannelConfig(provider="instantly", mailboxes=[mailbox()])


def test_legacy_email_config_synthesizes_one_default_mailbox():
    config = load_config("reachmax.yaml")
    senders = configured_mailboxes(config)
    assert len(senders) == 1
    assert (senders[0].id, senders[0].provider, senders[0].address) == (
        "default", config.channels.email.provider, config.persona.email)
    assert senders[0].from_name == config.persona.name
    assert senders[0].max_daily_sends == config.channels.email.max_daily_sends


@pytest.mark.asyncio
async def test_mailbox_migration_backfills_legacy_rows_and_is_idempotent(tmp_path):
    db_path = tmp_path / "harvey-shaped.db"
    async with aiosqlite.connect(db_path) as db:
        for script in MIGRATIONS[:-1]:
            await db.executescript(script)
        await db.execute("PRAGMA user_version = 14")
        await db.execute("INSERT INTO companies(id, name) VALUES ('c1', 'Acme')")
        await db.execute("INSERT INTO prospects(id, company_id, email) VALUES ('p1', 'c1', 'a@acme.test')")
        await db.execute("INSERT INTO conversations(id, prospect_id) VALUES ('v1', 'p1')")
        await db.execute("INSERT INTO outbox(id, prospect_id) VALUES ('o1', 'p1')")
        await db.execute("INSERT INTO actions(id, action_type) VALUES ('a1', 'sent')")
        await db.commit()

    state = StateManager(str(db_path))
    await state.init_db()
    await state.init_db()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("PRAGMA user_version") as cursor:
            assert (await cursor.fetchone())[0] == len(MIGRATIONS)
        for table in ("outbox", "prospects", "companies", "conversations", "actions"):
            async with db.execute(f"SELECT mailbox_id FROM {table}") as cursor:
                rows = await cursor.fetchall()
            if rows:
                assert all(row[0] == "default" for row in rows)
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
            tables = {row[0] for row in await cursor.fetchall()}
        assert {"mailboxes", "mailbox_events"} <= tables


@pytest.mark.asyncio
async def test_registry_sync_updates_config_and_disables_removed_rows(tmp_path):
    state = StateManager(str(tmp_path / "reachmax.db"))
    config = load_config("reachmax.yaml")
    registry = MailboxRegistry(config)
    await registry.sync(state)
    rows = await state.get_mailboxes()
    assert len(rows) == 1
    assert rows[0]["id"] == "default"
    assert rows[0]["address"] == config.persona.email
    assert rows[0]["status"] == "active"

    second = MailboxConfig(**mailbox())
    await state.sync_mailboxes([second])
    await state.record_mailbox_event("sales_a", "sent", "test history")
    await state.sync_mailboxes([])
    rows = {row["id"]: row for row in await state.get_mailboxes()}
    assert rows["sales_a"]["status"] == "disabled"
    async with aiosqlite.connect(state.db_path) as db:
        async with db.execute("SELECT kind, detail FROM mailbox_events WHERE mailbox_id='sales_a'") as cursor:
            assert await cursor.fetchone() == ("sent", "test history")


def test_mailbox_token_paths_are_distinct_and_reject_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr("reachmax.mailboxes.TOKEN_DIR", tmp_path)
    assert mailbox_token_path("sales_a") == tmp_path / "sales_a.json"
    assert mailbox_token_path("sales_b") == tmp_path / "sales_b.json"
    with pytest.raises(ValueError, match="short slug"):
        mailbox_token_path("../outside")


def test_explicit_mailbox_credentials_do_not_reuse_legacy_smtp(monkeypatch):
    from reachmax.config import load_mailbox_env

    monkeypatch.setenv("REACHMAX_SMTP_HOST", "legacy.example")
    monkeypatch.setenv("REACHMAX_SMTP_USERNAME", "legacy@example.com")
    monkeypatch.setenv("REACHMAX_SMTP_PASSWORD", "legacy-secret")
    monkeypatch.setenv("REACHMAX_MAILBOX_SALES_A_SMTP_HOST", "sales.example")
    monkeypatch.setenv("REACHMAX_MAILBOX_SALES_A_SMTP_USERNAME", "alex@example.com")
    monkeypatch.setenv("REACHMAX_MAILBOX_SALES_A_SMTP_PASSWORD", "sales-secret")

    env = load_mailbox_env("sales_a", "smtp")
    assert (env.smtp_host, env.smtp_username, env.smtp_password) == (
        "sales.example", "alex@example.com", "sales-secret")


def test_registry_builds_provider_with_mailbox_identity_and_unique_token(monkeypatch, tmp_path):
    from reachmax.mailboxes import MailboxRegistry

    config = load_config("reachmax.yaml")
    config.channels.email.mailboxes = [MailboxConfig(**mailbox())]
    monkeypatch.setattr("reachmax.mailboxes.TOKEN_DIR", tmp_path)
    monkeypatch.setattr("reachmax.mailboxes.load_mailbox_env", lambda *a, **k: object())
    registry = MailboxRegistry(config)

    provider = registry.provider("sales_a")
    assert provider.mailbox_id == "sales_a"
    assert provider.from_email == "alex@example.com"
    assert provider.from_name == "Alex Rivera"
    assert provider.token_file == tmp_path / "sales_a.json"


def test_registry_caps_warmup_and_selects_lowest_utilization():
    from datetime import date

    config = load_config("reachmax.yaml")
    config.channels.email.mailboxes = [
        MailboxConfig(**mailbox(id="sales_a", warmup={
            "start_date": "2026-01-01", "min_days": 0,
            "start_sends": 5, "daily_increase": 2})),
        MailboxConfig(**mailbox(id="sales_b", weight=10)),
    ]
    registry = MailboxRegistry(config)
    assert registry.effective_cap("sales_a", date(2026, 1, 3)) == 9
    assert registry.pick(["sales_a", "sales_b"], {"sales_a": 4, "sales_b": 20}) == "sales_a"
    assert registry.pick(["sales_a", "sales_b"], {"sales_a": 4, "sales_b": 20},
                         strategy="weighted") == "sales_b"


def test_registry_eligibility_enforces_status_cap_gap_and_window():
    from datetime import datetime, timedelta

    config = load_config("reachmax.yaml")
    config.channels.email.min_gap_per_mailbox_seconds = 120
    config.channels.email.mailboxes = [MailboxConfig(**mailbox())]
    registry = MailboxRegistry(config)
    # 10:00 UTC is 05:00 in New York during standard time.
    recipient = type("Recipient", (), {"timezone": "America/New_York"})()
    now = datetime(2026, 1, 5, 10, 0)
    assert registry.eligible("sales_a", now, recipient, status="active")
    assert not registry.eligible("sales_a", now, recipient, status="paused")
    assert not registry.eligible("sales_a", now, recipient, sent_today=40)
    assert not registry.eligible("sales_a", now, recipient,
                                 last_sent_at=now - timedelta(seconds=119))
    assert not registry.eligible("sales_a", now - timedelta(minutes=1), recipient)
