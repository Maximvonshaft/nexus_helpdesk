from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.enums import SourceChannel
from app.model_registry import register_all_models
from app.models import Customer, Ticket, TicketEvent
from app.models_osr import CaseContextRecord
from app.services.nexus_osr import auto_ticket_service
from app.services.nexus_osr.auto_ticket_service import create_or_reuse_ticket_from_case_context
from app.services.nexus_osr.case_context import CaseContext
from app.webchat_models import WebchatConversation

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="requires PostgreSQL DATABASE_URL",
)

register_all_models()


@pytest.fixture()
def pg_database():
    engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    try:
        yield engine, Session
    finally:
        engine.dispose()


def test_postgres_same_conversation_concurrency_creates_one_ticket(pg_database):
    _, Session = pg_database
    suffix = uuid.uuid4().hex
    with Session() as db:
        conversation = WebchatConversation(
            public_id=f"ticket_concurrency_{suffix}",
            visitor_token_hash=f"token-{suffix}",
            tenant_key="tenant-pg",
            channel_key="webchat",
            visitor_name="Concurrent Visitor",
            status="open",
        )
        db.add(conversation)
        db.commit()
        conversation_id = conversation.id
        public_id = conversation.public_id

    barrier = threading.Barrier(2)
    outcomes: list[tuple[int, bool]] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker() -> None:
        db = Session()
        try:
            with db.begin():
                conversation = db.get(WebchatConversation, conversation_id)
                context = CaseContext(
                    conversation_id=conversation_id,
                    channel="webchat",
                    country_code="ME",
                    issue_type="delivery_delay",
                )
                barrier.wait(timeout=20)
                result = create_or_reuse_ticket_from_case_context(
                    db,
                    case_context=context,
                    conversation=conversation,
                    source_channel=SourceChannel.web_chat,
                )
                with guard:
                    outcomes.append((result.ticket.id, result.created))
        except BaseException as exc:  # pragma: no cover - asserted below
            with guard:
                errors.append(exc)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        for future in futures:
            future.result(timeout=40)

    assert not errors
    assert len(outcomes) == 2
    assert len({ticket_id for ticket_id, _ in outcomes}) == 1
    assert sorted(created for _, created in outcomes) == [False, True]

    ticket_id = outcomes[0][0]
    with Session() as db:
        conversation = db.get(WebchatConversation, conversation_id)
        assert conversation.ticket_id == ticket_id
        assert db.query(Ticket).filter(Ticket.id == ticket_id).count() == 1
        assert db.query(Customer).filter(Customer.external_ref == f"webchat:{public_id}").count() == 1
        assert db.query(TicketEvent).filter(TicketEvent.ticket_id == ticket_id).count() == 1
        active_contexts = (
            db.query(CaseContextRecord)
            .filter(
                CaseContextRecord.tenant_id == "tenant-pg",
                CaseContextRecord.conversation_id == conversation_id,
                CaseContextRecord.is_active.is_(True),
            )
            .all()
        )
        assert len(active_contexts) == 1
        assert active_contexts[0].ticket_id == ticket_id


def test_postgres_real_ticket_number_collision_recovers_inside_service_savepoint(pg_database, monkeypatch):
    _, Session = pg_database
    suffix = uuid.uuid4().hex
    common_ticket_no = f"OSR-PG-COLLISION-{suffix[:16]}"
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def generate(case_context, *, attempt=0):
        if attempt == 0:
            barrier.wait(timeout=20)
            return common_ticket_no
        thread_suffix = f"{threading.get_ident():x}"[-8:].upper()
        return f"OSR-PG-{suffix[:8].upper()}-{thread_suffix}-{attempt}"

    monkeypatch.setattr(auto_ticket_service, "_generate_ticket_no", generate)

    def worker(index: int) -> None:
        db = Session()
        try:
            with db.begin():
                result = create_or_reuse_ticket_from_case_context(
                    db,
                    case_context=CaseContext(
                        channel="webchat",
                        country_code="PG",
                        issue_type=f"collision_{suffix}_{index}",
                    ),
                    source_channel=SourceChannel.web_chat,
                )
                with guard:
                    outcomes.append(result.ticket.ticket_no)
        except BaseException as exc:  # pragma: no cover - asserted below
            with guard:
                errors.append(exc)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, index) for index in range(2)]
        for future in futures:
            future.result(timeout=40)

    assert not errors
    assert len(outcomes) == 2
    assert len(set(outcomes)) == 2
    assert common_ticket_no in outcomes
    assert all(len(ticket_no) <= 40 for ticket_no in outcomes)

    with Session() as db:
        assert db.query(Ticket).filter(Ticket.ticket_no.in_(outcomes)).count() == 2
        assert db.query(Ticket).filter(Ticket.ticket_no == common_ticket_no).count() == 1
        assert not db.in_transaction()
