from __future__ import annotations

import subprocess
from pathlib import Path

EXPECTED_BLOBS = {
    "backend/app/api/governance.py": "142475dd17eec36988b942617ad85f47a4bf2700",
    "backend/app/api/knowledge_items.py": "7fa69b869e6c583982923bbf4bd442a920bece37",
    "backend/app/services/governance_service.py": "731f6dca956a8fba9125db6d4a8bdc743f2bf580",
    "backend/app/services/knowledge_service.py": "5bb7facf317c44756ee9ef7dc550d55968e274fc",
    "backend/app/services/knowledge_studio_service.py": "abb160734473713e141b8601a1dfff63285e09f5",
    "backend/tests/test_governance_review_regressions.py": "2d7a4f8a8e650903164d897eac29f083cd3d8381",
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
        """def list_items(\n    db: Session,\n    *,\n    status: Optional[str] = None,\n""",
        """def list_items(\n    db: Session,\n    *,\n    tenant_id: Optional[str] = None,\n    status: Optional[str] = None,\n""",
        path=str(path),
    )
    text = replace_exact(
        text,
        "    query = db.query(KnowledgeItem)\n    if status:\n",
        "    query = db.query(KnowledgeItem)\n    if tenant_id is not None:\n        query = query.filter(KnowledgeItem.tenant_id == tenant_id)\n    if status:\n",
        path=str(path),
    )
    text = replace_exact(
        text,
        """def get_item_or_404(db: Session, item_id: int) -> KnowledgeItem:\n    row = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id).first()\n""",
        """def get_item_or_404(\n    db: Session, item_id: int, *, tenant_id: str | None = None\n) -> KnowledgeItem:\n    query = db.query(KnowledgeItem).filter(KnowledgeItem.id == item_id)\n    if tenant_id is not None:\n        query = query.filter(KnowledgeItem.tenant_id == tenant_id)\n    row = query.first()\n""",
        path=str(path),
    )
    text = replace_exact(
        text,
        """def create_item(db: Session, payload, actor) -> KnowledgeItem:\n    key = _normalize_key(payload.item_key)\n""",
        """def create_item(\n    db: Session, payload, actor, *, tenant_id: str | None = None\n) -> KnowledgeItem:\n    key = _normalize_key(payload.item_key)\n    tenant_key = _normalize_scope(\n        tenant_id if tenant_id is not None else payload.tenant_id, "default"\n    )\n""",
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
        """    audience_scope: str | None = "customer",\n    language: str | None = None,\n) -> KnowledgeItem:\n    filename = file.filename or "knowledge.txt"\n""",
        """    audience_scope: str | None = "customer",\n    language: str | None = None,\n    tenant_id: str = "default",\n) -> KnowledgeItem:\n    filename = file.filename or "knowledge.txt"\n    tenant_key = _normalize_scope(tenant_id, "default")\n""",
        path=str(path),
    )
    text = replace_exact(
        text,
        '        tenant_id="default",\n',
        "        tenant_id=tenant_key,\n",
        path=str(path),
    )
    text = replace_exact(
        text,
        """def update_item(db: Session, row: KnowledgeItem, payload, actor) -> KnowledgeItem:\n    values = payload.model_dump(exclude_unset=True)\n""",
        """def update_item(\n    db: Session,\n    row: KnowledgeItem,\n    payload,\n    actor,\n    *,\n    tenant_id: str | None = None,\n) -> KnowledgeItem:\n    if tenant_id is not None and row.tenant_id != tenant_id:\n        raise HTTPException(status_code=404, detail="Knowledge item not found")\n    values = payload.model_dump(exclude_unset=True)\n    values.pop("tenant_id", None)\n""",
        path=str(path),
    )
    text = replace_exact(
        text,
        """def rollback_item(db: Session, row: KnowledgeItem, *, version: int, actor, notes: Optional[str] = None) -> KnowledgeItemVersion:\n""",
        """def rollback_item(\n    db: Session,\n    row: KnowledgeItem,\n    *,\n    version: int,\n    actor,\n    notes: Optional[str] = None,\n    tenant_id: str | None = None,\n) -> KnowledgeItemVersion:\n    if tenant_id is not None and row.tenant_id != tenant_id:\n        raise HTTPException(status_code=404, detail="Knowledge item not found")\n""",
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
        """def search_published(\n    db: Session,\n    *,\n    q: Optional[str] = None,\n""",
        """def search_published(\n    db: Session,\n    *,\n    tenant_id: Optional[str] = None,\n    q: Optional[str] = None,\n""",
        path=str(path),
    )
    text = replace_exact(
        text,
        """        or_(KnowledgeItem.valid_until.is_(None), KnowledgeItem.valid_until >= now),\n    )\n    if market_id is not None:\n""",
        """        or_(KnowledgeItem.valid_until.is_(None), KnowledgeItem.valid_until >= now),\n    )\n    if tenant_id is not None:\n        query = query.filter(KnowledgeItem.tenant_id == tenant_id)\n    if market_id is not None:\n""",
        path=str(path),
    )
    path.write_text(text, encoding="utf-8")


