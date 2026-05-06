from __future__ import annotations

import json

from app.db import Base, SessionLocal, engine
from app.operator_models import OperatorTask
from app.services.operator_queue import list_operator_tasks

PREFIX = "oq-page-"


def _ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


def _reset_data() -> None:
    _ensure_schema()
    db = SessionLocal()
    try:
        db.query(OperatorTask).filter(OperatorTask.source_id.like(f"{PREFIX}%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _seed_tasks(priorities: list[int], statuses: list[str] | None = None) -> list[int]:
    statuses = statuses or ["pending"] * len(priorities)
    db = SessionLocal()
    try:
        ids = []
        for idx, (priority, status) in enumerate(zip(priorities, statuses), start=1):
            row = OperatorTask(
                source_type="pytest",
                source_id=f"{PREFIX}{idx}",
                task_type="handoff" if idx % 2 else "bridge_unresolved",
                status=status,
                priority=priority,
                reason_code="pagination",
                payload_json=json.dumps({"idx": idx}),
            )
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
        return ids
    finally:
        db.close()


def _page_all(limit: int = 2) -> list[int]:
    db = SessionLocal()
    try:
        cursor = None
        seen: list[int] = []
        for _ in range(10):
            page = list_operator_tasks(db, source_type="pytest", cursor=cursor, limit=limit)
            seen.extend(item["id"] for item in page["items"] if str(item.get("source_id", "")).startswith(PREFIX))
            cursor = page["next_cursor"]
            if cursor is None:
                break
        return seen
    finally:
        db.close()


def test_simple_id_desc_cursor_does_not_skip_extra_row_when_priorities_equal():
    _reset_data()
    ids = _seed_tasks([40, 40, 40, 40, 40])
    expected = sorted(ids, reverse=True)

    seen = _page_all(limit=2)

    assert seen == expected
    assert len(seen) == len(set(seen))
    assert set(seen) == set(ids)


def test_compound_priority_id_cursor_is_stable_without_duplicates_or_omissions():
    _reset_data()
    ids = _seed_tasks(
        [10, 20, 10, 30, 20],
        statuses=["pending", "assigned", "pending", "dropped", "resolved"],
    )

    db = SessionLocal()
    try:
        expected_rows = (
            db.query(OperatorTask)
            .filter(OperatorTask.id.in_(ids))
            .order_by(OperatorTask.priority.asc(), OperatorTask.id.desc())
            .all()
        )
        expected = [row.id for row in expected_rows]
    finally:
        db.close()

    seen = _page_all(limit=2)

    assert seen == expected
    assert len(seen) == len(set(seen))
    assert set(seen) == set(ids)


def test_next_cursor_points_to_first_unseen_row_not_extra_row():
    _reset_data()
    ids = _seed_tasks([40, 40, 40, 40, 40])
    expected = sorted(ids, reverse=True)

    db = SessionLocal()
    try:
        page1 = list_operator_tasks(db, source_type="pytest", limit=2)
        assert [item["id"] for item in page1["items"]] == expected[:2]
        assert page1["next_cursor"] == expected[2]

        page2 = list_operator_tasks(db, source_type="pytest", cursor=page1["next_cursor"], limit=2)
        assert [item["id"] for item in page2["items"]][:1] == [expected[2]]
    finally:
        db.close()
