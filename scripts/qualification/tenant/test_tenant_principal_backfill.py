from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import tenant_principal_backfill as backfill
import tenant_principal_preflight as preflight


CORE_TABLES = backfill.CORE_ORDER


SIGNING_KEY = b"tenant-backfill-test-signing-key-32bytes!!"
SIGNING_KEY_ID = "test-key-2026"


def _approved_apply_kwargs(mapping: Path) -> dict:
    manifest = preflight._load_manifest(mapping)
    return {
        "expected_mapping_digest": backfill._manifest_digest(manifest),
        "receipt_signing_key": SIGNING_KEY,
        "receipt_signing_key_id": SIGNING_KEY_ID,
    }


def _manifest_payload(*, display_name: str = "Tenant A") -> dict:
    return {
        "schema_version": "nexus_tenant_backfill_mapping_v1",
        "tenants": [{"tenant_key": "tenant-a", "display_name": display_name}],
        "market_codes": {"ME": "tenant-a"},
        "team_ids": {},
        "user_ids": {},
        "channel_account_ids": {},
        "ticket_ids": {},
        "customer_ids": {},
    }


def _write_manifest(tmp_path: Path, payload: dict, name: str = "mapping.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _create_schema(db_path: Path, *, revision: str = backfill.SOURCE_SCHEMA_REVISION, legacy_tenant: str | None = None) -> str:
    url = f"sqlite:///{db_path}"
    engine = sa.create_engine(url, future=True)
    metadata = sa.MetaData()
    sa.Table("alembic_version", metadata, sa.Column("version_num", sa.String(40), primary_key=True))
    tenants = sa.Table(
        "tenants",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_key", sa.String(80), nullable=False, unique=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    def tenant_columns() -> list[sa.Column]:
        return [
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(tenants.c.id), nullable=True),
            sa.Column("tenant_assignment_source", sa.String(40), nullable=True),
            sa.Column("tenant_assignment_version", sa.String(80), nullable=True),
        ]

    markets = sa.Table(
        "markets",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        *tenant_columns(),
    )
    teams = sa.Table(
        "teams",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey(markets.c.id)),
        *tenant_columns(),
    )
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey(teams.c.id)),
        *tenant_columns(),
    )
    channels = sa.Table(
        "channel_accounts",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey(markets.c.id)),
        *tenant_columns(),
    )
    customers = sa.Table(
        "customers",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        *tenant_columns(),
    )
    sa.Table(
        "tickets",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey(customers.c.id)),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey(markets.c.id)),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey(teams.c.id)),
        sa.Column("channel_account_id", sa.Integer(), sa.ForeignKey(channels.c.id)),
        sa.Column("assignee_id", sa.Integer(), sa.ForeignKey(users.c.id)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey(users.c.id)),
        *tenant_columns(),
    )
    if legacy_tenant is not None:
        sa.Table(
            "provider_routing_rules",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(80), nullable=True),
        )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES (:revision)"), {"revision": revision})
        connection.execute(sa.text("INSERT INTO markets (id, code) VALUES (1, 'ME')"))
        connection.execute(sa.text("INSERT INTO teams (id, market_id) VALUES (1, 1)"))
        connection.execute(sa.text("INSERT INTO users (id, team_id) VALUES (1, 1)"))
        connection.execute(sa.text("INSERT INTO channel_accounts (id, market_id) VALUES (1, 1)"))
        connection.execute(sa.text("INSERT INTO customers (id) VALUES (1)"))
        connection.execute(
            sa.text(
                "INSERT INTO tickets "
                "(id, customer_id, market_id, team_id, channel_account_id, assignee_id, created_by) "
                "VALUES (1, 1, 1, 1, 1, 1, 1)"
            )
        )
        if legacy_tenant is not None:
            connection.execute(
                sa.text("INSERT INTO provider_routing_rules (id, tenant_id) VALUES (1, :tenant)"),
                {"tenant": legacy_tenant},
            )
    engine.dispose()
    return url