def patch_knowledge_api() -> None:
    path = Path("backend/app/api/knowledge_items.py")
    text = path.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        "from ..services.knowledge_retrieval_service import retrieve_published_chunks\n",
        "from ..services.knowledge_retrieval_service import retrieve_published_chunks\nfrom ..services.agent_release_service import authoritative_tenant_key\n",
        path=str(path),
    )
    helper = """def _actor_tenant_key(\n    db: Session, current_user, *, requested: str | None = None\n) -> str:\n    return authoritative_tenant_key(\n        db,\n        current_user,\n        requested=requested,\n        allow_platform_default=True,\n    )\n\n\n"""
    text = replace_exact(
        text,
        "def _item_out(row) -> KnowledgeItemOut:\n",
        helper + "def _item_out(row) -> KnowledgeItemOut:\n",
        path=str(path),
    )

    def block(start: str, end: str) -> tuple[int, int, str]:
        start_index = text.find(start)
        if start_index < 0:
            raise SystemExit(f"function marker missing: {start}")
        end_index = text.find(end, start_index)
        if end_index < 0:
            raise SystemExit(f"next marker missing: {end}")
        return start_index, end_index, text[start_index:end_index]

    def replace_block(start: str, end: str, old: str, new: str, *, count: int = 1) -> None:
        nonlocal text
        start_index, end_index, current = block(start, end)
        current = replace_exact(current, old, new, path=str(path), count=count)
        text = text[:start_index] + current + text[end_index:]

    replace_block(
        "def list_knowledge_items(\n",
        "@router.post(\"\", response_model=KnowledgeItemOut)\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    rows, total = knowledge_service.list_items(\n        db,\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    tenant_key = _actor_tenant_key(db, current_user)\n    rows, total = knowledge_service.list_items(\n        db,\n        tenant_id=tenant_key,\n",
    )
    replace_block(
        "def create_knowledge_item(\n",
        "@router.post(\"/upload\", response_model=KnowledgeItemOut)\n",
        "    ensure_can_manage_ai_configs(current_user, db)\n    with managed_session(db):\n        row = knowledge_service.create_item(db, payload, current_user)\n",
        "    ensure_can_manage_ai_configs(current_user, db)\n    tenant_key = _actor_tenant_key(db, current_user)\n    with managed_session(db):\n        row = knowledge_service.create_item(\n            db, payload, current_user, tenant_id=tenant_key\n        )\n",
    )
    replace_block(
        "def create_knowledge_item_from_upload(\n",
        "@router.post(\"/search-published\", response_model=KnowledgeSearchPublishedOut)\n",
        "    ensure_can_manage_ai_configs(current_user, db)\n    with managed_session(db):\n",
        "    ensure_can_manage_ai_configs(current_user, db)\n    tenant_key = _actor_tenant_key(db, current_user)\n    with managed_session(db):\n",
    )
    replace_block(
        "def create_knowledge_item_from_upload(\n",
        "@router.post(\"/search-published\", response_model=KnowledgeSearchPublishedOut)\n",
        "            language=language,\n        )\n",
        "            language=language,\n            tenant_id=tenant_key,\n        )\n",
    )
    replace_block(
        "def search_published_knowledge_items(\n",
        "@router.post(\"/retrieve-test\", response_model=KnowledgeRetrievalTestOut)\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    rows, total = knowledge_service.search_published(\n        db,\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    tenant_key = _actor_tenant_key(db, current_user)\n    rows, total = knowledge_service.search_published(\n        db,\n        tenant_id=tenant_key,\n",
    )
    for start, end in (
        ("def test_knowledge_retrieval(\n", "@router.post(\"/conflict-check\", response_model=KnowledgeConflictCheckOut)\n"),
        ("def run_knowledge_golden_test(\n", "@router.post(\"/runtime-context-test\", response_model=KnowledgeRuntimeContextTestOut)\n"),
    ):
        replace_block(
            start,
            end,
            "    ensure_can_read_ai_configs(current_user, db)\n    retrieval = retrieve_published_chunks(\n        db,\n",
            "    ensure_can_read_ai_configs(current_user, db)\n    tenant_key = _actor_tenant_key(db, current_user)\n    retrieval = retrieve_published_chunks(\n        db,\n        tenant_id=tenant_key,\n",
        )
    replace_block(
        "def check_knowledge_conflicts(\n",
        "@router.post(\"/golden-test\", response_model=KnowledgeGoldenTestOut)\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    return run_conflict_check(db, payload)\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    tenant_key = _actor_tenant_key(db, current_user)\n    return run_conflict_check(db, payload, tenant_id=tenant_key)\n",
    )
    replace_block(
        "def test_knowledge_runtime_context(\n",
        "@router.get(\"/{item_id}\", response_model=KnowledgeItemDetailOut)\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    return KnowledgeRuntimeContextTestOut(\n",
        "    ensure_can_read_ai_configs(current_user, db)\n    tenant_key = _actor_tenant_key(\n        db, current_user, requested=payload.tenant_key\n    )\n    return KnowledgeRuntimeContextTestOut(\n",
    )
    replace_block(
        "def test_knowledge_runtime_context(\n",
        "@router.get(\"/{item_id}\", response_model=KnowledgeItemDetailOut)\n",
        "            tenant_key=payload.tenant_key,\n",
        "            tenant_key=tenant_key,\n",
    )

    endpoint_boundaries = (
        ("def get_knowledge_item(\n", "@router.patch(\"/{item_id}\", response_model=KnowledgeItemOut)\n", False),
        ("def update_knowledge_item(\n", "@router.post(\"/{item_id}/upload\", response_model=KnowledgeItemOut)\n", True),
        ("def upload_knowledge_item_document(\n", "@router.post(\"/{item_id}/publish\", response_model=KnowledgeItemVersionOut)\n", True),
        ("def publish_knowledge_item(\n", "@router.post(\"/{item_id}/rollback\", response_model=KnowledgeItemVersionOut)\n", True),
    )
    for start, end, manage in endpoint_boundaries:
        permission = (
            "    ensure_can_manage_ai_configs(current_user, db)\n"
            if manage
            else "    ensure_can_read_ai_configs(current_user, db)\n"
        )
        replace_block(
            start,
            end,
            permission,
            permission + "    tenant_key = _actor_tenant_key(db, current_user)\n",
        )
        replace_block(
            start,
            end,
            "    row = knowledge_service.get_item_or_404(db, item_id)\n",
            "    row = knowledge_service.get_item_or_404(\n        db, item_id, tenant_id=tenant_key\n    )\n",
        )
    replace_block(
        "def update_knowledge_item(\n",
        "@router.post(\"/{item_id}/upload\", response_model=KnowledgeItemOut)\n",
        "        row = knowledge_service.update_item(db, row, payload, current_user)\n",
        "        row = knowledge_service.update_item(\n            db, row, payload, current_user, tenant_id=tenant_key\n        )\n",
    )

    start = "def rollback_knowledge_item(\n"
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit("rollback endpoint missing")
    current = text[start_index:]
    current = replace_exact(
        current,
        "    ensure_can_manage_ai_configs(current_user, db)\n",
        "    ensure_can_manage_ai_configs(current_user, db)\n    tenant_key = _actor_tenant_key(db, current_user)\n",
        path=str(path),
    )
    current = replace_exact(
        current,
        "    row = knowledge_service.get_item_or_404(db, item_id)\n",
        "    row = knowledge_service.get_item_or_404(\n        db, item_id, tenant_id=tenant_key\n    )\n",
        path=str(path),
    )
    current = replace_exact(
        current,
        "        version_row = knowledge_service.rollback_item(db, row, version=payload.version, actor=current_user, notes=payload.notes)\n",
        "        version_row = knowledge_service.rollback_item(\n            db,\n            row,\n            version=payload.version,\n            actor=current_user,\n            notes=payload.notes,\n            tenant_id=tenant_key,\n        )\n",
        path=str(path),
    )
    text = text[:start_index] + current
    path.write_text(text, encoding="utf-8")


