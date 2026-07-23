from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CANONICAL = WORKFLOW_DIR / "canonical-acceptance.yml"
CANDIDATE = WORKFLOW_DIR / "controlled-candidate-convergence.yml"
IMAGE_ASSURANCE = ROOT / "scripts" / "release" / "run_controlled_image_assurance.sh"


def _assert_checkout_credentials_are_not_persisted(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    checkout_lines = [
        index for index, line in enumerate(lines) if "uses: actions/checkout@" in line
    ]
    assert checkout_lines
    for index in checkout_lines:
        checkout_window = "\n".join(lines[index : index + 10])
        assert "persist-credentials: false" in checkout_window


def test_github_actions_has_one_acceptance_authority_and_one_isolated_publisher():
    workflow_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in WORKFLOW_DIR.iterdir()
        if path.is_file()
    )
    assert workflow_files == [
        ".github/workflows/canonical-acceptance.yml",
        ".github/workflows/controlled-candidate-convergence.yml",
    ]

    canonical = CANONICAL.read_text(encoding="utf-8")
    candidate = CANDIDATE.read_text(encoding="utf-8")
    assert "name: Canonical Acceptance" in canonical
    assert "pull_request:" in canonical
    assert "push:" in canonical
    assert "required-gate:" in canonical

    assert "workflow_run:" in candidate
    assert "- Canonical Acceptance" in candidate
    assert "github.event.workflow_run.conclusion == 'success'" in candidate
    assert "github.event.workflow_run.event == 'push'" in candidate
    assert "github.event.workflow_run.head_branch == 'main'" in candidate
    for forbidden in (
        "pull_request:",
        "workflow_dispatch:",
        "issue_comment:",
        "repository_dispatch:",
    ):
        assert forbidden not in candidate

    _assert_checkout_credentials_are_not_persisted(CANONICAL)
    _assert_checkout_credentials_are_not_persisted(CANDIDATE)


def test_image_smoke_enforces_the_expected_migration_head_readiness_contract():
    workflow = CANONICAL.read_text(encoding="utf-8")
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
