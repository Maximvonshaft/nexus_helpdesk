from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(content: str, old: str, new: str, *, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return content.replace(old, new, 1)


# Existing mature api.ts is the sole generic Transport Authority.
api_path = "webapp/src/lib/api.ts"
api = read(api_path)
api = replace_once(
    api,
    "async function request<T>(path: string, init?: RequestInit): Promise<T> {",
    "export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {",
    label="generic request declaration",
)
api = replace_once(
    api,
    "  throw lastError instanceof Error ? lastError : new Error('API request failed')\n}\n\nexport type TicketTimelinePage",
    "  throw lastError instanceof Error ? lastError : new Error('API request failed')\n}\n\nconst request = apiRequest\n\nexport type TicketTimelinePage",
    label="generic request alias",
)
write(api_path, api)

# supportApi remains a typed domain adapter and re-exports compatibility names only.
support_path = "webapp/src/lib/supportApi.ts"
support = read(support_path)
support = """import {
  apiRequest,
  ApiError,
  AuthExpiredError,
  clearToken,
  getToken,
  normalizeApiBaseUrl,
  setToken,
} from '@/lib/api'
""" + support
support, count = re.subn(
    r"\nconst STORAGE_KEY = 'helpdesk-webapp-token'.*?\nasync function request<T>\(path: string, init\?: RequestInit\): Promise<T> \{.*?\n\}\n",
    """\nexport { ApiError, AuthExpiredError }
export const normalizeSupportApiBaseUrl = normalizeApiBaseUrl
export const getSupportToken = getToken
export const setSupportToken = setToken
export const clearSupportToken = clearToken

const request = apiRequest
""",
    support,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"support transport block: expected one match, found {count}")
for forbidden in ("fetch(", "new AbortController", "createRequestId", "readErrorBody", "REQUEST_ID_HEADER"):
    if forbidden in support:
        raise SystemExit(f"supportApi retains generic transport: {forbidden}")
write(support_path, support)

# Operator Workspace keeps domain path/scope construction but delegates generic HTTP behavior.
workspace_path = "webapp/src/lib/operatorWorkspaceApi.ts"
workspace = read(workspace_path)
workspace, count = re.subn(
    r"import \{\n  ApiError,\n  AuthExpiredError,\n  clearSupportToken,\n  getSupportToken,\n  normalizeSupportApiBaseUrl,\n\} from '@/lib/supportApi'",
    "import { apiRequest, ApiError } from '@/lib/api'",
    workspace,
    count=1,
)
if count != 1:
    raise SystemExit(f"workspace transport import: expected one match, found {count}")
workspace = workspace.replace("const API_BASE_URL = normalizeSupportApiBaseUrl(import.meta.env.VITE_API_BASE_URL)\n", "")
workspace = workspace.replace("const DEFAULT_API_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 15000)\n", "")
workspace, count = re.subn(r"\nfunction apiUrl\(path: string\) \{.*?\n\}\n", "\n", workspace, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"workspace apiUrl: expected one match, found {count}")
workspace, count = re.subn(r"\nfunction createRequestId\(\) \{.*?\n\}\n", "\n", workspace, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"workspace request id: expected one match, found {count}")
workspace, count = re.subn(r"\nasync function readFailure\(.*?\n\}\n", "\n", workspace, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"workspace failure parser: expected one match, found {count}")
workspace, count = re.subn(r"\nasync function operatorRequest<T>\(.*?\n\}\n", "\n", workspace, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"workspace request wrapper: expected one match, found {count}")
workspace = workspace.replace("operatorRequest<", "apiRequest<").replace("operatorRequest(`", "apiRequest(`")
for forbidden in ("fetch(", "new AbortController", "getSupportToken", "clearSupportToken", "createRequestId", "readFailure"):
    if forbidden in workspace:
        raise SystemExit(f"operatorWorkspaceApi retains generic transport: {forbidden}")
write(workspace_path, workspace)

# Runtime audit remains a typed adapter over the same transport.
runtime_path = "webapp/src/features/runtime/aiDebugApi.ts"
runtime = read(runtime_path)
runtime = replace_once(
    runtime,
    "import { ApiError, getSupportToken, normalizeSupportApiBaseUrl } from '@/lib/supportApi'",
    "import { apiRequest } from '@/lib/api'",
    label="runtime transport import",
)
runtime, count = re.subn(
    r"\nconst API_BASE_URL = .*?\nasync function request<T>\(path: string, init\?: RequestInit\): Promise<T> \{.*?\n\}\n",
    "\n",
    runtime,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"runtime request block: expected one match, found {count}")
runtime = runtime.replace("request<", "apiRequest<")
for forbidden in ("fetch(", "new AbortController", "getSupportToken", "createRequestId", "buildApiUrl"):
    if forbidden in runtime:
        raise SystemExit(f"runtime adapter retains generic transport: {forbidden}")
write(runtime_path, runtime)

# Static admin is already deleted; Settings now fail closed when frontend_dist is absent.
settings_path = "backend/app/settings.py"
settings = read(settings_path)
settings = replace_once(
    settings,
    "        self.legacy_frontend_root = self.project_root / \"frontend\"\n        self.frontend_dist_root = self.project_root / \"frontend_dist\"\n        self.frontend_dist_index = self.frontend_dist_root / \"index.html\"\n        self.frontend_dist_available = self.frontend_dist_index.exists()\n        self.frontend_root = self.frontend_dist_root if self.frontend_dist_available else self.legacy_frontend_root\n        self.frontend_uses_legacy_fallback = not self.frontend_dist_available\n",
    "        self.frontend_dist_root = self.project_root / \"frontend_dist\"\n        self.frontend_dist_index = self.frontend_dist_root / \"index.html\"\n        self.frontend_dist_available = self.frontend_dist_index.exists()\n        self.frontend_root = self.frontend_dist_root\n",
    label="legacy Settings fallback",
)
for forbidden in ("legacy_frontend_root", "frontend_uses_legacy_fallback"):
    if forbidden in settings:
        raise SystemExit(f"Settings retains legacy frontend authority: {forbidden}")
write(settings_path, settings)

# Machine-readable authority must match actual paths and deletion state.
manifest_path = ROOT / "webapp/design/operator-console-consolidation.v1.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["version"] = "operator_console_consolidation.2026-07-14.6"
surfaces = {row["id"]: row for row in manifest["implementation_surfaces"]}
legacy = surfaces["legacy_static_admin"]
legacy.clear()
legacy.update({
    "id": "legacy_static_admin",
    "paths": ["frontend/app.js", "frontend/index.html", "frontend/style.css"],
    "disposition": "SUPERSEDED_DELETE",
    "deleted": True,
    "replacement_routes": ["/workspace", "/knowledge", "/channels", "/runtime", "/control-tower"],
})
manifest["transport_authority"] = {
    "target": "webapp/src/lib/api.ts",
    "selection": "CANONICAL_EXISTING_IMPLEMENTATION",
    "current_duplicates": [],
    "typed_adapters": [
        "webapp/src/lib/supportApi.ts",
        "webapp/src/lib/operatorWorkspaceApi.ts",
        "webapp/src/features/runtime/aiDebugApi.ts",
    ],
    "required_shared_behavior": [
        "api_base_url", "token_storage", "request_id", "timeout", "safe_retry",
        "auth_expiry", "error_normalization", "latency_telemetry", "external_abort_propagation",
    ],
    "migration_rule": "typed domain adapters delegate all generic transport behavior to webapp/src/lib/api.ts",
    "forbidden_targets": [
        "webapp/src/lib/apiClient.ts",
        "webapp/src/lib/http/httpClient.ts",
        "a_second_generic_fetch_wrapper",
    ],
}
for row in manifest["donor_convergence"]:
    if row["id"] == "static_frontend_retirement":
        row.update({
            "decision": "ALREADY_SUPERSEDED",
            "destination": "deleted_on_pr_748",
            "required_evidence": ["tracked_absence", "fallback_free_settings", "browser_route_parity", "rollback"],
            "reason": "The legacy operator product is deleted and cannot be selected as a runtime fallback.",
        })
    elif row["id"] == "single_transport_authority":
        row.update({
            "decision": "REJECT_SUPERSEDED_DONOR_PATH",
            "destination": "webapp/src/lib/api.ts",
            "required_evidence": ["all_typed_adapters_delegate", "no_direct_generic_fetch", "network_auth_tests"],
            "reason": "The current api.ts already owns the complete mature transport behavior; nonexistent donor paths are forbidden.",
        })
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# Executable contract now rejects reintroduction instead of recording known duplication.
contract_path = "webapp/tests/operator-console-consolidation-contract.test.mjs"
contract = read(contract_path)
contract = replace_once(
    contract,
    "  assert.equal(surfaces.get('legacy_static_admin')?.disposition, 'LEGACY_ACTIVE_MIGRATE_THEN_DELETE')\n",
    "  const legacyAdmin = surfaces.get('legacy_static_admin')\n  assert.equal(legacyAdmin?.disposition, 'SUPERSEDED_DELETE')\n  assert.equal(legacyAdmin?.deleted, true)\n  for (const path of legacyAdmin?.paths ?? []) {\n    assert.equal(existsSync(join(REPO_ROOT, path)), false, `legacy static admin path still exists: ${path}`)\n  }\n",
    label="legacy disposition assertion",
)
contract, count = re.subn(
    r"test\('transport duplication is frozen and must converge on one target', \(\) => \{.*?\n\}\)\n",
    """test('one generic HTTP Transport Authority owns every typed adapter', () => {
  const transport = contract().transport_authority
  assert.equal(transport.target, 'webapp/src/lib/api.ts')
  assert.deepEqual(transport.current_duplicates, [])
  assert.deepEqual(transport.typed_adapters, [
    'webapp/src/lib/supportApi.ts',
    'webapp/src/lib/operatorWorkspaceApi.ts',
    'webapp/src/features/runtime/aiDebugApi.ts',
  ])
  assert.equal(existsSync(join(REPO_ROOT, transport.target)), true)
  for (const path of transport.typed_adapters) {
    const source = read(join(REPO_ROOT, path))
    assert.doesNotMatch(source, /\\bfetch\\s*\\(/, `${path} reintroduced direct fetch`)
    assert.doesNotMatch(source, /new AbortController/, `${path} reintroduced timeout transport`)
    assert.match(source, /apiRequest/, `${path} must delegate to api.ts`)
  }
  assert.ok(transport.required_shared_behavior.includes('auth_expiry'))
  assert.ok(transport.required_shared_behavior.includes('external_abort_propagation'))
})
""",
    contract,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"transport contract: expected one match, found {count}")
write(contract_path, contract)

# Reconcile focused backend expectations with the deleted fallback and delegated transport.
round27_path = "backend/tests/test_round27_frontend_hardening.py"
round27 = read(round27_path)
round27 = round27.replace("    assert 'Authorization' in support_api\n    assert 'clearSupportToken()' in support_api\n", "    assert \"from '@/lib/api'\" in support_api\n    assert 'apiRequest' in support_api\n    assert 'fetch(' not in support_api\n")
round27 = round27.replace("    assert 'refusing legacy frontend fallback' in settings\n", "    assert 'legacy_frontend_root' not in settings\n    assert 'frontend_uses_legacy_fallback' not in settings\n")
round27, count = re.subn(r"\n\ndef test_legacy_frontend_copy_is_business_friendly\(\):.*?(?=\n\ndef test_round27_smoke_script_exists)", "", round27, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"legacy frontend copy test: expected one match, found {count}")
write(round27_path, round27)

print("canonical transport and static retirement patch applied")
