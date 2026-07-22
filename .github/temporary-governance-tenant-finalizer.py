from __future__ import annotations

import subprocess
from pathlib import Path

EXPECTED_BLOBS = {
    "backend/app/api/governance.py": "3d53fc227f4ceef0248e65ba22ef30280fd695cf",
    "backend/app/api/knowledge_items.py": "7fa69b869e6c583982923bbf4bd442a920bece37",
    "backend/app/services/governance_service.py": "9fde1e613ac5e1eef12e797358ae7c04d322d2d9",
    "backend/app/services/knowledge_service.py": "5bb7facf317c44756ee9ef7dc550d55968e274fc",
    "backend/app/services/knowledge_studio_service.py": "abb160734473713e141b8601a1dfff63285e09f5",
    "backend/tests/test_governance_review_regressions.py": "5b0c9dfc53b4b75a3ce3075a7db4d166d5b8c689",
}


def assert_blob(path: str, expected: str) -> None:
    observed = subprocess.check_output(["git", "hash-object", path], text=True).strip()
    if observed != expected:
        raise SystemExit(f"base blob drift: {path} observed={observed} expected={expected}")


def replace_exact(text: str, old: str, new: str, *, path: str, count: int = 1) -> str:
    observed = text.count(old)
    if observed != count:
        raise SystemExit(
            f"replacement authority mismatch: {path} count={observed} expected={count} pattern={old!r}"
        )
    return text.replace(old, new)