def patch_knowledge_studio() -> None:
    path = Path("backend/app/services/knowledge_studio_service.py")
    text = path.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        "from .permissions import CAP_AI_CONFIG_MANAGE, CAP_AI_CONFIG_READ, resolve_capabilities\n",
        "from .agent_release_service import authoritative_tenant_key\nfrom .permissions import CAP_AI_CONFIG_MANAGE, CAP_AI_CONFIG_READ, resolve_capabilities\n",
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
        """    if not (capabilities & KNOWLEDGE_STUDIO_CAPABILITIES):\n        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="knowledge_studio_requires_ai_config_capability")\n\n    rows = (\n        db.query(KnowledgeItem)\n""",
        """    if not (capabilities & KNOWLEDGE_STUDIO_CAPABILITIES):\n        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="knowledge_studio_requires_ai_config_capability")\n    tenant_key = authoritative_tenant_key(\n        db, current_user, allow_platform_default=True\n    )\n\n    rows = (\n        db.query(KnowledgeItem)\n        .filter(KnowledgeItem.tenant_id == tenant_key)\n""",
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
    helper = """def _lock_governance_scope(db: Session, tenant_id: int | None) -> None:\n    if tenant_id is not None:\n        tenant = (\n            db.query(Tenant)\n            .filter(Tenant.id == tenant_id)\n            .with_for_update()\n            .one_or_none()\n        )\n        if tenant is None or not tenant.is_active:\n            raise HTTPException(status_code=403, detail="authenticated_tenant_unavailable")\n        return\n    (\n        apply_tenant_scope(db.query(User), User, None)\n        .filter(User.is_active.is_(True))\n        .order_by(User.id.asc())\n        .with_for_update()\n        .all()\n    )\n\n\n"""
    text = replace_exact(
        text,
        "def _active_governor_ids(db: Session, tenant_id: int | None) -> set[int]:\n",
        helper + "def _active_governor_ids(db: Session, tenant_id: int | None) -> set[int]:\n",
        path=str(path),
    )

    publish = """@router.post("/role-templates/{template_id}/publish")\ndef publish_role_template(\n    template_id: int,\n    payload: PublishRequest,\n    db: Session = Depends(get_db),\n    current_user=Depends(get_current_user),\n):\n    ensure_can_manage_users(current_user, db)\n    tenant_id = actor_tenant_id(db, current_user)\n    with managed_session(db):\n        _lock_governance_scope(db, tenant_id)\n        row = _template_for_actor(\n            db, current_user, template_id, require_manageable=True\n        )\n        capabilities = governance_service.clean_capabilities(\n            list(row.draft_capabilities_json or [])\n        )\n        base_role = governance_service.validate_base_role(row.base_role)\n        assigned_users = _role_template_assigned_users(\n            db, tenant_id=tenant_id, template_id=row.id\n        )\n        losing_governors = {\n            user.id\n            for user in assigned_users\n            if user.is_active\n            and CAP_USER_MANAGE in resolve_capabilities(user, db)\n            and CAP_USER_MANAGE not in capabilities\n        }\n        if current_user.id in losing_governors:\n            raise HTTPException(\n                status_code=409, detail="cannot_remove_own_governance_access"\n            )\n        _ensure_governance_access_survives(\n            db, tenant_id=tenant_id, losing_user_ids=losing_governors\n        )\n        version = governance_service.publish_role_template(\n            db, row=row, actor=current_user, notes=payload.notes\n        )\n        now = utc_now()\n        for user in assigned_users:\n            user.role = base_role\n            _apply_user_capability_overrides(\n                db,\n                user_id=user.id,\n                role=user.role,\n                requested_capabilities=capabilities,\n            )\n            assignment = db.get(RoleTemplateAssignment, user.id)\n            if assignment is None:\n                raise RuntimeError("role_template_assignment_missing")\n            assignment.template_version = version.version\n            assignment.assigned_by = current_user.id\n            assignment.assigned_at = now\n            advance_user_identity_version(user)\n        db.flush()\n        log_admin_audit(\n            db,\n            actor_id=current_user.id,\n            action="role_template.publish",\n            target_type="role_template",\n            target_id=row.id,\n            old_value={"published_version": version.version - 1},\n            new_value={\n                "published_version": version.version,\n                "affected_users": len(assigned_users),\n                "sessions_revoked": bool(assigned_users),\n            },\n        )\n    return {\n        "template_id": row.id,\n        "version": version.version,\n        "published_at": version.published_at,\n        "affected_users": len(assigned_users),\n    }\n\n\n"""
    text = replace_between(
        text,
        '@router.post("/role-templates/{template_id}/publish")\n',
        '@router.post("/role-templates/{template_id}/apply/{user_id}")\n',
        publish,
        path=str(path),
    )

    apply = """@router.post("/role-templates/{template_id}/apply/{user_id}")\ndef apply_role_template(\n    template_id: int,\n    user_id: int,\n    db: Session = Depends(get_db),\n    current_user=Depends(get_current_user),\n):\n    ensure_can_manage_users(current_user, db)\n    tenant_id = actor_tenant_id(db, current_user)\n    with managed_session(db):\n        _lock_governance_scope(db, tenant_id)\n        template = _template_for_actor(db, current_user, template_id)\n        if not template.is_active or template.published_version <= 0:\n            raise HTTPException(\n                status_code=409, detail="publish_role_template_before_assignment"\n            )\n        user = (\n            apply_tenant_scope(db.query(User), User, tenant_id)\n            .filter(User.id == user_id, User.is_active.is_(True))\n            .one_or_none()\n        )\n        if user is None:\n            raise HTTPException(status_code=404, detail="user_not_found")\n        base_role, capabilities = governance_service.role_template_version_values(\n            db, template_id=template.id, version=template.published_version\n        )\n        currently_governs = CAP_USER_MANAGE in resolve_capabilities(user, db)\n        will_govern = CAP_USER_MANAGE in capabilities\n        if user.id == current_user.id and not will_govern:\n            raise HTTPException(\n                status_code=409, detail="cannot_remove_own_governance_access"\n            )\n        if currently_governs and not will_govern:\n            _ensure_governance_access_survives(\n                db, tenant_id=tenant_id, losing_user_ids={user.id}\n            )\n        before = {\n            "role": user.role.value,\n            "capabilities": sorted(resolve_capabilities(user, db)),\n            "assignment": governance_service.role_assignment_payload(db, user),\n        }\n        user.role = base_role\n        _apply_user_capability_overrides(\n            db,\n            user_id=user.id,\n            role=user.role,\n            requested_capabilities=capabilities,\n        )\n        assignment = db.get(RoleTemplateAssignment, user.id)\n        if assignment is None:\n            assignment = RoleTemplateAssignment(user_id=user.id)\n            db.add(assignment)\n        assignment.template_id = template.id\n        assignment.template_version = template.published_version\n        assignment.assigned_by = current_user.id\n        assignment.assigned_at = utc_now()\n        advance_user_identity_version(user)\n        db.flush()\n        log_admin_audit(\n            db,\n            actor_id=current_user.id,\n            action="role_template.apply",\n            target_type="user",\n            target_id=user.id,\n            old_value=before,\n            new_value={\n                "role": user.role.value,\n                "capabilities": capabilities,\n                "template_id": template.id,\n                "template_version": template.published_version,\n                "sessions_revoked": True,\n            },\n        )\n    db.refresh(user)\n    return {\n        "user_id": user.id,\n        "role": user.role.value,\n        "capabilities": sorted(resolve_capabilities(user, db)),\n        "assignment": governance_service.role_assignment_payload(db, user),\n    }\n\n\n"""
    text = replace_between(
        text,
        '@router.post("/role-templates/{template_id}/apply/{user_id}")\n',
        '@router.get("/role-template-assignments")\n',
        apply,
        path=str(path),
    )
    text = replace_exact(
        text,
        "                        language=batch.language,\n                    )\n                    item.tenant_id = tenant_key\n",
        "                        language=batch.language,\n                        tenant_id=tenant_key,\n                    )\n",
        path=str(path),
    )
    path.write_text(text, encoding="utf-8")


