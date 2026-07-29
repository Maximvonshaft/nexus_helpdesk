from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/r15-today-priority.db")

from app.db import Base
from app.enums import (
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.model_registry import register_all_models
from app.models import Team, Ticket, User
from app.services.today_workbench_service import build_today_workbench
from app.utils.time import utc_now

register_all_models()


def test_today_workbench_returns_global_top_six_beyond_eighty_rows(
    tmp_path: Path,
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'today-priority.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as db:
        team = Team(name="R15 Priority Team", team_type="support", is_active=True)
        db.add(team)
        db.flush()
        user = User(
            username="r15-priority-admin",
            display_name="R15 Priority Admin",
            email="r15-priority@example.test",
            password_hash="x",
            role=UserRole.admin,
            team_id=team.id,
            is_active=True,
        )
        db.add(user)
        db.flush()
        now = utc_now()
        for index in range(84):
            db.add(
                Ticket(
                    ticket_no=f"R15-NORMAL-{index:03d}",
                    title="Normal SLA candidate",
                    description="Representative volume row",
                    source=TicketSource.manual,
                    source_channel=SourceChannel.web_chat,
                    priority=TicketPriority.medium,
                    status=TicketStatus.in_progress,
                    team_id=team.id,
                    assignee_id=user.id,
                    first_response_due_at=now + timedelta(minutes=120 + index),
                    resolution_due_at=now + timedelta(minutes=240 + index),
                )
            )
        db.flush()

        urgent_numbers: list[str] = []
        for offset in range(6):
            number = f"R15-URGENT-{offset:02d}"
            urgent_numbers.append(number)
            db.add(
                Ticket(
                    ticket_no=number,
                    title="Overdue customer harm",
                    description="Inserted after the first eighty-four rows",
                    source=TicketSource.manual,
                    source_channel=SourceChannel.web_chat,
                    priority=TicketPriority.urgent,
                    status=TicketStatus.in_progress,
                    team_id=team.id,
                    assignee_id=user.id,
                    first_response_due_at=now - timedelta(minutes=60 - offset),
                    resolution_due_at=now + timedelta(minutes=30),
                    first_response_breached=True,
                )
            )
        db.commit()

        payload = build_today_workbench(db, user)
        observed = [row["ticket_no"] for row in payload["sla_priorities"]]
        assert observed == urgent_numbers
        assert all(row["overdue"] is True for row in payload["sla_priorities"])
        assert all(row["minutes_to_due"] < 0 for row in payload["sla_priorities"])

    Base.metadata.drop_all(engine)
    engine.dispose()