def replace_between(text: str, start: str, end: str, replacement: str, *, path: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit(f"start marker missing: {path}: {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"end marker missing: {path}: {end!r}")
    return text[:start_index] + replacement + text[end_index:]


def patch_knowledge_service() -> None:
    path = Path("backend/app/services/knowledge_service.py")
    text = path.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        "from ..models_control_plane import KnowledgeItem, KnowledgeItemVersion\n",
        "from ..models import Tenant\nfrom ..models_control_plane import KnowledgeItem, KnowledgeItemVersion\n",
        path=str(path),
    )
    text = replace_exact(
        text,
        "from .knowledge_retrieval_service import index_published_item\n",
        "from .knowledge_retrieval_service import index_published_item\nfrom .tenant_authority import resolve_actor_tenant_id\n",
        path=str(path),
    )
    helper = '''def actor_knowledge_tenant_key(db: Session, actor) -> str:\n    tenant_id = resolve_actor_tenant_id(db, actor)\n    if tenant_id is None:\n        return "default"\n    tenant = db.get(Tenant, tenant_id)\n    if tenant is None or not tenant.is_active:\n        raise HTTPException(status_code=403, detail="authenticated_tenant_unavailable")\n    return tenant.tenant_key\n\n\n'''
    text = replace_exact(text, "def _normalize_key(value: str) -> str:\n", helper + "def _normalize_key(value: str) -> str:\n", path=str(path))
    text = replace_exact(
        text,
        '''def list_items(\n    db: Session,\n    *,\n    status: Optional[str] = None,\n''',
        '''def list_items(\n    db: Session,\n    *,\n    tenant_id: Optional[str] = None,\n    status: Optional[str] = None,\n''',
        path=str(path),
    )
    text = replace_exact(
        text,
        "    query = db.query(KnowledgeItem)\n    if status:\n",
        "    query = db.query(KnowledgeItem)\n    if tenant_id is not None:\n        query = query.filter(KnowledgeItem.tenant_id == tenant_id)\n    if status:\n",
        path=str(path),
        count=2,
    )
    text = replace_exact(
        text,
        '''def get_item_or_404(db: Session, item_id: int) -> KnowledgeItem:\n    row = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()\n''',
        '''def get_item_or_404(\n    db: Session, item_id: int, *, tenant_id: str | None = None\n) -> KnowledgeItem:\n    query = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id)\n    if tenant_id is not None:\n        query = query.filter(KnowledgeItem.tenant_id == tenant_id)\n    row = query.first()\n''',
        path=str(path),
    )
    text = replace_exact(
        text,
        '''def create_item(db: Session, payload, actor) -> KnowledgeItem:\n    key = _normalize_key(payload.item_key)\n''',
        '''def create_item(\n    db: Session, payload, actor, *, tenant_id: str | None = None\n) -> KnowledgeItem:\n    key = _normalize_key(payload.item_key)\n    tenant_key = _normalize_scope(tenant_id if tenant_id is not None else payload.tenant_id, "default")\n''',
        path=str(path),
    )
    text = replace_exact(
        text,
        '        tenant_id=_normalize_scope(payload.tenant_id, "default"),\n',
        "        tenant_id=tenant_key,\n",
        path=str(path),
    )
    text = replace_exact(
        text,
        '''    language: str | None = None,\n) -> KnowledgeItem:\n    filename = file.filename or "knowledge.txt"\n''',
        '''    language: str | None = None,\n    tenant_id: str = "default",\n) -> KnowledgeItem:\n    filename = file.filename or "knowledge.txt"\n    tenant_key = _normalize_scope(tenant_id, "default")\n''',
        path=str(path),
    )
    text = replace_exact(text, '        tenant_id="default",\n', "        tenant_id=tenant_key,\n", path=str(path))
    text = replace_exact(
        text,
        '''def update_item(db: Session, row: KnowledgeItem, payload, actor) -> KnowledgeItem:\n    values = payload.model_dump(exclude_unset=True)\n''',
        '''def update_item(\n    db: Session,\n    row: KnowledgeItem,\n    payload,\n    actor,\n    *,\n    tenant_id: str | None = None,\n) -> KnowledgeItem:\n    if tenant_id is not None and row.tenant_id != tenant_id:\n        raise HTTPException(status_code=404, detail="Knowledge item not found")\n    values = payload.model_dump(exclude_unset=True)\n    values.pop("tenant_id", None)\n''',
        path=str(path),
    )
    text = replace_exact(
        text,
        '''def rollback_item(db: Session, row: KnowledgeItem, *, version: int, actor, notes: Optional[str] = None) -> KnowledgeItemVersion:\n''',
        '''def rollback_item(\n    db: Session,\n    row: KnowledgeItem,\n    *,\n    version: int,\n    actor,\n    notes: Optional[str] = None,\n    tenant_id: str | None = None,\n) -> KnowledgeItemVersion:\n    if tenant_id is not None and row.tenant_id != tenant_id:\n        raise HTTPException(status_code=404, detail="Knowledge item not found")\n''',
        path=str(path),
    )
    text = replace_exact(
        text,
        '    row.tenant_id = snapshot.get("tenant_id") or row.tenant_id or "default"\n',
        '    row.tenant_id = tenant_id if tenant_id is not None else (snapshot.get("tenant_id") or row.tenant_id or "default")\n',
        path=str(path),
    )
    text = replace_exact(
        text,
        '''def search_published(\n    db: Session,\n    *,\n    q: Optional[str] = None,\n''',
        '''def search_published(\n    db: Session,\n    *,\n    tenant_id: Optional[str] = None,\n    q: Optional[str] = None,\n''',
        path=str(path),
    )
    path.write_text(text, encoding="utf-8")


def patch_knowledge_api() -> None:
    path = Path("backend/app/api/knowledge_items.py")
    text = path.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        "    ensure_can_read_ai_configs(current_user, db)\n    rows, total = knowledge_service.list_items(\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    tenant_key = knowledge_service.actor_knowledge_tenant_key(db, current_user)\n    rows, total = knowledge_service.list_items(\n        db,\n        tenant_id=tenant_key,\n",
        path=str(path),
    )
    text = replace_exact(text, "        db,\n        status=status,\n", "        status=status,\n", path=str(path))
    text = replace_exact(
        text,
        "    ensure_can_manage_ai_configs(current_user, db)\n    with managed_session(db):\n        row = knowledge_service.create_item(db, payload, current_user)\n",
        "    ensure_can_manage_ai_configs(current_user, db)\n    tenant_key = knowledge_service.actor_knowledge_tenant_key(db, current_user)\n    with managed_session(db):\n        row = knowledge_service.create_item(\n            db, payload, current_user, tenant_id=tenant_key\n        )\n",
        path=str(path),
    )
    text = replace_exact(
        text,
        "    ensure_can_manage_ai_configs(current_user, db)\n    with managed_session(db):\n        row = knowledge_service.create_file_item_from_upload(\n",
        "    ensure_can_manage_ai_configs(current_user, db)\n    tenant_key = knowledge_service.actor_knowledge_tenant_key(db, current_user)\n    with managed_session(db):\n        row = knowledge_service.create_file_item_from_upload(\n",
        path=str(path),
    )
    text = replace_exact(text, "            language=language,\n        )\n", "            language=language,\n            tenant_id=tenant_key,\n        )\n", path=str(path), count=1)
    text = replace_exact(
        text,
        "    ensure_can_read_ai_configs(current_user, db)\n    rows, total = knowledge_service.search_published(\n        db,\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    tenant_key = knowledge_service.actor_knowledge_tenant_key(db, current_user)\n    rows, total = knowledge_service.search_published(\n        db,\n        tenant_id=tenant_key,\n",
        path=str(path),
    )
    text = replace_exact(
        text,
        "    ensure_can_read_ai_configs(current_user, db)\n    retrieval = retrieve_published_chunks(\n        db,\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    tenant_key = knowledge_service.actor_knowledge_tenant_key(db, current_user)\n    retrieval = retrieve_published_chunks(\n        db,\n        tenant_id=tenant_key,\n",
        path=str(path),
        count=2,
    )
    text = replace_exact(
        text,
        "    ensure_can_read_ai_configs(current_user, db)\n    return run_conflict_check(db, payload)\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    tenant_key = knowledge_service.actor_knowledge_tenant_key(db, current_user)\n    return run_conflict_check(db, payload, tenant_id=tenant_key)\n",
        path=str(path),
    )
    old_runtime = '''    ensure_can_read_ai_configs(current_user, db)\n    return KnowledgeRuntimeContextTestOut(\n        context=build_agent_context(\n            db,\n            tenant_key=payload.tenant_key,\n'''
    new_runtime = '''    ensure_can_read_ai_configs(current_user, db)\n    tenant_key = knowledge_service.actor_knowledge_tenant_key(db, current_user)\n    requested_tenant = str(payload.tenant_key or "").strip()\n    if requested_tenant and requested_tenant != tenant_key:\n        raise HTTPException(status_code=404, detail="knowledge_tenant_not_found")\n    return KnowledgeRuntimeContextTestOut(\n        context=build_agent_context(\n            db,\n            tenant_key=tenant_key,\n'''
    text = replace_exact(text, old_runtime, new_runtime, path=str(path))
    for function_marker in (
        "def get_knowledge_item(\n",
        "def update_knowledge_item(\n",
        "def upload_knowledge_item_document(\n",
        "def publish_knowledge_item(\n",
        "def rollback_knowledge_item(\n",
    ):
        start = text.find(function_marker)
        if start < 0:
            raise SystemExit(f"function missing: {function_marker}")
        next_marker = text.find("\n@router.", start + 1)
        if next_marker < 0:
            next_marker = len(text)
        block = text[start:next_marker]
        block = replace_exact(
            block,
            "    ensure_can_read_ai_configs(current_user, db)\n" if function_marker == "def get_knowledge_item(\n" else "    ensure_can_manage_ai_configs(current_user, db)\n",
            ("    ensure_can_read_ai_configs(current_user, db)\n" if function_marker == "def get_knowledge_item(\n" else "    ensure_can_manage_ai_configs(current_user, db)\n")
            + "    tenant_key = knowledge_service.actor_knowledge_tenant_key(db, current_user)\n",
            path=str(path),
        )
        block = replace_exact(
            block,
            "    row = knowledge_service.get_item_or_404(db, item_id)\n",
            "    row = knowledge_service.get_item_or_404(\n        db, item_id, tenant_id=tenant_key\n    )\n",
            path=str(path),
        )
        if function_marker == "def update_knowledge_item(\n":
            block = replace_exact(
                block,
                "        row = knowledge_service.update_item(db, row, payload, current_user)\n",
                "        row = knowledge_service.update_item(\n            db, row, payload, current_user, tenant_id=tenant_key\n        )\n",
                path=str(path),
            )
        if function_marker == "def rollback_knowledge_item(\n":
            block = replace_exact(
                block,
                "        version_row = knowledge_service.rollback_item(db, row, version=payload.version, actor=current_user, notes=payload.notes)\n",
                "        version_row = knowledge_service.rollback_item(\n            db,\n            row,\n            version=payload.version,\n            actor=current_user,\n            notes=payload.notes,\n            tenant_id=tenant_key,\n        )\n",
                path=str(path),
            )
        text = text[:start] + block + text[next_marker:]
    path.write_text(text, encoding="utf-8")