def _receipt(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _core_state(url: str) -> dict[str, tuple[object, object, object]]:
    engine = sa.create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            return {
                table: tuple(
                    connection.execute(
                        sa.text(
                            f"SELECT tenant_id, tenant_assignment_source, tenant_assignment_version "
                            f"FROM {table} WHERE id=1"
                        )
                    ).one()
                )
                for table in CORE_TABLES
            }
    finally:
        engine.dispose()


def test_dry_run_plans_without_mutation(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "dry.db")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    output = tmp_path / "dry-receipt.json"

    assert backfill.run_backfill(url, mapping, output) == 0
    receipt = _receipt(output)
    assert receipt["mode"] == "dry_run"
    assert receipt["status"] == "pass"
    assert receipt["production_mutation_performed"] is False
    assert all(count == 1 for count in receipt["planned_counts"].values())
    assert all(value == (None, None, None) for value in _core_state(url).values())


def test_apply_assigns_all_core_tables_and_full_digest(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "apply.db")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    output = tmp_path / "apply-receipt.json"

    assert backfill.run_backfill(url, mapping, output, apply=True, batch_size=2, **_approved_apply_kwargs(mapping)) == 0
    receipt = _receipt(output)
    digest = receipt["mapping_digest"]
    assert len(digest) == 71 and digest.startswith("sha256:")
    assert receipt["status"] == "pass"
    assert receipt["receipt_signing_key_id"] == SIGNING_KEY_ID
    assert backfill.verify_receipt_signature(receipt, SIGNING_KEY) is True
    assert receipt["production_mutation_performed"] is True
    assert all(count == 1 for count in receipt["applied_counts"].values())
    for tenant_id, source, version in _core_state(url).values():
        assert tenant_id == 1
        assert source == backfill.ASSIGNMENT_SOURCE
        assert version == digest


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "idempotent.db")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert backfill.run_backfill(url, mapping, first, apply=True, **_approved_apply_kwargs(mapping)) == 0
    assert backfill.run_backfill(url, mapping, second, apply=True, **_approved_apply_kwargs(mapping)) == 0
    receipt = _receipt(second)
    assert receipt["status"] == "pass"
    assert receipt["production_mutation_performed"] is False
    assert all(count == 1 for count in receipt["already_applied_counts"].values())
    assert all(count == 0 for count in receipt["applied_counts"].values())


def test_partial_batch_can_resume(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "partial.db")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    partial_path = tmp_path / "partial.json"
    final_path = tmp_path / "final.json"

    assert backfill.run_backfill(url, mapping, partial_path, apply=True, batch_size=1, max_batches=2, **_approved_apply_kwargs(mapping)) == 0
    partial = _receipt(partial_path)
    assert partial["status"] == "partial"
    assert sum(partial["applied_counts"].values()) == 2
    assert sum(partial["remaining_counts"].values()) == 4

    assert backfill.run_backfill(url, mapping, final_path, apply=True, batch_size=2, **_approved_apply_kwargs(mapping)) == 0
    final = _receipt(final_path)
    assert final["status"] == "pass"
    assert sum(final["already_applied_counts"].values()) == 2
    assert sum(final["applied_counts"].values()) == 4


def test_existing_assignment_conflict_fails_without_mutation(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "existing-conflict.db")
    engine = sa.create_engine(url, future=True)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO tenants (id, tenant_key, display_name, is_active) "
                "VALUES (9, 'tenant-a', 'Tenant A', 1)"
            )
        )
        connection.execute(
            sa.text(
                "UPDATE markets SET tenant_id=9, tenant_assignment_source='manual', "
                "tenant_assignment_version='sha256:wrong' WHERE id=1"
            )
        )
    engine.dispose()
    mapping = _write_manifest(tmp_path, _manifest_payload())
    output = tmp_path / "conflict.json"

    assert backfill.run_backfill(url, mapping, output, apply=True, **_approved_apply_kwargs(mapping)) == 1
    receipt = _receipt(output)
    assert receipt["issues"]["counts"]["tenant.backfill_existing_assignment_conflict"] == 1
    assert receipt["production_mutation_performed"] is False
    assert _core_state(url)["markets"] == (9, "manual", "sha256:wrong")
    assert _core_state(url)["teams"] == (None, None, None)


