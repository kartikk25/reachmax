import aiosqlite
import pytest
from pydantic import ValidationError

from reachmax.config import EmailChannelConfig, MailboxConfig, load_config
from reachmax.mailboxes import MailboxRegistry, configured_mailboxes
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
