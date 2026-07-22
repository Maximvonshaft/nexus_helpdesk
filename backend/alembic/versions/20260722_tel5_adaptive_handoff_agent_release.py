"""publish adaptive human-handoff playbook and atomically deploy new Releases

Revision ID: 20260722_tel5
Revises: 20260722_tel4
Create Date: 2026-07-22
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20260722_tel5"
down_revision = "20260722_tel4"
branch_labels = None
depends_on = None

_PLAYBOOK_KEY = "agent.playbook.human-handoff"
_MIGRATION_ORIGIN = "20260722_tel5_adaptive_handoff"

_PLAYBOOK_V2: dict[str, Any] = {
    "schema_version": "nexus.agent_playbook.v1",
    "name": "human_handoff",
    "display_name": "Human Handoff",
    "description": (
        "Inspect canonical operator capacity, explain evidence-based waiting, "
        "and perform governed human transfer or confirmed follow-up."
    ),
    "tools": [
        "support.availability",
        "handoff.request.create",
        "ticket.create",
    ],
    "instructions": [
        (
            "When the customer requests a person, or policy, legal, privacy, "
            "compensation or authority boundaries require one, call "
            "support.availability before promising a transfer."
        ),
        (
            "Treat the support.availability observation as the only source of "
            "truth for eligible operator status, voice capacity, queue position "
            "and estimated wait. Never invent a wait time or claim availability "
            "without a committed observation."
        ),
        (
            "If eligible capacity is available, explain that a specialist can "
            "join and call handoff.request.create only when transfer matches the "
            "customer's request or the governed boundary."
        ),
        (
            "If all eligible specialists are busy, explain the returned wait "
            "range and confidence. Ask whether the customer prefers to wait, "
            "continue with AI assistance, or request a follow-up."
        ),
        (
            "Use ticket.create only when a real business follow-up is required "
            "and only after the customer explicitly confirms the exact proposed "
            "follow-up. Never create a Ticket merely to start, queue or transfer "
            "a call."
        ),
        (
            "Never state that a Ticket was created, a person accepted the "
            "Conversation, or a transfer completed unless the corresponding "
            "committed Tool observation confirms it."
        ),
    ],
    "priority": 30,
    "channels": [],
    "languages": [],
    "enabled": True,
}


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _replace_playbook_reference(manifest: dict[str, Any]) -> dict[str, Any] | None:
    output = _json_copy(manifest)
    playbooks = output.get("playbooks")
    if not isinstance(playbooks, list):
        return None
    replaced = False
    for reference in playbooks:
        if not isinstance(reference, dict):
            continue
        resource_key = str(
            reference.get("resource_key") or reference.get("key") or ""
        ).strip()
        if resource_key != _PLAYBOOK_KEY:
            continue
        reference["resource_key"] = _PLAYBOOK_KEY
        reference.pop("key", None)
        reference["version"] = 2
        replaced = True
    if not replaced:
        return None
    metadata = output.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["adaptive_handoff_playbook_version"] = 2
    metadata["adaptive_handoff_origin"] = _MIGRATION_ORIGIN
    output["metadata"] = metadata
    return output


def _updated_validation(
    validation: dict[str, Any],
    *,
    previous_release_id: int,
    resource_id: int,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    output = _json_copy(validation)
    resources = output.get("resources")
    if not isinstance(resources, list):
        resources = []
        output["resources"] = resources
    found = False
    for row in resources:
        if not isinstance(row, dict):
            continue
        if str(row.get("resource_key") or "").strip() != _PLAYBOOK_KEY:
            continue
        row["id"] = int(row.get("id") or resource_id)
        row["resource_key"] = _PLAYBOOK_KEY
        row["config_type"] = "playbook"
        row["version"] = 2
        row["content"] = _json_copy(_PLAYBOOK_V2)
        if "snapshot" in row:
            row["snapshot"] = _json_copy(_PLAYBOOK_V2)
        found = True
    if not found:
        resources.append(
            {
                "id": resource_id,
                "resource_key": _PLAYBOOK_KEY,
                "config_type": "playbook",
                "version": 2,
                "tenant_key": "default",
                "is_global_template": True,
                "scope": {
                    "scope_type": "global",
                    "scope_value": None,
                    "market_id": None,
                },
                "content": _json_copy(_PLAYBOOK_V2),
            }
        )

    allowed_tools: set[str] = set()
    for row in resources:
        if not isinstance(row, dict) or row.get("config_type") != "playbook":
            continue
        content = row.get("content")
        if not isinstance(content, dict):
            content = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
        tools = content.get("tools") if isinstance(content, dict) else []
        if isinstance(tools, list):
            allowed_tools.update(
                str(tool).strip() for tool in tools if str(tool).strip()
            )
    output["allowed_tools"] = sorted(allowed_tools)
    output["manifest_sha256"] = _digest(manifest)
    output["adaptive_handoff_migration"] = {
        "origin": _MIGRATION_ORIGIN,
        "previous_release_id": previous_release_id,
        "playbook_resource_id": resource_id,
        "playbook_version": 2,
    }
    return output


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    resource = bind.execute(
        sa.text(
            "SELECT id, published_version FROM ai_config_resources "
            "WHERE resource_key = :resource_key AND config_type = 'playbook'"
        ),
        {"resource_key": _PLAYBOOK_KEY},
    ).mappings().one_or_none()
    if resource is None:
        raise RuntimeError("adaptive_handoff_playbook_resource_missing")
    resource_id = int(resource["id"])
    existing_v2 = bind.execute(
        sa.text(
            "SELECT id FROM ai_config_versions "
            "WHERE resource_id = :resource_id AND version = 2"
        ),
        {"resource_id": resource_id},
    ).scalar_one_or_none()
    if existing_v2 is not None:
        raise RuntimeError("adaptive_handoff_playbook_version_conflict")

    summary = str(_PLAYBOOK_V2["description"])
    bind.execute(
        sa.text(
            """
            INSERT INTO ai_config_versions
                (resource_id, version, snapshot_json, summary, notes,
                 published_by, published_at)
            VALUES
                (:resource_id, 2, :snapshot, :summary, :notes,
                 NULL, :now)
            """
        ),
        {
            "resource_id": resource_id,
            "snapshot": _PLAYBOOK_V2,
            "summary": summary,
            "notes": "Publish adaptive canonical human-handoff playbook",
            "now": now,
        },
    )
    bind.execute(
        sa.text(
            """
            UPDATE ai_config_resources
            SET draft_summary = :summary,
                draft_content_json = :content,
                published_summary = :summary,
                published_content_json = :content,
                published_version = 2,
                published_at = :now,
                updated_at = :now
            WHERE id = :resource_id
            """
        ),
        {
            "summary": summary,
            "content": _PLAYBOOK_V2,
            "now": now,
            "resource_id": resource_id,
        },
    )

    deployment_rows = bind.execute(
        sa.text(
            """
            SELECT id, active_release_id, canary_release_id
            FROM agent_deployments
            WHERE is_active = true
            ORDER BY id
            """
        )
    ).mappings().all()
    candidate_release_ids = sorted(
        {
            int(value)
            for row in deployment_rows
            for value in (row["active_release_id"], row["canary_release_id"])
            if value is not None
        }
    )
    replacement_by_release: dict[int, int] = {}
    latest_manifest_by_definition: dict[int, dict[str, Any]] = {}

    for previous_release_id in candidate_release_ids:
        release = bind.execute(
            sa.text(
                """
                SELECT id, definition_id, version, status, manifest_json,
                       validation_json, created_by, approved_by
                FROM agent_releases
                WHERE id = :release_id
                """
            ),
            {"release_id": previous_release_id},
        ).mappings().one_or_none()
        if release is None or str(release["status"] or "") != "approved":
            continue
        previous_manifest = dict(release["manifest_json"] or {})
        manifest = _replace_playbook_reference(previous_manifest)
        if manifest is None:
            continue
        definition_id = int(release["definition_id"])
        next_version = int(
            bind.execute(
                sa.text(
                    "SELECT COALESCE(MAX(version), 0) + 1 "
                    "FROM agent_releases WHERE definition_id = :definition_id"
                ),
                {"definition_id": definition_id},
            ).scalar_one()
        )
        validation = _updated_validation(
            dict(release["validation_json"] or {}),
            previous_release_id=previous_release_id,
            resource_id=resource_id,
            manifest=manifest,
        )
        new_release_id = int(
            bind.execute(
                sa.text(
                    """
                    INSERT INTO agent_releases
                        (definition_id, version, status, manifest_json,
                         manifest_sha256, validation_json, created_by,
                         approved_by, created_at, approved_at)
                    VALUES
                        (:definition_id, :version, 'approved', :manifest,
                         :manifest_sha256, :validation, :created_by,
                         :approved_by, :now, :now)
                    RETURNING id
                    """
                ),
                {
                    "definition_id": definition_id,
                    "version": next_version,
                    "manifest": manifest,
                    "manifest_sha256": _digest(manifest),
                    "validation": validation,
                    "created_by": release["created_by"],
                    "approved_by": release["approved_by"],
                    "now": now,
                },
            ).scalar_one()
        )
        replacement_by_release[previous_release_id] = new_release_id
        latest_manifest_by_definition[definition_id] = manifest

    for previous_release_id, new_release_id in replacement_by_release.items():
        bind.execute(
            sa.text(
                """
                UPDATE agent_deployments
                SET active_release_id = :new_release_id,
                    updated_at = :now
                WHERE active_release_id = :previous_release_id
                """
            ),
            {
                "new_release_id": new_release_id,
                "previous_release_id": previous_release_id,
                "now": now,
            },
        )
        bind.execute(
            sa.text(
                """
                UPDATE agent_deployments
                SET canary_release_id = :new_release_id,
                    updated_at = :now
                WHERE canary_release_id = :previous_release_id
                """
            ),
            {
                "new_release_id": new_release_id,
                "previous_release_id": previous_release_id,
                "now": now,
            },
        )

    for definition_id, manifest in latest_manifest_by_definition.items():
        bind.execute(
            sa.text(
                """
                UPDATE agent_definitions
                SET draft_manifest_json = :manifest,
                    updated_at = :now
                WHERE id = :definition_id
                """
            ),
            {
                "manifest": manifest,
                "now": now,
                "definition_id": definition_id,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    migrated = bind.execute(
        sa.text(
            "SELECT id, definition_id, validation_json FROM agent_releases "
            "ORDER BY id DESC"
        )
    ).mappings().all()
    restore_by_new_release: dict[int, int] = {}
    definitions: set[int] = set()
    for row in migrated:
        validation = dict(row["validation_json"] or {})
        marker = validation.get("adaptive_handoff_migration")
        if not isinstance(marker, dict) or marker.get("origin") != _MIGRATION_ORIGIN:
            continue
        previous_release_id = int(marker.get("previous_release_id") or 0)
        if previous_release_id <= 0:
            continue
        restore_by_new_release[int(row["id"])] = previous_release_id
        definitions.add(int(row["definition_id"]))

    for new_release_id, previous_release_id in restore_by_new_release.items():
        bind.execute(
            sa.text(
                """
                UPDATE agent_deployments
                SET active_release_id = :previous_release_id,
                    updated_at = :now
                WHERE active_release_id = :new_release_id
                """
            ),
            {
                "previous_release_id": previous_release_id,
                "new_release_id": new_release_id,
                "now": now,
            },
        )
        bind.execute(
            sa.text(
                """
                UPDATE agent_deployments
                SET canary_release_id = :previous_release_id,
                    updated_at = :now
                WHERE canary_release_id = :new_release_id
                """
            ),
            {
                "previous_release_id": previous_release_id,
                "new_release_id": new_release_id,
                "now": now,
            },
        )

    for definition_id in definitions:
        previous_manifest = bind.execute(
            sa.text(
                """
                SELECT manifest_json
                FROM agent_releases
                WHERE definition_id = :definition_id
                  AND id NOT IN (
                      SELECT id FROM agent_releases
                  )
                """
            ),
            {"definition_id": definition_id},
        ).scalar_one_or_none()
        del previous_manifest
        previous_release_id = next(
            (
                old_id
                for new_id, old_id in restore_by_new_release.items()
                if int(
                    bind.execute(
                        sa.text(
                            "SELECT definition_id FROM agent_releases WHERE id = :id"
                        ),
                        {"id": new_id},
                    ).scalar_one()
                )
                == definition_id
            ),
            None,
        )
        if previous_release_id is not None:
            manifest = bind.execute(
                sa.text(
                    "SELECT manifest_json FROM agent_releases WHERE id = :release_id"
                ),
                {"release_id": previous_release_id},
            ).scalar_one()
            bind.execute(
                sa.text(
                    """
                    UPDATE agent_definitions
                    SET draft_manifest_json = :manifest,
                        updated_at = :now
                    WHERE id = :definition_id
                    """
                ),
                {
                    "manifest": manifest,
                    "now": now,
                    "definition_id": definition_id,
                },
            )

    if restore_by_new_release:
        placeholders = ", ".join(
            f":release_id_{index}"
            for index, _ in enumerate(restore_by_new_release)
        )
        bind.execute(
            sa.text(
                f"DELETE FROM agent_releases WHERE id IN ({placeholders})"
            ),
            {
                f"release_id_{index}": release_id
                for index, release_id in enumerate(restore_by_new_release)
            },
        )

    resource_id = bind.execute(
        sa.text(
            "SELECT id FROM ai_config_resources "
            "WHERE resource_key = :resource_key AND config_type = 'playbook'"
        ),
        {"resource_key": _PLAYBOOK_KEY},
    ).scalar_one_or_none()
    if resource_id is not None:
        version_one = bind.execute(
            sa.text(
                "SELECT snapshot_json, summary FROM ai_config_versions "
                "WHERE resource_id = :resource_id AND version = 1"
            ),
            {"resource_id": int(resource_id)},
        ).mappings().one_or_none()
        if version_one is not None:
            bind.execute(
                sa.text(
                    """
                    UPDATE ai_config_resources
                    SET draft_summary = :summary,
                        draft_content_json = :content,
                        published_summary = :summary,
                        published_content_json = :content,
                        published_version = 1,
                        published_at = :now,
                        updated_at = :now
                    WHERE id = :resource_id
                    """
                ),
                {
                    "summary": version_one["summary"],
                    "content": version_one["snapshot_json"],
                    "now": now,
                    "resource_id": int(resource_id),
                },
            )
        bind.execute(
            sa.text(
                "DELETE FROM ai_config_versions "
                "WHERE resource_id = :resource_id AND version = 2"
            ),
            {"resource_id": int(resource_id)},
        )