def patch_knowledge_studio() -> None:
    path = Path("backend/app/services/knowledge_studio_service.py")
    text = path.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        "from .permissions import CAP_AI_CONFIG_MANAGE, CAP_AI_CONFIG_READ, resolve_capabilities\n",
        "from .knowledge_service import actor_knowledge_tenant_key\nfrom .permissions import CAP_AI_CONFIG_MANAGE, CAP_AI_CONFIG_READ, resolve_capabilities\n",
        path=str(path),
    )
    text = replace_exact(
        text,
        "def run_conflict_check(db: Session, payload) -> dict[str, Any]:\n    query = db.query(KnowledgeItem)\n",
        "def run_conflict_check(\n    db: Session, payload, *, tenant_id: str | None = None\n) -> dict[str, Any]:\n    query = db.query(KnowledgeItem)\n    if tenant_id is not None:\n        query = query.filter(KnowledgeItem.tenant_id == tenant_id)\n",
        path=str(path),
    )
    text = replace_exact(
        text,
        "    now = utc_now()\n    capabilities = resolve_capabilities(current_user, db)\n",
        "    now = utc_now()\n    capabilities = resolve_capabilities(current_user, db)\n    tenant_key = actor_knowledge_tenant_key(db, current_user)\n",
        path=str(path),
    )
    text = replace_exact(
        text,
        "        db.query(KnowledgeItem)\n        .order_by(",
        "        db.query(KnowledgeItem)\n        .filter(KnowledgeItem.tenant_id == tenant_key)\n        .order_by(",
        path=str(path),
    )
    text = replace_exact(
        text,
        "    indexed_chunks = int(db.query(func.count(KnowledgeChunk.id)).scalar() or 0)\n    version_count = int(db.query(func.count(KnowledgeItemVersion.id)).scalar() or 0)\n",
        "    indexed_chunks = int(\n        db.query(func.count(KnowledgeChunk.id))\n        .filter(KnowledgeChunk.tenant_id == tenant_key)\n        .scalar()\n        or 0\n    )\n    version_count = int(\n        db.query(func.count(KnowledgeItemVersion.id))\n        .join(KnowledgeItem, KnowledgeItem.id == KnowledgeItemVersion.item_id)\n        .filter(KnowledgeItem.tenant_id == tenant_key)\n        .scalar()\n        or 0\n    )\n",
        path=str(path),
    )
    path.write_text(text, encoding="utf-8")


