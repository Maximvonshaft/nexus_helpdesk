from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, func, or_
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/r15-control-tower-join.db")

from app.db import Base
from app.enums import TicketPriority, TicketSource, TicketStatus, UserRole
from app.model_registry import register_all_models
from app.models import Team, Tenant, Ticket, User
from app.operator_models import OperatorTask
from app.services.tenant_query_authority import ActorTenantQueryScope

register_all_models()


def test_operator_task_ticket_join_is_emitted_once_and_executes(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'control-tower.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as db:
        tenant = Tenant(
            tenant_key="r15-control",
            display_name="R15 Control",
            is_active=True,
        )
        db.add(tenant)
        db.flush()
        team = Team(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            name="R15 Control Team",
            is_active=True,
        )
        db.add(team)
        db.flush()
        user = User(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            username="r15-control-user",
            display_name="R15 Control User",
            email="r15-control@example.test",
            password_hash="x",
            role=UserRole.agent,
            team_id=team.id,
            is_active=True,
        )
        ticket = Ticket(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            ticket_no="R15-CONTROL-001",
            title="Bounded Control Tower work",
            description="Canonical join regression",
            source=TicketSource.manual,
            priority=TicketPriority.high,
            status=TicketStatus.pending_assignment,
            team_id=team.id,
        )
        db.add_all([user, ticket])
        db.flush()
        db.add(
            OperatorTask(
                tenant_id=tenant.id,
                source_type="test",
                source_id="r15-control-task",
                ticket_id=ticket.id,
                task_type="control_tower_action",
                status="pending",
                priority=40,
            )
        )
        db.commit()

        scope = ActorTenantQueryScope(
            tenant_id=tenant.id,
            tenant_key=tenant.tenant_key,
        )
        query = (
            scope.operator_tasks(db)
            .outerjoin(Ticket, OperatorTask.ticket_id == Ticket.id)
            .filter(
                or_(
                    Ticket.team_id == user.team_id,
                    Ticket.assignee_id == user.id,
                    OperatorTask.assignee_id == user.id,
                )
            )
        )
        compiled = str(
            query.statement.compile(
                dialect=engine.dialect,
                compile_kwargs={"literal_binds": True},
            )
        ).lower()
        assert compiled.count("left outer join tickets") == 1
        assert int(query.with_entities(func.count(OperatorTask.id)).scalar() or 0) == 1

    Base.metadata.drop_all(engine)
    engine.dispose()