def patch_governance_tests() -> None:
    path = Path("backend/tests/test_governance_review_regressions.py")
    text = path.read_text(encoding="utf-8")
    text = replace_exact(
        text,
        "from contextlib import nullcontext\nimport importlib.util\n",
        "from contextlib import nullcontext\nimport importlib.util\nimport inspect\nfrom unittest.mock import MagicMock\n",
        path=str(path),
    )
    additions = """\n\ndef test_role_description_distinguishes_omitted_from_explicit_null(db_session):\n    tenant = Tenant(tenant_key="description-tenant", display_name="Description Tenant")\n    actor = User(\n        tenant=tenant,\n        username="description-actor",\n        display_name="Description Actor",\n        password_hash="test",\n        role=UserRole.admin,\n        is_active=True,\n    )\n    template = RoleTemplate(\n        tenant=tenant,\n        role_key="description-semantics",\n        display_name="Description semantics",\n        description="old description",\n        base_role=UserRole.agent.value,\n        risk_level="standard",\n        draft_capabilities_json=["ticket.read"],\n    )\n    db_session.add_all([tenant, actor, template])\n    db_session.commit()\n\n    governance_service.update_role_template(\n        db_session, row=template, actor=actor, display_name="Renamed"\n    )\n    assert template.description == "old description"\n\n    governance_service.update_role_template(\n        db_session, row=template, actor=actor, description=None\n    )\n    assert template.description is None\n\n\ndef test_governance_access_mutations_lock_scope_inside_transaction():\n    db = MagicMock()\n    tenant = Tenant(tenant_key="governance-lock", display_name="Governance Lock")\n    tenant.id = 91\n    tenant.is_active = True\n    query = MagicMock()\n    db.query.return_value = query\n    query.filter.return_value = query\n    query.with_for_update.return_value = query\n    query.one_or_none.return_value = tenant\n\n    governance_api._lock_governance_scope(db, tenant.id)\n    query.with_for_update.assert_called_once_with()\n\n    for endpoint in (\n        governance_api.publish_role_template,\n        governance_api.apply_role_template,\n    ):\n        source = inspect.getsource(endpoint)\n        assert source.index("with managed_session(db):") < source.index(\n            "_lock_governance_scope"\n        )\n        assert source.index("_lock_governance_scope") < source.index(\n            "_ensure_governance_access_survives"\n        )\n"""
    text += additions
    path.write_text(text, encoding="utf-8")


