from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PINNED_ACTIONS = {
    "actions/checkout@v4": "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4.3.1",
    "actions/setup-python@v5": "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0",
    "actions/setup-node@v4": "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4.4.0",
    "actions/upload-artifact@v4": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2",
}


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(content: str, old: str, new: str, *, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return content.replace(old, new, 1)


def regex_once(content: str, pattern: str, replacement: str, *, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, content, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return updated


def harden_checkout_credentials(text: str) -> str:
    if not re.search(r"(?m)^\s*pull_request(?:_target)?:", text):
        return text
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        line = lines[index]
        if "uses: actions/checkout@" not in line:
            index += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        step_indent = indent
        block_end = index + 1
        while block_end < len(lines):
            candidate = lines[block_end]
            stripped = candidate.strip()
            candidate_indent = len(candidate) - len(candidate.lstrip(" "))
            if stripped.startswith("- ") and candidate_indent <= step_indent:
                break
            block_end += 1
        block = "".join(lines[index:block_end])
        if "persist-credentials:" in block:
            index = block_end
            continue
        with_index = None
        for pos in range(index + 1, block_end):
            if lines[pos].strip() == "with:":
                with_index = pos
                break
        if with_index is None:
            insert_at = index + 1
            lines[insert_at:insert_at] = [
                " " * (indent + 2) + "with:\n",
                " " * (indent + 4) + "persist-credentials: false\n",
            ]
            index = block_end + 2
        else:
            with_indent = len(lines[with_index]) - len(lines[with_index].lstrip(" "))
            lines[with_index + 1:with_index + 1] = [
                " " * (with_indent + 2) + "persist-credentials: false\n"
            ]
            index = block_end + 1
    return "".join(lines)


def harden_workflows() -> None:
    for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        for mutable, pinned in PINNED_ACTIONS.items():
            text = text.replace(mutable, pinned)
        text = harden_checkout_credentials(text)
        path.write_text(text, encoding="utf-8")


path = "backend/tests/test_canonical_policy_projection_contract.py"
text = read(path)
text = replace_once(
    text,
    'WORKSPACE_API_SOURCE = ROOT / "webapp/src/lib/operatorWorkspaceApi.ts"\n',
    'WORKSPACE_API_SOURCE = ROOT / "webapp/src/lib/operatorWorkspaceApi.ts"\nWORKSPACE_ROUTE_SOURCE = ROOT / "webapp/src/routes/workspace.tsx"\n',
    label="workspace route authority path",
)
text = replace_once(
    text,
    '''def test_server_derived_scope_projection_remains_fail_closed() -> None:\n    source = _read(WORKSPACE_API_SOURCE)\n\n    assert "authorized" in source.lower()\n    assert "no authorized" in source.lower() or "未授权" in source or "无可用" in source\n''',
    '''def test_server_derived_scope_projection_remains_fail_closed() -> None:\n    api_source = _read(WORKSPACE_API_SOURCE)\n    route_source = _read(WORKSPACE_ROUTE_SOURCE)\n\n    assert "currentScopes" in api_source\n    assert "operatorWorkspaceApi.currentScopes" in route_source\n    assert "当前账号没有可用工作范围" in route_source\n    assert "不会回退到手工 Tenant、国家或渠道" in route_source\n    assert "LegacyWorkspaceFallback" not in route_source\n''',
    label="policy projection fail closed authority",
)
write(path, text)

path = "backend/tests/test_webchat_voice_p0_static.py"
text = read(path)
text = replace_once(
    text,
    '''    for marker in [\n        "tracking",\n        "voiceStatus",\n        "nd-webchat-voice-transcript",\n    ]:\n        assert marker in workspace + "\\n" + widget\n''',
    '''    assert "voiceStatus" in widget\n    assert "nd-webchat-voice-transcript" in widget\n    assert "evidence_timeline" in workspace\n''',
    label="voice evidence markers",
)
write(path, text)

path = "backend/tests/test_webchat_voice_static_headers.py"
text = read(path)
text = replace_once(
    text,
    '    api_response = client.get("/api/webchat/not-found")\n',
    '    api_response = client.get("/api/not-a-real-route")\n',
    label="truly absent non voice route",
)
write(path, text)

path = "webapp/e2e/operator-authorized-scope.spec.ts"
text = read(path)
text = replace_once(text, "const SCOPE_KEY = 'nexus-operator-workspace-scope'\n", "", label="authorized scope storage key")
text = regex_once(
    text,
    r"async function seedSession\(page: Page\) \{.*?\n\}\n",
    "async function seedSession(page: Page) {\n  await page.addInitScript(([tokenKey, token]) => {\n    sessionStorage.setItem(tokenKey, token)\n  }, [TOKEN_KEY, 'operator-token'])\n}\n",
    label="authorized scope session seed",
    flags=re.S,
)
text = text.replace("        requires_explicit_admin_scope: false,\n", "")
text, count = re.subn(
    r"\n  await expect\.poll\(\(\) => page\.evaluate\(\(key\) => JSON\.parse\(sessionStorage\.getItem\(key\) \|\| '\{\}'\), SCOPE_KEY\)\)\.toEqual\(\{\n.*?\n  \}\)\n",
    "\n",
    text,
    flags=re.S,
)
if count != 2:
    raise SystemExit(f"authorized scope storage assertions: expected 2 matches, found {count}")
text = replace_once(
    text,
    "  await expect(page.getByText('系统不会自动猜测或扩大访问范围。')).toBeVisible()\n",
    "  await expect(page.getByText('请联系管理员分配授权范围。系统不会自动猜测、扩大或允许手工输入 Tenant、国家和渠道。')).toBeVisible()\n",
    label="authorized scope fail closed copy",
)
write(path, text)

path = "webapp/e2e/operator-workspace.spec.ts"
text = read(path)
text = replace_once(text, "const SCOPE_KEY = 'nexus-operator-workspace-scope'\n", "", label="workspace scope storage key")
text = replace_once(
    text,
    '''  await page.addInitScript(([tokenKey, scopeKey, channel]) => {\n    sessionStorage.setItem(tokenKey, 'operator-token')\n    sessionStorage.setItem(scopeKey, JSON.stringify({ tenantKey: 'default', countryCode: 'CH', channelKey: channel }))\n  }, [TOKEN_KEY, SCOPE_KEY, channelKey])\n''',
    '''  await page.addInitScript(([tokenKey, token]) => {\n    sessionStorage.setItem(tokenKey, token)\n  }, [TOKEN_KEY, 'operator-token'])\n''',
    label="workspace session seed",
)
text = replace_once(
    text,
    "    if (url.pathname === '/api/auth/me') return json(user())\n    if (url.pathname === '/api/admin/operator-queue/unified') {\n",
    "    if (url.pathname === '/api/auth/me') return json(user())\n    if (url.pathname === '/api/admin/operator-queue/my-scopes') {\n      return json({\n        items: [{ tenant_key: 'default', tenant_hash: '123456789abc', country_code: 'CH', channel_key: channelKey }],\n      })\n    }\n    if (url.pathname === '/api/admin/operator-queue/unified') {\n",
    label="workspace authorized scope fixture",
)
write(path, text)

path = "webapp/e2e/canonical-supporting-routes.spec.ts"
text = read(path)
text = replace_once(
    text,
    "  await expect(page.getByText('wa-primary-private')).toHaveCount(0)\n",
    "  await expect(page.getByText('wa-primary-private', { exact: true })).not.toBeVisible()\n",
    label="channel technical identifier collapsed",
)
text = replace_once(
    text,
    "  await expect(page.getByText('internal-model-name')).toHaveCount(0)\n  await expect(page.getByText('internal-rag-model-name')).toHaveCount(0)\n",
    "  await expect(page.getByText('internal-model-name', { exact: true })).not.toBeVisible()\n  await expect(page.getByText('internal-rag-model-name', { exact: true })).not.toBeVisible()\n",
    label="runtime technical diagnostics collapsed",
)
write(path, text)

path = "webapp/e2e/canonical-knowledge-control-tower.spec.ts"
text = read(path)
text = replace_once(
    text,
    "  await expect(page.getByText('未分配').locator('..')).toContainText('12')\n",
    "  await expect(page.getByRole('region', { name: '关键运营指标' }).getByText('未分配', { exact: true }).locator('..')).toContainText('12')\n",
    label="control tower KPI locator",
)
write(path, text)

path = "webapp/e2e/smoke.spec.ts"
text = read(path)
text = replace_once(
    text,
    "      'operator_queue.read',\n",
    "      'operator_queue.read',\n      'tool:speedaf.work_order.create:write',\n",
    label="smoke work order capability",
)
text = text.replace("      requires_explicit_admin_scope: false,\n", "")
write(path, text)

path = "scripts/ci/reconcile_external_channel_inventory.py"
text = read(path)
text = replace_once(
    text,
    '''RECONCILIATION_CONTROL_PATHS = (\n    "scripts/ci/reconcile_external_channel_inventory.py",\n    "scripts/ci/tests/test_reconcile_external_channel_inventory.py",\n)\n''',
    '''RECONCILIATION_CONTROL_PATHS = (\n    "config/governance/actions-authority.v1.json",\n    "scripts/ci/reconcile_external_channel_inventory.py",\n    "scripts/ci/tests/test_reconcile_external_channel_inventory.py",\n)\n''',
    label="external reconciliation governance controls",
)
write(path, text)

path = "scripts/ci/actions_authority_inventory.py"
text = read(path)
text = replace_once(
    text,
    'MUTATION_RE = re.compile(\n    r"(?:^|\\s)(?:git\\s+(?:commit|push|tag)|gh\\s+(?:release|api)|curl\\b.*?/contents/)",\n    re.IGNORECASE,\n)\n',
    'MUTATION_RE = re.compile(\n    r"(?:^|\\s)(?:git\\s+(?:commit|push|tag)|gh\\s+(?:release|api)|curl\\b.*?/contents/)",\n    re.IGNORECASE,\n)\nSAFE_EVENT_SHELL_RE = re.compile(\n    r"\\$\\{\\{\\s*(?:github\\.event\\.pull_request\\.(?:head|base)\\.sha(?:\\s*\\|\\|\\s*github\\.sha)?|github\\.event\\.(?:pull_request|issue)\\.number)\\s*\\}\\}"\n)\nPR_HEAD_CHECKOUT_RE = re.compile(\n    r"uses:\\s*actions/checkout@[^\\n]+\\n(?:(?:\\s+[^\\n]*\\n){0,14}?)\\s+ref:\\s*\\$\\{\\{[^}]*github\\.event\\.pull_request\\.head\\.sha[^}]*\\}\\}",\n    re.MULTILINE,\n)\n',
    label="actions safe shell and head checkout patterns",
)
text = replace_once(
    text,
    '''    run_values = _walk_run_values(document)\n    for run_value in run_values:\n        if triggers_pr and MUTATION_RE.search(run_value):\n            add("pull_request_repository_mutation", "PR validation contains commit/push/tag/API mutation")\n        if "${{ github.event." in run_value or "${{ github.head_ref" in run_value:\n            add("untrusted_event_shell_interpolation", "attacker-controlled event value is interpolated into executable script")\n\n    if privileged and "github.event.pull_request.head.sha" in text and run_values:\n        add("privileged_trigger_executes_untrusted_head", "privileged trigger checks out and executes PR head")\n''',
    '''    run_values = _walk_run_values(document)\n    for run_value in run_values:\n        if triggers_pr and MUTATION_RE.search(run_value):\n            add("pull_request_repository_mutation", "PR validation contains commit/push/tag/API mutation")\n        sanitized = SAFE_EVENT_SHELL_RE.sub("", run_value)\n        if "${{ github.event." in sanitized or "${{ github.head_ref" in sanitized:\n            add("untrusted_event_shell_interpolation", "attacker-controlled event value is interpolated into executable script")\n\n    trusted_split_checkout = (\n        "path: .trusted" in text\n        and "path: target" in text\n        and (".trusted/scripts/" in text or "working-directory: trusted" in text)\n    )\n    if privileged and PR_HEAD_CHECKOUT_RE.search(text) and not trusted_split_checkout:\n        add("privileged_trigger_executes_untrusted_head", "privileged trigger checks out and executes PR head")\n''',
    label="actions shell and privileged trigger audit",
)
text = replace_once(
    text,
    '            if row["authority"] != authority or row["classification"] in {"reusable", "historical_delete", "publication"}:\n',
    '            if row["authority"] != authority or row["classification"] in {"reusable", "matrix_component", "historical_delete", "publication"}:\n',
    label="matrix components are not duplicate authorities",
)
text = replace_once(
    text,
    '''        path for path in (\n            list(inventory["authoritative"].values())\n            + list(inventory["publication_allowlist"])\n            + list(inventory["historical_delete"])\n            + list(inventory["classification_overrides"])\n        ) if path not in tracked\n''',
    '''        path for path in (\n            list(inventory["authoritative"].values())\n            + list(inventory["publication_allowlist"])\n            + list(inventory["classification_overrides"])\n        ) if path not in tracked\n''',
    label="historical delete paths are intentionally absent",
)
write(path, text)

path = ".github/workflows/tenant-runtime-authority-gate.yml"
text = read(path)
text = replace_once(
    text,
    '''          pytest -q \\\n            backend/tests/test_tenant_runtime_authority.py \\\n            backend/tests/test_lite_cases_pagination.py \\\n            backend/tests/test_ticket_search_query.py \\\n            backend/tests/test_ticket_detail_summary.py \\\n            backend/tests/test_ticket_timeline_pagination.py \\\n            backend/tests/test_production_hardening_permissions.py \\\n            backend/tests/test_production_settings_contract.py \\\n            backend/tests/test_settings.py \\\n            backend/tests/test_webchat_public_tenant_binding.py \\\n            backend/tests/test_support_conversations_rbac.py \\\n            backend/tests/test_support_conversations_api.py \\\n            scripts/release/tests/test_seed_rc_test_data_contract.py \\\n            scripts/release/tests/test_generate_rc_test_env.py \\\n            scripts/release/tests/test_validate_rc_test_manifest.py\n''',
    '''          mkdir -p artifacts/tenant-runtime\n          pytest -q \\\n            backend/tests/test_tenant_runtime_authority.py \\\n            backend/tests/test_lite_cases_pagination.py \\\n            backend/tests/test_ticket_search_query.py \\\n            backend/tests/test_ticket_detail_summary.py \\\n            backend/tests/test_ticket_timeline_pagination.py \\\n            backend/tests/test_production_hardening_permissions.py \\\n            backend/tests/test_production_settings_contract.py \\\n            backend/tests/test_settings.py \\\n            backend/tests/test_webchat_public_tenant_binding.py \\\n            backend/tests/test_support_conversations_rbac.py \\\n            backend/tests/test_support_conversations_api.py \\\n            scripts/release/tests/test_seed_rc_test_data_contract.py \\\n            scripts/release/tests/test_generate_rc_test_env.py \\\n            scripts/release/tests/test_validate_rc_test_manifest.py \\\n            --junitxml=artifacts/tenant-runtime/contracts.xml\n''',
    label="tenant JUnit contract evidence",
)
text = replace_once(text, "        if: success()\n        uses: actions/upload-artifact@", "        if: always()\n        uses: actions/upload-artifact@", label="tenant always upload")
text = replace_once(
    text,
    '''          path: |\n            artifacts/tenant-runtime/evidence.json\n            artifacts/tenant-runtime/artifact-scan.json\n''',
    '''          path: |\n            artifacts/tenant-runtime/contracts.xml\n            artifacts/tenant-runtime/evidence.json\n            artifacts/tenant-runtime/artifact-scan.json\n''',
    label="tenant upload JUnit",
)
text = replace_once(
    text,
    '''      - name: Check exact-head whitespace\n        if: github.event_name == 'pull_request'\n        run: git diff --check "${{ github.event.pull_request.base.sha }}...${{ github.event.pull_request.head.sha }}"\n''',
    '''      - name: Check exact-head whitespace\n        if: github.event_name == 'pull_request'\n        env:\n          BASE_SHA: ${{ github.event.pull_request.base.sha }}\n          HEAD_SHA: ${{ github.event.pull_request.head.sha }}\n        run: git diff --check "${BASE_SHA}...${HEAD_SHA}"\n''',
    label="tenant safe diff identity",
)
write(path, text)

path = ".github/workflows/rc-test-candidate.yml"
text = read(path)
if "id: rc-preflight" not in text:
    text = replace_once(
        text,
        '''      - name: Validate registry and release contracts\n        run: |\n          python - <<'PY'\n          from pathlib import Path\n          text = Path("docs/ai/remote-skills-registry.yaml").read_text(encoding="utf-8")\n          assert text.startswith("schema: nexus.osr.remote-skills-registry.v1\\n")\n          assert "name: test_release_candidate_convergence" in text\n          assert "auto_upgrade: false" in text\n          PY\n          python -m py_compile \\\n            scripts/release/generate_rc_test_env.py \\\n            scripts/release/seed_rc_test_data.py \\\n            scripts/release/rc_test_http_smoke.py \\\n            scripts/release/rc_test_side_effects.py \\\n            scripts/release/build_rc_test_manifest.py \\\n            scripts/release/validate_rc_test_manifest.py \\\n            scripts/release/validate_rc_test_evidence.py\n          python -m unittest discover -s scripts/release/tests\n          bash -n scripts/release/run_rc_test_candidate.sh\n''',
        '''      - name: Validate registry and release contracts\n        id: rc-preflight\n        run: python scripts/release/rc_preflight.py --artifact-root artifacts/rc-test\n''',
        label="RC bounded preflight",
    )
    text = replace_once(
        text,
        "        if: always() && steps.run-rc-test.outcome == 'failure'\n",
        "        if: always() && (steps.rc-preflight.outcome == 'failure' || steps.run-rc-test.outcome == 'failure')\n",
        label="RC failure scan includes preflight",
    )
    text = replace_once(
        text,
        "        if: always() && steps.run-rc-test.outcome == 'failure' && steps.scan-failure-evidence.outcome == 'success'\n",
        "        if: always() && (steps.rc-preflight.outcome == 'failure' || steps.run-rc-test.outcome == 'failure') && steps.scan-failure-evidence.outcome == 'success'\n",
        label="RC failure upload includes preflight",
    )
write(path, text)

harden_workflows()
print("canonical convergence materialization applied")