def patch_governance_service() -> None:
    path = Path("backend/app/services/governance_service.py")
    text = path.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        "    description: str | None = None,\n",
        "    description: str | None | object = _UNSET,\n",
        path=str(path),
    )
    text = replace_exact(
        text,
        "    if description is not None:\n        row.description = str(description).strip() or None\n",
        "    if description is not _UNSET:\n        row.description = (\n            str(description).strip() or None if description is not None else None\n        )\n",
        path=str(path),
    )
    path.write_text(text, encoding="utf-8")


def patch_governance_api() -> None:
    path = Path("backend/app/api/governance.py")
    text = path.read_text(encoding="utf-8")
    lock_helper = '''def _lock_governance_scope(db: Session, tenant_id: int | None) -> None:\n    if tenant_id is not None:\n        tenant = (\n            db.query(Tenant)\n            .filter(Tenant.id == tenant_id)\n            .with_for_update()\n            .one_or_none()\n        )\n        if tenant is None or not tenant.is_active:\n            raise HTTPException(status_code=403, detail="authenticated_tenant_unavailable")\n        return\n    (\n        apply_tenant_scope(db.query(User), User, None)\n        .filter(User.is_active.is_(True))\n        .order_by(User.id.asc())\n        .with_for_update()\n        .all()\n    )\n\n\n'''
    text = replace_exact(text, "def _active_governor_ids(db: Session, tenant_id: int | None) -> set[int]:\n", lock_helper + "def _active_governor_ids(db: Session, tenant_id: int | None) -> set[int]:\n", path=str(path))
    publish_block = '''@router.post("/role-templates/{template_id}/publish")\ndef publish_role_template(\n    template_id: int,\n    payload: PublishRequest,\n    db: Session = Depends(get_db),\n    current_user=Depends(get_current_user),\n):\n    ensure_can_manage_users(current_user, db)\n    tenant_id = actor_tenant_id(db, current_user)\n    with managed_session(db):\n        _lock_governance_scope(db, tenant_id)\n        row = _template_for_actor(\n            db, current_user, template_id, require_manageable=True\n        )\n        capabilities = governance_service.clean_capabilities(\n            list(row.draft_capabilities_json or [])\n        )\n        base_role = governance_service.validate_base_role(row.base_role)\n        assigned_users = _role_template_assigned_users(\n            db, tenant_id=tenant_id, template_id=row.id\n        )\n        losing_governors = {\n            user.id\n            for user in assigned_users\n            if user.is_active\n            and CAP_USER_MANAGE in resolve_capabilities(user, db)\n            and CAP_USER_MANAGE not in capabilities\n        }\n        if current_user.id in losing_governors:\n            raise HTTPException(\n                status_code=409, detail="cannot_remove_own_governance_access"\n            )\n        _ensure_governance_access_survives(\n            db, tenant_id=tenant_id, losing_user_ids=losing_governors\n        )\n        version = governance_service.publish_role_template(\n            db, row=row, actor=current_user, notes=payload.notes\n        )\n        now = utc_now()\n        for user in assigned_users:\n            user.role = base_role\n            _apply_user_capability_overrides(\n                db,\n                user_id=user.id,\n                role=user.role,\n                requested_capabilities=capabilities,\n            )\n            assignment = db.get(RoleTemplateAssignment, user.id)\n            if assignment is None:\n                raise RuntimeError("role_template_assignment_missing")\n            assignment.template_version = version.version\n            assignment.assigned_by = current_user.id\n            assignment.assigned_at = now\n            advance_user_identity_version(user)\n        db.flush()\n        log_admin_audit(\n            db,\n            actor_id=current_user.id,\n            action="role_template.publish",\n            target_type="role_template",\n            target_id=row.id,\n            old_value={"published_version": version.version - 1},\n            new_value={\n                "published_version": version.version,\n                "affected_users": len(assigned_users),\n                "sessions_revoked": bool(assigned_users),\n            },\n        )\n    return {\n        "template_id": row.id,\n        "version": version.version,\n        "published_at": version.published_at,\n        "affected_users": len(assigned_users),\n    }\n\n\n'''
    text = replace_between(
        text,
        '@router.post("/role-templates/{template_id}/publish")\n',
        '@router.post("/role-templates/{template_id}/apply/{user_id}")\n',
        publish_block,
        path=str(path),
    )
    apply_block = '''@router.post("/role-templates/{template_id}/apply/{user_id}")\ndef apply_role_template(\n    template_id: int,\n    user_id: int,\n    db: Session = Depends(get_db),\n    current_user=Depends(get_current_user),\n):\n    ensure_can_manage_users(current_user, db)\n    tenant_id = actor_tenant_id(db, current_user)\n    with managed_session(db):\n        _lock_governance_scope(db, tenant_id)\n        template = _template_for_actor(db, current_user, template_id)\n        if not template.is_active or template.published_version <= 0:\n            raise HTTPException(\n                status_code=409, detail="publish_role_template_before_assignment"\n            )\n        user = (\n            apply_tenant_scope(db.query(User), User, tenant_id)\n            .filter(User.id == user_id, User.is_active.is_(True))\n            .one_or_none()\n        )\n        if user is None:\n            raise HTTPException(status_code=404, detail="user_not_found")\n        base_role, capabilities = governance_service.role_template_version_values(\n            db, template_id=template.id, version=template.published_version\n        )\n        currently_governs = CAP_USER_MANAGE in resolve_capabilities(user, db)\n        will_govern = CAP_USER_MANAGE in capabilities\n        if user.id == current_user.id and not will_govern:\n            raise HTTPException(\n                status_code=409, detail="cannot_remove_own_governance_access"\n            )\n        if currently_governs and not will_govern:\n            _ensure_governance_access_survives(\n                db, tenant_id=tenant_id, losing_user_ids={user.id}\n            )\n        before = {\n            "role": user.role.value,\n            "capabilities": sorted(resolve_capabilities(user, db)),\n            "assignment": governance_service.role_assignment_payload(db, user),\n        }\n        user.role = base_role\n        _apply_user_capability_overrides(\n            db,\n            user_id=user.id,\n            role=user.role,\n            requested_capabilities=capabilities,\n        )\n        assignment = db.get(RoleTemplateAssignment, user.id)\n        if assignment is None:\n            assignment = RoleTemplateAssignment(user_id=user.id)\n            db.add(assignment)\n        assignment.template_id = template.id\n        assignment.template_version = template.published_version\n        assignment.assigned_by = current_user.id\n        assignment.assigned_at = utc_now()\n        advance_user_identity_version(user)\n        db.flush()\n        log_admin_audit(\n            db,\n            actor_id=current_user.id,\n            action="role_template.apply",\n            target_type="user",\n            target_id=user.id,\n            old_value=before,\n            new_value={\n                "role": user.role.value,\n                "capabilities": capabilities,\n                "template_id": template.id,\n                "template_version": template.published_version,\n                "sessions_revoked": True,\n            },\n        )\n    db.refresh(user)\n    return {\n        "user_id": user.id,\n        "role": user.role.value,\n        "capabilities": sorted(resolve_capabilities(user, db)),\n        "assignment": governance_service.role_assignment_payload(db, user),\n    }\n\n\n'''
    text = replace_between(
        text,
        '@router.post("/role-templates/{template_id}/apply/{user_id}")\n',
        '@router.get("/role-template-assignments")\n',
        apply_block,
        path=str(path),
    )
    text = replace_exact(
        text,
        "                        language=batch.language,\n                    )\n",
        "                        language=batch.language,\n                        tenant_id=tenant_key,\n                    )\n",
        path=str(path),
    )
    path.write_text(text, encoding="utf-8")