def test_relationship_conflict_blocks_backfill(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "relation-conflict.db")
    payload = _manifest_payload()
    payload["tenants"].append({"tenant_key": "tenant-b", "display_name": "Tenant B"})
    payload["team_ids"] = {"1": "tenant-b"}
    mapping = _write_manifest(tmp_path, payload)
    output = tmp_path / "relation-conflict.json"

    assert backfill.run_backfill(url, mapping, output, apply=True, **_approved_apply_kwargs(mapping)) == 1
    receipt = _receipt(output)
    assert receipt["issues"]["counts"]["tenant.explicit_relation_conflict"] == 1
    assert receipt["production_mutation_performed"] is False
    assert all(value == (None, None, None) for value in _core_state(url).values())


def test_non_core_default_blocks_before_core_mutation(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "legacy-default.db", legacy_tenant="default")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    output = tmp_path / "legacy-default.json"

    assert backfill.run_backfill(url, mapping, output, apply=True, **_approved_apply_kwargs(mapping)) == 1
    receipt = _receipt(output)
    assert receipt["issues"]["counts"]["tenant.existing_default_forbidden"] == 1
    assert receipt["production_mutation_performed"] is False
    assert all(value == (None, None, None) for value in _core_state(url).values())


def test_manifest_digest_is_stable_after_normalization(tmp_path: Path) -> None:
    first = _manifest_payload(display_name=" Tenant   A ")
    first["tenants"][0]["tenant_key"] = "Tenant-A"
    second = _manifest_payload(display_name="Tenant A")
    first_path = _write_manifest(tmp_path, first, "first-map.json")
    second_path = _write_manifest(tmp_path, second, "second-map.json")

    one = preflight._load_manifest(first_path)
    two = preflight._load_manifest(second_path)
    assert backfill._manifest_digest(one) == backfill._manifest_digest(two)


def test_inactive_existing_principal_blocks(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "inactive-principal.db")
    engine = sa.create_engine(url, future=True)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO tenants (id, tenant_key, display_name, is_active) "
                "VALUES (1, 'tenant-a', 'Tenant A', 0)"
            )
        )
    engine.dispose()
    mapping = _write_manifest(tmp_path, _manifest_payload())
    output = tmp_path / "inactive-principal.json"

    assert backfill.run_backfill(
        url, mapping, output, apply=True, **_approved_apply_kwargs(mapping)
    ) == 1
    receipt = _receipt(output)
    assert receipt["issues"]["counts"]["tenant.principal_inactive"] == 1
    assert receipt["production_mutation_performed"] is False


def test_existing_principal_display_conflict_blocks(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "display-conflict.db")
    engine = sa.create_engine(url, future=True)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO tenants (id, tenant_key, display_name, is_active) "
                "VALUES (1, 'tenant-a', 'Wrong Display', 1)"
            )
        )
    engine.dispose()
    mapping = _write_manifest(tmp_path, _manifest_payload())
    output = tmp_path / "display-conflict.json"

    assert backfill.run_backfill(url, mapping, output, apply=True, **_approved_apply_kwargs(mapping)) == 1
    receipt = _receipt(output)
    assert receipt["issues"]["counts"]["tenant.principal_display_conflict"] == 1
    assert receipt["production_mutation_performed"] is False


