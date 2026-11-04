import asyncio
import aiosqlite
import pytest
from pydantic import ValidationError

from reachmax.config import EmailChannelConfig, EnvConfig, MailboxConfig, load_config
from reachmax.mailboxes import MailboxRegistry, configured_mailboxes, mailbox_token_path
from reachmax.state import MIGRATIONS, StateManager
from reachmax.models.company import Company
from reachmax.models.prospect import Prospect


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
    assert config.writer.email_rules == "skills/email_rules.md"
    from reachmax.config import load_email_rules
    assert "specific pain point" in load_email_rules(config)


@pytest.mark.asyncio
async def test_mailbox_migration_backfills_legacy_rows_and_is_idempotent(tmp_path):
    db_path = tmp_path / "harvey-shaped.db"
    async with aiosqlite.connect(db_path) as db:
        for script in MIGRATIONS[:14]:
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


@pytest.mark.asyncio
async def test_agents_pick_up_mailbox_changes_on_config_reload(tmp_path):
    from reachmax.agents.handler import Handler
    from reachmax.agents.sender import Sender

    state = StateManager(str(tmp_path / "reachmax.db"))
    await state.init_db()
    config = load_config("reachmax.yaml")
    sender = Sender(None, state, config, EnvConfig())
    handler = Handler(None, state, config, EnvConfig())
    assert sender.mailbox_registry is None and handler.mailbox_registry is None

    config.channels.email.mailboxes = [MailboxConfig(**mailbox())]
    await sender.refresh_mailboxes()
    await handler.refresh_mailboxes()
    assert sender.mailbox_registry.get("sales_a").address == "alex@example.com"
    assert handler.mailbox_registry.get("sales_a").address == "alex@example.com"


@pytest.mark.asyncio
async def test_mailbox_rate_limit_streak_resets_after_success(tmp_path):
    state = StateManager(str(tmp_path / "reachmax.db"))
    await state.init_db()
    await state.sync_mailboxes([MailboxConfig(**mailbox())])
    await state.record_mailbox_event("sales_a", "rate_limited")
    await state.record_mailbox_event("sales_a", "rate_limited")
    assert await state.mailbox_rate_limit_attempts("sales_a") == 2
    await state.record_mailbox_event("sales_a", "sent")
    assert await state.mailbox_rate_limit_attempts("sales_a") == 0


@pytest.mark.asyncio
async def test_transactional_claim_reserves_daily_cap_across_workers(tmp_path):
    from datetime import datetime, timedelta

    state = StateManager(str(tmp_path / "reachmax.db"))
    await state.init_db()
    await state.sync_mailboxes([MailboxConfig(**mailbox())])
    now = datetime.utcnow()
    async with aiosqlite.connect(state.db_path) as db:
        for item_id in ("out-a", "out-b"):
            await db.execute(
                "INSERT INTO outbox(id, status, send_at, mailbox_id) VALUES (?, 'approved', ?, 'sales_a')",
                (item_id, (now - timedelta(seconds=1)).isoformat()),
            )
        await db.commit()

    async def claim(item_id):
        return await state.claim_mailbox_sequence(
            item_id, "sales_a", daily_cap=1,
            day_start=(now - timedelta(days=1)).isoformat(),
            day_end=(now + timedelta(days=1)).isoformat(),
            mailbox_gap_seconds=0, global_gap_seconds=0, now=now,
        )

    assert sum(await asyncio.gather(claim("out-a"), claim("out-b"))) == 1


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
    assert registry.eligible("sales_a", datetime(2026, 1, 5, 21, 59), recipient)
    assert not registry.eligible("sales_a", datetime(2026, 1, 5, 22, 0), recipient)


def test_mailbox_and_fixed_timezone_modes():
    from reachmax.config import SendWindowConfig

    config = load_config("reachmax.yaml")
    config.channels.email.send_window.timezone_mode = "mailbox"
    config.channels.email.mailboxes = [
        MailboxConfig(**mailbox(id="sales_a", timezone="America/Los_Angeles")),
        MailboxConfig(**mailbox(id="sales_b", address="sam@example.com",
                                send_window=SendWindowConfig(
                                    start="06:00", end="16:00", timezone_mode="fixed",
                                    timezone="Europe/Paris"))),
    ]
    registry = MailboxRegistry(config)
    assert registry.timezone_for("sales_a") == "America/Los_Angeles"
    assert registry.timezone_for("sales_b") == "Europe/Paris"