def patch_governance_tests() -> None:
    path = Path("backend/tests/test_governance_review_regressions.py")
    text = path.read_text(encoding="utf-8")
    text = replace_exact(text, "from __future__ import annotations\n\n", "from __future__ import annotations\n\nimport inspect\nfrom unittest.mock import MagicMock\n\n", path=str(path))
    text = replace_exact(
        text,
        "from app.api.governance import (\n",
        "from app.api.governance import (\n    _lock_governance_scope,\n    apply_role_template,\n    publish_role_template,\n",
        path=str(path),
    )
    additions = '''\n\ndef test_role_description_distinguishes_omitted_from_explicit_null(db_session):\n    actor = User(\n        username="description-actor",\n        display_name="Description Actor",\n        password_hash="test",\n        role=UserRole.admin,\n        is_active=True,\n    )\n    template = RoleTemplate(\n        role_key="description-semantics",\n        display_name="Description semantics",\n        description="old description",\n        base_role=UserRole.agent.value,\n        risk_level="standard",\n        draft_capabilities_json=["ticket.read"],\n    )\n    db_session.add_all([actor, template])\n    db_session.commit()\n\n    governance_service.update_role_template(\n        db_session, row=template, actor=actor, display_name="Renamed"\n    )\n    assert template.description == "old description"\n\n    governance_service.update_role_template(\n        db_session, row=template, actor=actor, description=None\n    )\n    assert template.description is None\n\n\ndef test_governance_access_mutations_lock_scope_before_survival_check():\n    db = MagicMock()\n    tenant = Tenant(tenant_key="governance-lock", display_name="Governance Lock")\n    tenant.id = 91\n    tenant.is_active = True\n    query = MagicMock()\n    db.query.return_value = query\n    query.filter.return_value = query\n    query.with_for_update.return_value = query\n    query.one_or_none.return_value = tenant\n\n    _lock_governance_scope(db, tenant.id)\n    query.with_for_update.assert_called_once_with()\n\n    for endpoint in (publish_role_template, apply_role_template):\n        source = inspect.getsource(endpoint)\n        assert source.index("with managed_session(db):") < source.index(\n            "_lock_governance_scope"\n        )\n        assert source.index("_lock_governance_scope") < source.index(\n            "_ensure_governance_access_survives"\n        )\n'''
    text += additions
    path.write_text(text, encoding="utf-8")


