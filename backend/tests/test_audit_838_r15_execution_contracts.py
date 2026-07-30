from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/audit-838-r15-execution-contracts.db",
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import (  # noqa: E402
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.model_registry import register_all_models  # noqa: E402
from app.models import Market, Team, Tenant, Ticket, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.data_subject_action_service import (  # noqa: E402
    DataProcessingRestricted,
)
from app.services.golden_journey_portfolio import (  # noqa: E402
    GoldenJourneyPortfolioError,
    require_selected_scenario,
    selected_scenario_keys,
)
from app.services.knowledge_pdf_safety import (  # noqa: E402
    extract_pdf_text_bounded,
)
from app.services.read_model_contracts import (  # noqa: E402
    _control_tower_operator_tasks,
    _sla_priority_rows,
)
from app.services.tenant_reference_runtime_contract import (  # noqa: E402
    install_tenant_reference_runtime_contract,
)
from app.services.voice_compliance_service import (  # noqa: E402
    apply_session_compliance_state,
)
from app.utils.time import utc_now  # noqa: E402

register_all_models()
install_tenant_reference_runtime_contract()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'r15-contracts.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _tenant(db, key: str) -> Tenant:
    row = Tenant(tenant_key=key, display_name=key, is_active=True)
    db.add(row)
    db.flush()
    return row


def _team(db, tenant: Tenant, name: str) -> Team:
    row = Team(
        tenant_id=tenant.id,
        tenant_assignment_source="pytest",
        tenant_assignment_version="r15",
        name=name,
        team_type="support",
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _agent(db, tenant: Tenant, team: Team, suffix: str) -> User:
    row = User(
        tenant_id=tenant.id,
        tenant_assignment_source="pytest",
        tenant_assignment_version="r15",
        username=f"r15-agent-{suffix}",
        display_name=f"R15 Agent {suffix}",
        email=f"r15-agent-{suffix}@example.test",
        password_hash="x",
        role=UserRole.agent,
        team_id=team.id,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def test_team_and_market_identity_is_unique_inside_tenant_not_across_platform(
    db_session,
):
    tenant_a = _tenant(db_session, "reference-a")
    tenant_b = _tenant(db_session, "reference-b")
    _team(db_session, tenant_a, "Support")
    _team(db_session, tenant_b, "Support")
    db_session.add_all(
        [
            Market(
                tenant_id=tenant_a.id,
                tenant_assignment_source="pytest",
                tenant_assignment_version="r15",
                code="CH",
                name="Switzerland",
                country_code="CH",
                is_active=True,
            ),
            Market(
                tenant_id=tenant_b.id,
                tenant_assignment_source="pytest",
                tenant_assignment_version="r15",
                code="CH",
                name="Switzerland",
                country_code="CH",
                is_active=True,
            ),
        ]
    )
    db_session.flush()

    db_session.add(
        Team(
            tenant_id=tenant_a.id,
            tenant_assignment_source="pytest",
            tenant_assignment_version="r15",
            name="support",
            team_type="support",
            is_active=True,
        )
    )
    with pytest.raises(HTTPException) as exc_info:
        db_session.flush()
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Team name already exists in this Tenant"


def test_golden_journey_portfolio_rejects_unselected_runtime_scenario():
    selected = selected_scenario_keys()
    assert len(selected) == 5
    require_selected_scenario(next(iter(selected)))
    with pytest.raises(
        GoldenJourneyPortfolioError,
        match="scenario_outside_selected_portfolio",
    ):
        require_selected_scenario("customer_complaint_refund")


def test_control_tower_bounded_query_uses_ticket_join_once(db_session):
    tenant = _tenant(db_session, "tower-query")
    team = _team(db_session, tenant, "Tower Team")
    agent = _agent(db_session, tenant, team, "tower")

    class Scope:
        @staticmethod
        def operator_tasks(db):
            return db.query(OperatorTask).outerjoin(
                Ticket,
                Ticket.id == OperatorTask.ticket_id,
            )

    query = _control_tower_operator_tasks(db_session, agent, Scope())
    sql = str(
        query.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert sql.count("LEFT OUTER JOIN tickets") == 1


def test_today_workbench_orders_global_sla_priority_before_limit(db_session):
    tenant = _tenant(db_session, "workbench-priority")
    team = _team(db_session, tenant, "Priority Team")
    agent = _agent(db_session, tenant, team, "priority")
    now = utc_now()
    for index in range(90):
        row = Ticket(
            tenant_id=tenant.id,
            tenant_assignment_source="pytest",
            tenant_assignment_version="r15",
            ticket_no=f"R15-PRIORITY-{index:03d}",
            title=f"Priority {index}",
            description="Representative backlog",
            source=TicketSource.manual,
            source_channel=SourceChannel.web_chat,
            priority=TicketPriority.low,
            status=TicketStatus.pending_assignment,
            team_id=team.id,
            country_code="CH",
            resolution_due_at=now + timedelta(hours=10, minutes=index),
            created_at=now - timedelta(minutes=index),
            updated_at=now - timedelta(minutes=index),
        )
        db_session.add(row)
    db_session.flush()
    urgent = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="pytest",
        tenant_assignment_version="r15",
        ticket_no="R15-PRIORITY-URGENT",
        title="Globally most urgent",
        description="Must not be hidden outside an arbitrary sample",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.urgent,
        status=TicketStatus.pending_assignment,
        team_id=team.id,
        country_code="CH",
        resolution_due_at=now - timedelta(minutes=15),
        resolution_breached=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(urgent)
    db_session.flush()

    rows = _sla_priority_rows(db_session, agent, now)
    assert len(rows) == 6
    assert rows[0]["ticket_id"] == urgent.id
    assert rows[0]["overdue"] is True


def test_voice_policy_authorization_does_not_claim_provider_active(monkeypatch):
    from app.services import voice_compliance_service

    monkeypatch.setattr(
        voice_compliance_service,
        "capability_authorized",
        lambda *_args, **_kwargs: True,
    )
    session = SimpleNamespace(
        recording_status="consent_required",
        transcript_status="consent_required",
        updated_at=None,
    )
    db = SimpleNamespace(flush=lambda: None)
    apply_session_compliance_state(
        db,
        session=session,
        recording_policy="explicit_consent",
        transcription_policy="explicit_consent",
    )
    assert session.recording_status == "authorized"
    assert session.transcript_status == "authorized"
    assert session.transcript_status != "active"


def test_synchronous_speedaf_read_fails_before_provider_when_processing_restricted(
    monkeypatch,
):
    from app.api import speedaf_actions

    def blocked(*_args, **_kwargs):
        raise DataProcessingRestricted(
            customer_id=7,
            purpose="provider_tool_execution",
            restriction_id=11,
        )

    monkeypatch.setattr(
        speedaf_actions,
        "ensure_ticket_processing_allowed_fresh",
        blocked,
    )
    with pytest.raises(HTTPException) as exc_info:
        speedaf_actions._require_provider_processing(123)
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "data_processing_restricted"
    assert exc_info.value.detail["restriction_id"] == 11


def test_pdf_extraction_rejects_page_budget_before_iteration(monkeypatch):
    class FakeReader:
        is_encrypted = False
        pages = [object()] * 501

        def __init__(self, *_args, **_kwargs):
            pass

    monkeypatch.setitem(
        sys.modules,
        "pypdf",
        SimpleNamespace(PdfReader=FakeReader),
    )
    with pytest.raises(HTTPException, match="page extraction budget"):
        extract_pdf_text_bounded(b"%PDF-1.7 bounded-test")