@pytest.mark.asyncio
async def test_mailbox_assignment_is_sticky_at_company_level(tmp_path):
    state = StateManager(str(tmp_path / "reachmax.db"))
    await state.init_db()
    company = Company(name="Acme", domain="acme.test")
    company_id = await state.add_company(company)
    prospect_a = Prospect(company_id=company_id, first_name="Alex", last_name="A",
                          email="alex@acme.test", title="CEO")
    prospect_b = Prospect(company_id=company_id, first_name="Sam", last_name="B",
                          email="sam@acme.test", title="CEO")
    prospect_a.id = await state.add_prospect(prospect_a)
    prospect_b.id = await state.add_prospect(prospect_b)
    outbox_a = await state.add_outbox_item(prospect_id=prospect_a.id,
        to_email=prospect_a.email, subject="One", body="", send_at="2026-01-01")
    outbox_b = await state.add_outbox_item(prospect_id=prospect_b.id,
        to_email=prospect_b.email, subject="Two", body="", send_at="2026-01-01")
    assert await state.assign_outbox_mailbox(prospect_a.id, outbox_a, "sales_a") == "sales_a"
    # Even when another mailbox is requested, the company identity wins.
    assert await state.assign_outbox_mailbox(prospect_b.id, outbox_b, "sales_b") == "sales_a"
    assert (await state.get_prospect(prospect_b.id)).mailbox_id == "sales_a"
    assert (await state.get_outbox_item(outbox_b))["mailbox_id"] == "sales_a"


@pytest.mark.asyncio
async def test_global_cold_send_slot_persists_across_reservations(tmp_path):
    from datetime import datetime, timedelta

    state = StateManager(str(tmp_path / "reachmax.db"))
    await state.init_db()
    now = datetime(2026, 1, 5, 12, 0)
    assert await state.reserve_global_send_slot(11, now)
    assert not await state.reserve_global_send_slot(11, now + timedelta(seconds=10))
    assert await state.reserve_global_send_slot(11, now + timedelta(seconds=11))


@pytest.mark.asyncio
async def test_handler_polls_each_configured_mailbox(tmp_path):
    from reachmax.agents.handler import Handler

    class Provider:
        name = "fake"
        def __init__(self): self.reads = 0; self.confirmed = 0
        def is_configured(self): return True
        async def get_replies(self): self.reads += 1; return []
        def confirm_read(self): self.confirmed += 1

    config = load_config("reachmax.yaml")
    config.channels.email.mailboxes = [MailboxConfig(**mailbox(id="sales_a")),
                                       MailboxConfig(**mailbox(id="sales_b", address="sam@example.com"))]
    state = StateManager(str(tmp_path / "reachmax.db"))
    await state.init_db()
    handler = Handler(None, state, config, EnvConfig())
    providers = {mid: Provider() for mid in ("sales_a", "sales_b")}
    handler.mailbox_registry.provider = lambda mid: providers[mid]
    assert await handler._run_native()
    assert all(provider.reads == 1 and provider.confirmed == 1 for provider in providers.values())


@pytest.mark.asyncio
async def test_bounce_threshold_pauses_only_that_mailbox(tmp_path):
    state = StateManager(str(tmp_path / "reachmax.db"))
    await state.init_db()
    await state.sync_mailboxes([MailboxConfig(**mailbox(id="sales_a")),
                                MailboxConfig(**mailbox(id="sales_b", address="sam@example.com"))])
    for _ in range(20):
        await state.record_mailbox_event("sales_a", "sent")
        await state.record_mailbox_event("sales_b", "sent")
    sent, bounces, paused = await state.record_mailbox_bounce("sales_a", 0.03, 100, 20)
    assert (sent, bounces, paused) == (20, 1, True)
    assert (await state.get_mailbox("sales_a"))["status"] == "paused"
    assert (await state.get_mailbox("sales_b"))["status"] == "active"


@pytest.mark.asyncio
async def test_multi_mailbox_cold_send_requires_both_compliance_fields(tmp_path):
    from reachmax.agents.sender import Sender
    from reachmax.config import EnvConfig
    from reachmax.models.prospect import Prospect

    config = load_config("reachmax.yaml")
    config.channels.email.mailboxes = [MailboxConfig(**mailbox())]
    config.compliance.postal_address = ""
    config.compliance.opt_out_line = ""
    state = StateManager(str(tmp_path / "reachmax.db"))
    await state.init_db()
    sender = Sender(None, state, config, EnvConfig())
    prospect = Prospect(id="p", email="alex@example.com", email_status="verified")
    status, reason = await sender._blocked(
        {"kind": "sequence", "step": 1, "to_email": "alex@example.com",
         "subject": "hello", "body": "Hi"}, prospect, False)
    assert status == "failed"
    assert "compliance.postal_address" in reason
    assert "compliance.opt_out_line" in reason