@pytest.mark.parametrize(
    ("batch_size", "max_batches", "message"),
    [(0, None, "batch_size"), (backfill.MAX_BATCH_SIZE + 1, None, "batch_size"), (1, 0, "max_batches")],
)
def test_invalid_execution_limits_fail_before_database_write(
    tmp_path: Path,
    batch_size: int,
    max_batches: int | None,
    message: str,
) -> None:
    url = _create_schema(tmp_path / f"invalid-{batch_size}-{max_batches}.db")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    with pytest.raises(backfill.TenantBackfillError, match=message):
        backfill.run_backfill(
            url,
            mapping,
            tmp_path / "never.json",
            apply=True,
            **_approved_apply_kwargs(mapping),
            batch_size=batch_size,
            max_batches=max_batches,
        )
    engine = sa.create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT count(*) FROM tenants")).scalar_one() == 0
    finally:
        engine.dispose()


def test_schema_revision_mismatch_rejects(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "wrong-revision.db", revision="20260711_0058")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    with pytest.raises(backfill.TenantBackfillError, match="schema_revision_mismatch"):
        backfill.run_backfill(url, mapping, tmp_path / "wrong-revision.json", apply=True, **_approved_apply_kwargs(mapping))


def test_apply_requires_approved_digest_and_signing_material(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "approval-required.db")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    output = tmp_path / "approval-required.json"
    with pytest.raises(backfill.TenantBackfillError, match="expected_digest_required"):
        backfill.run_backfill(url, mapping, output, apply=True)
    with pytest.raises(backfill.TenantBackfillError, match="mapping_digest_mismatch"):
        backfill.run_backfill(
            url,
            mapping,
            output,
            apply=True,
            expected_mapping_digest="sha256:" + ("0" * 64),
            receipt_signing_key=SIGNING_KEY,
            receipt_signing_key_id=SIGNING_KEY_ID,
        )
    approved = _approved_apply_kwargs(mapping)
    approved["receipt_signing_key"] = b"short"
    with pytest.raises(backfill.TenantBackfillError, match="signing_key_invalid"):
        backfill.run_backfill(url, mapping, output, apply=True, **approved)


def test_receipt_signature_detects_tampering(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "signature.db")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    output = tmp_path / "signature.json"
    assert backfill.run_backfill(
        url, mapping, output, apply=True, **_approved_apply_kwargs(mapping)
    ) == 0
    receipt = _receipt(output)
    assert backfill.verify_receipt_signature(receipt, SIGNING_KEY) is True
    receipt["applied_counts"]["tickets"] = 999
    assert backfill.verify_receipt_signature(receipt, SIGNING_KEY) is False


def test_receipt_samples_are_hashed_and_bounded(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "hashed.db", legacy_tenant="default")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    output = tmp_path / "hashed-receipt.json"
    assert backfill.run_backfill(url, mapping, output, apply=False) == 1
    text_value = output.read_text(encoding="utf-8")
    receipt = json.loads(text_value)
    samples = receipt["issues"]["samples"]["tenant.existing_default_forbidden"]
    assert samples and all(sample.startswith("sha256:") and len(sample) == 71 for sample in samples)
    assert "provider_routing_rules:1" not in text_value
    assert len(text_value.encode("utf-8")) <= backfill.MAX_RECEIPT_BYTES


