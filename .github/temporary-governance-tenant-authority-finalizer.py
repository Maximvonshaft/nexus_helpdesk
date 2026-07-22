from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

ORIGINAL_FINALIZER_BLOB = "422fae7c509e32ec60f3b5d5c3c2b743fdda03a0"

# Compatibility anchor for the already-registered workflow. The workflow replaces
# this text inside the inert string before invoking this wrapper.
_WORKFLOW_COMPATIBILITY_ANCHOR = """
    changed = set(subprocess.check_output(["git", "diff", "--name-only"], text=True).splitlines())
"""


def replace_once(text: str, old: str, new: str, *, authority: str) -> str:
    observed = text.count(old)
    if observed != 1:
        raise SystemExit(
            f"{authority} anchor mismatch: observed={observed} expected=1"
        )
    return text.replace(old, new, 1)


def main() -> None:
    original = subprocess.check_output(
        ["git", "cat-file", "blob", ORIGINAL_FINALIZER_BLOB],
        text=True,
    )

    changed_old = (
        '    changed = set(subprocess.check_output(["git", "diff", '
        '"--name-only"], text=True).splitlines())\n'
    )
    changed_new = (
        '    changed = set(subprocess.check_output(["git", "diff", '
        '"--name-only"], text=True).splitlines())\n'
        '    changed.update(\n'
        '        subprocess.check_output(\n'
        '            ["git", "ls-files", "--others", "--exclude-standard"], '
        'text=True\n'
        '        ).splitlines()\n'
        '    )\n'
    )
    patched = replace_once(
        original,
        changed_old,
        changed_new,
        authority="changed-path",
    )

    fixture_old = '''    tenant = Tenant(tenant_key="description-tenant", display_name="Description Tenant")
    actor = User(
        tenant=tenant,
        username="description-actor",
        display_name="Description Actor",
        password_hash="test",
        role=UserRole.admin,
        is_active=True,
    )
    template = RoleTemplate(
        tenant=tenant,
        role_key="description-semantics",
        display_name="Description semantics",
        description="old description",
        base_role=UserRole.agent.value,
        risk_level="standard",
        draft_capabilities_json=["ticket.read"],
    )
    db_session.add_all([tenant, actor, template])
'''
    fixture_new = '''    tenant = Tenant(tenant_key="description-tenant", display_name="Description Tenant")
    db_session.add(tenant)
    db_session.flush()
    actor = User(
        tenant_id=tenant.id,
        username="description-actor",
        display_name="Description Actor",
        password_hash="test",
        role=UserRole.admin,
        is_active=True,
    )
    template = RoleTemplate(
        tenant_id=tenant.id,
        role_key="description-semantics",
        display_name="Description semantics",
        description="old description",
        base_role=UserRole.agent.value,
        risk_level="standard",
        draft_capabilities_json=["ticket.read"],
    )
    db_session.add_all([actor, template])
'''
    patched = replace_once(
        patched,
        fixture_old,
        fixture_new,
        authority="role-description-fixture",
    )

    generated = Path("/tmp/authenticated-governance-tenant-finalizer.py")
    generated.write_text(patched, encoding="utf-8")
    runpy.run_path(str(generated), run_name="__main__")


if __name__ == "__main__":
    main()