def create_knowledge_tenant_tests() -> None:
    path = Path("backend/tests/test_knowledge_tenant_authority.py")
    if path.exists():
        raise SystemExit(f"unexpected existing path: {path}")
    path.write_text(
        '''from __future__ import annotations\n\nimport inspect\n\nimport pytest\nfrom fastapi import HTTPException\nfrom sqlalchemy import create_engine\nfrom sqlalchemy.orm import sessionmaker\n\nfrom app.api import knowledge_items\nfrom app.db import Base\nfrom app.models_control_plane import KnowledgeItem\nfrom app.schemas_control_plane import KnowledgeConflictCheckRequest\nfrom app.services import knowledge_service\nfrom app.services.knowledge_studio_service import run_conflict_check\n\n\n@pytest.fixture()\ndef db_session(tmp_path):\n    engine = create_engine(\n        f"sqlite:///{tmp_path / 'knowledge_tenant_authority.db'}",\n        connect_args={"check_same_thread": False},\n        future=True,\n    )\n    session_factory = sessionmaker(\n        bind=engine,\n        autoflush=False,\n        autocommit=False,\n        future=True,\n        expire_on_commit=False,\n    )\n    Base.metadata.create_all(engine)\n    session = session_factory()\n    try:\n        yield session\n    finally:\n        session.close()\n        Base.metadata.drop_all(engine)\n        engine.dispose()\n\n\ndef _item(*, key: str, tenant: str, question: str, priority: int = 100) -> KnowledgeItem:\n    return KnowledgeItem(\n        item_key=key,\n        title=key,\n        tenant_id=tenant,\n        status="active",\n        source_type="text",\n        knowledge_kind="faq",\n        visibility="customer",\n        shareability="customer_visible",\n        fact_question=question,\n        fact_answer=f"answer for {tenant}",\n        fact_status="approved",\n        answer_mode="direct_answer",\n        audience_scope="customer",\n        published_version=1,\n        priority=priority,\n    )\n\n\ndef test_canonical_knowledge_reads_and_conflicts_are_tenant_scoped(db_session):\n    tenant_a_one = _item(key="tenant-a-one", tenant="tenant-a", question="same question")\n    tenant_a_two = _item(\n        key="tenant-a-two", tenant="tenant-a", question="same question", priority=101\n    )\n    tenant_b = _item(key="tenant-b", tenant="tenant-b", question="same question")\n    db_session.add_all([tenant_a_one, tenant_a_two, tenant_b])\n    db_session.commit()\n\n    rows, total = knowledge_service.list_items(\n        db_session, tenant_id="tenant-a", limit=20\n    )\n    assert total == 2\n    assert {row.item_key for row in rows} == {"tenant-a-one", "tenant-a-two"}\n\n    assert (\n        knowledge_service.get_item_or_404(\n            db_session, tenant_a_one.id, tenant_id="tenant-a"\n        ).id\n        == tenant_a_one.id\n    )\n    with pytest.raises(HTTPException) as exc_info:\n        knowledge_service.get_item_or_404(\n            db_session, tenant_b.id, tenant_id="tenant-a"\n        )\n    assert exc_info.value.status_code == 404\n\n    published, published_total = knowledge_service.search_published(\n        db_session, tenant_id="tenant-a", q="same", limit=20\n    )\n    assert published_total == 2\n    assert {row.item_key for row in published} == {"tenant-a-one", "tenant-a-two"}\n\n    conflicts = run_conflict_check(\n        db_session,\n        KnowledgeConflictCheckRequest(limit=20),\n        tenant_id="tenant-a",\n    )\n    assert conflicts["total"] == 1\n    assert set(conflicts["conflicts"][0]["item_ids"]) == {\n        tenant_a_one.id,\n        tenant_a_two.id,\n    }\n    assert tenant_b.id not in set(conflicts["conflicts"][0]["item_ids"])\n\n\ndef test_canonical_knowledge_api_resolves_and_passes_actor_tenant():\n    source = inspect.getsource(knowledge_items)\n    assert "actor_knowledge_tenant_key" in source\n    assert source.count("tenant_id=tenant_key") >= 9\n    assert "requested_tenant != tenant_key" in source\n''',
        encoding="utf-8",
    )


def main() -> None:
    for path, expected in EXPECTED_BLOBS.items():
        assert_blob(path, expected)
    patch_knowledge_service()
    patch_knowledge_api()
    patch_knowledge_studio()
    patch_governance_service()
    patch_governance_api()
    patch_governance_tests()
    create_knowledge_tenant_tests()
    changed = set(subprocess.check_output(["git", "diff", "--name-only"], text=True).splitlines())
    expected = {
        "backend/app/api/governance.py",
        "backend/app/api/knowledge_items.py",
        "backend/app/services/governance_service.py",
        "backend/app/services/knowledge_service.py",
        "backend/app/services/knowledge_studio_service.py",
        "backend/tests/test_governance_review_regressions.py",
        "backend/tests/test_knowledge_tenant_authority.py",
    }
    if changed != expected:
        raise SystemExit(f"unexpected changed paths: {sorted(changed)!r}")
    subprocess.run(["git", "diff", "--check"], check=True)
    print(f"remediated_files={len(changed)}")


if __name__ == "__main__":
    main()