def test_concurrent_drift_rolls_back_tenant_and_prior_updates(tmp_path: Path, monkeypatch) -> None:
    url = _create_schema(tmp_path / "concurrent-drift.db")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    engine = sa.create_engine(url, future=True)

    @sa.event.listens_for(engine, "before_cursor_execute", retval=True)
    def force_team_rowcount_mismatch(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE teams SET tenant_id="):
            return statement + " AND 1=0", parameters
        return statement, parameters

    monkeypatch.setattr(backfill, "create_engine", lambda *args, **kwargs: engine)
    with pytest.raises(backfill.TenantBackfillError, match="concurrent_drift"):
        backfill.run_backfill(
            url,
            mapping,
            tmp_path / "concurrent-drift.json",
            apply=True,
            **_approved_apply_kwargs(mapping),
        )

    verify_engine = sa.create_engine(url, future=True)
    try:
        with verify_engine.connect() as connection:
            assert connection.execute(sa.text("SELECT count(*) FROM tenants")).scalar_one() == 0
        assert all(value == (None, None, None) for value in _core_state(url).values())
    finally:
        verify_engine.dispose()


def test_postgresql_apply_contract_is_serializable_and_fail_fast_locked() -> None:
    source = Path(backfill.__file__).read_text(encoding="utf-8")
    assert "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE" in source
    assert "pg_try_advisory_xact_lock(7140059)" in source
    assert "tenant_backfill_lock_unavailable" in source


def test_receipt_prepare_failure_rolls_back_apply(tmp_path: Path, monkeypatch) -> None:
    url = _create_schema(tmp_path / "receipt-prepare-failure.db")
    mapping = _write_manifest(tmp_path, _manifest_payload())

    def fail_prepare(*args, **kwargs):
        raise OSError("synthetic receipt prepare failure")

    monkeypatch.setattr(backfill, "_prepare_receipt", fail_prepare)
    with pytest.raises(OSError, match="receipt prepare failure"):
        backfill.run_backfill(
            url,
            mapping,
            tmp_path / "receipt.json",
            apply=True,
            **_approved_apply_kwargs(mapping),
        )

    engine = sa.create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT count(*) FROM tenants")).scalar_one() == 0
        assert all(value == (None, None, None) for value in _core_state(url).values())
    finally:
        engine.dispose()


def test_receipt_publish_failure_preserves_signed_pending_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    url = _create_schema(tmp_path / "receipt-publish-failure.db")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    output = tmp_path / "receipt.json"

    def fail_replace(source, destination):
        raise OSError("synthetic publish failure")

    monkeypatch.setattr(backfill.os, "replace", fail_replace)
    with pytest.raises(backfill.TenantBackfillError, match="receipt_publish_failed"):
        backfill.run_backfill(
            url,
            mapping,
            output,
            apply=True,
            **_approved_apply_kwargs(mapping),
        )

    pending = output.with_name(output.name + ".pending")
    assert output.exists() is False
    assert pending.is_file()
    receipt = json.loads(pending.read_text(encoding="utf-8"))
    assert receipt["status"] == "pass"
    assert receipt["production_mutation_performed"] is True
    assert backfill.verify_receipt_signature(receipt, SIGNING_KEY) is True
    assert all(value[0] == 1 for value in _core_state(url).values())


def test_existing_receipt_or_pending_path_is_not_overwritten(tmp_path: Path) -> None:
    url = _create_schema(tmp_path / "receipt-path-exists.db")
    mapping = _write_manifest(tmp_path, _manifest_payload())
    output = tmp_path / "receipt.json"
    output.write_text("preserve-me", encoding="utf-8")

    with pytest.raises(backfill.TenantBackfillError, match="receipt_path_exists"):
        backfill.run_backfill(
            url,
            mapping,
            output,
            apply=True,
            **_approved_apply_kwargs(mapping),
        )
    assert output.read_text(encoding="utf-8") == "preserve-me"
    engine = sa.create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT count(*) FROM tenants")).scalar_one() == 0
        assert all(value == (None, None, None) for value in _core_state(url).values())
    finally:
        engine.dispose()

    output.unlink()
    pending = output.with_name(output.name + ".pending")
    pending.write_text("pending-evidence", encoding="utf-8")
    with pytest.raises(backfill.TenantBackfillError, match="receipt_path_exists"):
        backfill.run_backfill(
            url,
            mapping,
            output,
            apply=True,
            **_approved_apply_kwargs(mapping),
        )
    assert pending.read_text(encoding="utf-8") == "pending-evidence"


def test_postgresql_apply_locks_all_tenant_scope_tables_and_relationship_rows() -> None:
    source = Path(backfill.__file__).read_text(encoding="utf-8")
    assert "IN SHARE ROW EXCLUSIVE MODE NOWAIT" in source
    assert "lock_rows=apply" in source
