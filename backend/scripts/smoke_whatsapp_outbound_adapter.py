from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DB_PATH = Path(tempfile.gettempdir()) / "nexus_whatsapp_outbound_adapter_smoke.db"
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["APP_ENV"] = "development"
os.environ["AUTO_INIT_DB"] = "false"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["ALLOW_DEV_AUTH"] = "false"
os.environ["DATABASE_URL"] = "sqlite:///" + str(DB_PATH.resolve())
os.environ["SECRET_KEY"] = "whatsapp-outbound-smoke-secret-000000000000000000"
os.environ["ENABLE_OUTBOUND_DISPATCH"] = "true"
os.environ["OUTBOUND_PROVIDER"] = "openclaw"

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.enums import MessageStatus, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import ChannelAccount, Customer, Team, Ticket, TicketOutboundMessage, User  # noqa: E402
from app.services import message_dispatch  # noqa: E402
from app.auth_service import hash_password  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def main() -> int:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    calls: list[dict] = []
    try:
        team = Team(name=f"Ops-{_uid()}", team_type="support")
        user = User(
            username=f"smoke-{_uid()}",
            display_name="Smoke Operator",
            email=f"smoke-{_uid()}@example.test",
            password_hash=hash_password("pass123"),
            role=UserRole.admin,
            team=team,
            is_active=True,
        )
        customer = Customer(name="Alice", phone="+15550123456", email="alice@example.test")
        db.add_all([team, user, customer])
        db.flush()

        account = ChannelAccount(
            provider="whatsapp",
            account_id="wa-smoke-main",
            display_name="WhatsApp Smoke Main",
            is_active=True,
            priority=10,
        )
        db.add(account)
        db.flush()

        ticket = Ticket(
            ticket_no=f"T-{_uid()}",
            title="WhatsApp smoke outbound",
            description="WhatsApp smoke outbound",
            customer_id=customer.id,
            source=TicketSource.user_message,
            source_channel=SourceChannel.whatsapp,
            priority=TicketPriority.medium,
            status=TicketStatus.pending_assignment,
            resolution_category=ResolutionCategory.none,
            team_id=team.id,
            created_by=user.id,
            source_chat_id="+15550123456",
            preferred_reply_channel=SourceChannel.whatsapp.value,
            preferred_reply_contact="+15550123456",
        )
        db.add(ticket)
        db.flush()

        message = TicketOutboundMessage(
            ticket_id=ticket.id,
            channel=SourceChannel.whatsapp,
            status=MessageStatus.processing,
            body="Hello from NexusDesk WhatsApp outbound adapter smoke.",
            provider_status="queued",
            max_retries=3,
            created_by=user.id,
            locked_by="smoke-worker",
        )
        db.add(message)
        db.flush()
        message.ticket = ticket

        def fake_whatsapp_dispatch(db, *, message, ticket, idempotency_key):
            route = {
                "adapter": "whatsapp_openclaw_bridge",
                "channel": "whatsapp",
                "target": ticket.source_chat_id,
                "account_id": account.account_id,
                "thread_id": None,
                "session_key": None,
                "idempotency_key": idempotency_key,
            }
            calls.append({"route": route, "body": message.body})
            return MessageStatus.sent, "sent_via_fake_whatsapp_bridge", utc_now(), route

        message_dispatch.dispatch_whatsapp_outbound = fake_whatsapp_dispatch
        message_dispatch.log_event = lambda *args, **kwargs: None
        message_dispatch._enforce_outbound_safety = lambda *args, **kwargs: True
        message_dispatch.settings.enable_outbound_dispatch = True
        message_dispatch.settings.outbound_provider = "openclaw"

        processed = message_dispatch.process_outbound_message(db, message)
        db.commit()

        evidence = {
            "ok": processed.status == MessageStatus.sent,
            "message_id": processed.id,
            "status": processed.status.value if hasattr(processed.status, "value") else str(processed.status),
            "provider_status": processed.provider_status,
            "sent_at_present": processed.sent_at is not None,
            "conversation_state": ticket.conversation_state.value if hasattr(ticket.conversation_state, "value") else str(ticket.conversation_state),
            "dispatch_calls": calls,
            "db_path": str(DB_PATH),
        }
        print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
        if not evidence["ok"] or not calls:
            return 1
        if calls[0]["route"].get("adapter") != "whatsapp_openclaw_bridge":
            return 1
        if calls[0]["route"].get("account_id") != "wa-smoke-main":
            return 1
        return 0
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
