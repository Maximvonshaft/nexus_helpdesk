from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/r15-speedaf-processing.db")

from app.api import speedaf_actions
from app.db import Base
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole
from app.model_registry import register_all_models
from app.models import Customer, Team, Tenant, Ticket, TicketEvent, User
from app.services.data_subject_action_service import DataProcessingRestricted

register_all_models()


def test_synchronous_speedaf_lookup_rechecks_restriction_before_network(
    tmp_path: Path,
    monkeypatch,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'speedaf.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    calls: list[tuple[str, str]] = []

    class _Adapter:
        def query_waybills_by_caller(self, *, caller_id: str, country_code: str):
            calls.append((caller_id, country_code))
            raise AssertionError("Provider I/O must not occur")

    def _blocked(*, ticket_id: int | None, purpose: str) -> None:
        assert ticket_id is not None
        assert purpose == "provider_tool_execution"
        raise DataProcessingRestricted(
            customer_id=1,
            purpose=purpose,
            restriction_id=99,
        )

    monkeypatch.setenv("SPEEDAF_MCP_ENABLED", "true")
    monkeypatch.setattr(speedaf_actions, "SpeedafCoreAdapter", _Adapter)
    monkeypatch.setattr(
        speedaf_actions,
        "ensure_ticket_processing_allowed_fresh",
        _blocked,
    )
    monkeypatch.setattr(
        speedaf_actions,
        "enforce_admin_action_rate_limit",
        lambda *_args, **_kwargs: None,
    )

    with factory() as db:
        tenant = Tenant(
            tenant_key="r15-speedaf",
            display_name="R15 Speedaf",
            is_active=True,
        )
        db.add(tenant)
        db.flush()
        team = Team(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            name="R15 Speedaf Team",
            is_active=True,
        )
        db.add(team)
        db.flush()
        user = User(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            username="r15-speedaf-user",
            display_name="R15 Speedaf User",
            email="r15-speedaf@example.test",
            password_hash="x",
            role=UserRole.admin,
            team_id=team.id,
            is_active=True,
        )
        customer = Customer(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            name="Restricted Customer",
        )
        db.add_all([user, customer])
        db.flush()
        ticket = Ticket(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            ticket_no="R15-SPEEDAF-001",
            title="Restricted Provider lookup",
            description="No network call is permitted",
            source=TicketSource.manual,
            source_channel=SourceChannel.internal,
            priority=TicketPriority.medium,
            status=TicketStatus.in_progress,
            customer_id=customer.id,
            team_id=team.id,
            assignee_id=user.id,
        )
        db.add(ticket)
        db.commit()

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": f"/api/tickets/{ticket.id}/speedaf/waybills/query",
                "headers": [],
                "query_string": b"",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 1234),
                "scheme": "http",
            }
        )
        with pytest.raises(HTTPException) as exc:
            speedaf_actions.query_speedaf_waybills(
                ticket_id=ticket.id,
                payload=speedaf_actions.SpeedafWaybillLookupRequest(
                    callerID="+41790000000",
                    countryCode="CH",
                ),
                request=request,
                db=db,
                current_user=user,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == {
            "code": "data_processing_restricted",
            "purpose": "provider_tool_execution",
        }
        assert calls == []
        event = (
            db.query(TicketEvent)
            .filter(
                TicketEvent.ticket_id == ticket.id,
                TicketEvent.field_name == "speedaf_waybill_lookup",
            )
            .one()
        )
        assert event.new_value == "blocked"
        assert "+41790000000" not in (event.payload_json or "")
        assert "data_processing_restricted" in (event.payload_json or "")

    Base.metadata.drop_all(engine)
    engine.dispose()
