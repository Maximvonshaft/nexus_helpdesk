from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "canonical-acceptance.yml"
IMAGE_ASSURANCE = ROOT / "scripts" / "release" / "run_controlled_image_assurance.sh"


def test_canonical_workflow_is_single_and_checkout_credentials_are_not_persisted():
    workflow_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / ".github" / "workflows").iterdir()
        if path.is_file()
    )
    assert workflow_files == [".github/workflows/canonical-acceptance.yml"]

    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    checkout_lines = [
        index for index, line in enumerate(lines) if "uses: actions/checkout@" in line
    ]
    assert checkout_lines
    for index in checkout_lines:
        checkout_window = "\n".join(lines[index : index + 8])
        assert "persist-credentials: false" in checkout_window


def test_image_smoke_enforces_the_expected_migration_head_readiness_contract():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assurance = IMAGE_ASSURANCE.read_text(encoding="utf-8")

    assert "bash scripts/release/run_controlled_image_assurance.sh" in workflow
    for marker in (
        "discover_alembic_head",
        "EXPECTED_MIGRATION_HEAD",
        "migration-readiness-contract.json",
        '"nexus.migration-readiness-contract.v1"',
        ".payload.migration.required == true",
        ".payload.migration.expected == $expected",
        ".payload.migration.observed == $expected",
        ".payload.migration_revision == $expected",
    ):
        assert marker in assurance