def create_knowledge_tenant_tests() -> None:
    path = Path("backend/tests/test_knowledge_tenant_authority.py")
    if path.exists():
        raise SystemExit(f"unexpected existing path: {path}")
    path.write_text(
        '''from __future__ import annotations\n\nimport inspect\nfrom types import SimpleNamespace\n\nimport pytest\nfrom fastapi import HTTPException\nfrom sqlalchemy import create_engine\nfrom sqlalchemy.orm import sessionmaker\n\nfrom app.api import knowledge_items\nfrom app.db import Base\nfrom app.models_control_plane import KnowledgeItem\nfrom app.schemas_control_plane import KnowledgeConflictCheckRequest\nfrom app.services import knowledge_service\nfrom app.services.knowledge_studio_service import run_conflict_check\n\n\n@pytest.fixture()\ndef db_session(tmp_path):\n    engine = create_engine(\n        f"sqlite:///{tmp_path / 'knowledge_tenant_authority.db'}",\n        connect_args={"check_same_thread": False},\n        future=True,\n    )\n    session_factory = sessionmaker(\n        bind=engine,\n        autoflush=False,\n        autocommit=False,\n        future=True,\n        expire_on_commit=False,\n    )\n    Base.metadata.create_all(engine)\n    session = session_factory()\n    try:\n        yield session\n    finally:\n        session.close()\n        Base.metadata.drop_all(engine)\n        engine.dispose()\n\n\ndef _item(*, key: str, tenant: str, question: str, priority: int = 100) -> KnowledgeItem:\n    return KnowledgeItem(\n        item_key=key,\n        title=key,\n        tenant_id=tenant,\n        status="active",\n        source_type="text",\n        knowledge_kind="faq",\n        visibility="customer",\n        shareability="customer_visible",\n        fact_question=question,\n        fact_answer=f"answer for {tenant}",\n        fact_status="approved",\n        answer_mode="direct_answer",\n        audience_scope="customer",\n        published_version=1,\n        published_normalized_text=question,\n        priority=priority,\n    )\n\n\ndef test_canonical_knowledge_reads_search_and_conflicts_are_tenant_scoped(db_session):\n    tenant_a_one = _item(key="tenant-a-one", tenant="tenant-a", question="same question")\n    tenant_a_two = _item(\n        key="tenant-a-two", tenant="tenant-a", question="same question", priority=101\n    )\n    tenant_b = _item(key="tenant-b", tenant="tenant-b", question="same question")\n    db_session.add_all([tenant_a_one, tenant_a_two, tenant_b])\n    db_session.commit()\n\n    rows, total = knowledge_service.list_items(\n        db_session, tenant_id="tenant-a", limit=20\n    )\n    assert total == 2\n    assert {row.item_key for row in rows} == {"tenant-a-one", "tenant-a-two"}\n\n    assert knowledge_service.get_item_or_404(\n        db_session, tenant_a_one.id, tenant_id="tenant-a"\n    ).id == tenant_a_one.id\n    with pytest.raises(HTTPException) as hidden:\n        knowledge_service.get_item_or_404(\n            db_session, tenant_b.id, tenant_id="tenant-a"\n        )\n    assert hidden.value.status_code == 404\n\n    published, published_total = knowledge_service.search_published(\n        db_session, tenant_id="tenant-a", q="same", limit=20\n    )\n    assert published_total == 2\n    assert {row.item_key for row in published} == {"tenant-a-one", "tenant-a-two"}\n\n    conflicts = run_conflict_check(\n        db_session, KnowledgeConflictCheckRequest(limit=20), tenant_id="tenant-a"\n    )\n    assert conflicts["total"] == 1\n    assert set(conflicts["conflicts"][0]["item_ids"]) == {\n        tenant_a_one.id, tenant_a_two.id\n    }\n    assert tenant_b.id not in conflicts["conflicts"][0]["item_ids"]\n\n\ndef test_knowledge_update_cannot_move_item_between_tenants(db_session):\n    item = _item(key="tenant-update", tenant="tenant-a", question="question")\n    db_session.add(item)\n    db_session.commit()\n    payload = SimpleNamespace(\n        model_dump=lambda **_: {"tenant_id": "tenant-b", "title": "Updated"}\n    )\n    knowledge_service.update_item(\n        db_session, item, payload, actor=None, tenant_id="tenant-a"\n    )\n    assert item.tenant_id == "tenant-a"\n    assert item.title == "Updated"\n    with pytest.raises(HTTPException) as hidden:\n        knowledge_service.update_item(\n            db_session, item, payload, actor=None, tenant_id="tenant-b"\n        )\n    assert hidden.value.status_code == 404\n\n\ndef test_canonical_knowledge_api_uses_authenticated_tenant_authority():\n    source = inspect.getsource(knowledge_items)\n    assert "authoritative_tenant_key" in source\n    assert source.count("tenant_id=tenant_key") >= 9\n    assert "requested=payload.tenant_key" in source\n''',
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
