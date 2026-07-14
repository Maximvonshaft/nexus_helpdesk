from __future__ import annotations

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


# Remove environment/session storage as normal scope authority.
api_path = "webapp/src/lib/operatorWorkspaceApi.ts"
api = read(api_path)
api = replace_once(api, "const WORKSPACE_SCOPE_STORAGE_KEY = 'nexus-operator-workspace-scope'\n", "", label="scope storage key")
api = re.sub(
    r"\nexport function loadWorkspaceScope\(\): WorkspaceScope \{.*?\n\}\n\nexport function saveWorkspaceScope\(scope: WorkspaceScope\) \{.*?\n\}\n",
    "\n",
    api,
    count=1,
    flags=re.S,
)
if "loadWorkspaceScope" in api or "saveWorkspaceScope" in api or "VITE_NEXUS_" in api or "sessionStorage" in api:
    raise SystemExit("operatorWorkspaceApi still contains manual/environment scope authority")
write(api_path, api)

# Remove the obsolete special-admin response vocabulary.
types_path = "webapp/src/lib/operatorWorkspaceTypes.ts"
types = read(types_path)
types = replace_once(types, "  requires_explicit_admin_scope: boolean\n", "", label="frontend admin scope flag")
write(types_path, types)

schema_path = "backend/app/operator_schemas.py"
schema = read(schema_path)
schema = replace_once(schema, "    requires_explicit_admin_scope: bool = False\n", "", label="backend admin scope flag")
write(schema_path, schema)

# The route owns server-derived scope selection and passes the exact authorized tuple.
route_path = "webapp/src/routes/workspace.tsx"
write(route_path, """import { lazy, Suspense, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AppShell } from '@/app/AppShell'
import '@/app/app-shell.css'
import { Button } from '@/components/ui/Button'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { useLogout, useSession } from '@/hooks/useAuth'
import { getSupportToken } from '@/lib/supportApi'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import { workspaceScopeFromAuthorized, workspaceScopeKey } from '@/lib/operatorWorkspaceTypes'
import type { AuthorizedWorkspaceScope } from '@/lib/operatorWorkspaceTypes'

const LazyOperatorWorkspacePage = lazy(() => import('@/features/operator-workspace/lazy'))

function WorkspaceLoading() {
  return (
    <main className=\"operator-workspace\" aria-busy=\"true\">
      <section className=\"operator-session-state\" role=\"status\" aria-live=\"polite\">
        <strong>正在加载操作员工作台…</strong>
        <p>正在载入统一队列、案例状态和受控动作界面。</p>
      </section>
    </main>
  )
}

function authorizedScopeKey(scope: AuthorizedWorkspaceScope) {
  return workspaceScopeKey(workspaceScopeFromAuthorized(scope))
}

function AuthorizedWorkspaceRoutePage() {
  const navigate = useNavigate()
  const logout = useLogout()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const [requestedScopeKey, setRequestedScopeKey] = useState<string | null>(null)

  const scopes = useQuery({
    queryKey: ['operatorWorkspaceAuthorizedScopes'],
    queryFn: ({ signal }) => operatorWorkspaceApi.currentScopes({ signal }),
    enabled: Boolean(session.data),
    retry: false,
    staleTime: 30_000,
  })

  const authorizedScopes = scopes.data?.items ?? []
  const selectedScope = authorizedScopes.find((scope) => authorizedScopeKey(scope) === requestedScopeKey)
    ?? authorizedScopes[0]
    ?? null
  const selectedKey = selectedScope ? authorizedScopeKey(selectedScope) : null

  const handleLogout = () => {
    logout()
    navigate({ to: '/login', replace: true })
  }

  if (!session.data && (session.isLoading || !session.isError)) return <WorkspaceLoading />

  if (session.isError) {
    return (
      <main className=\"nd-app-boundary-state\">
        <ErrorSummary
          title=\"无法读取当前账号\"
          errors={['登录状态可能已失效，请重新登录。']}
          action={<Button onClick={handleLogout}>返回登录</Button>}
        />
      </main>
    )
  }

  if (scopes.isLoading) return <WorkspaceLoading />

  if (scopes.isError) {
    return (
      <AppShell
        activeRoute=\"workspace\"
        capabilities={capabilities}
        userLabel={session.data?.display_name || session.data?.username || '操作员'}
        onLogout={handleLogout}
      >
        <main className=\"nd-app-boundary-state\">
          <ErrorSummary
            title=\"无法读取授权工作范围\"
            errors={['服务器未能返回当前账号的授权范围。系统不会回退到手工 Tenant、国家或渠道。']}
            action={<Button onClick={() => scopes.refetch()}>重新加载</Button>}
          />
        </main>
      </AppShell>
    )
  }

  if (!selectedScope || !selectedKey) {
    return (
      <AppShell
        activeRoute=\"workspace\"
        capabilities={capabilities}
        userLabel={session.data?.display_name || session.data?.username || '操作员'}
        onLogout={handleLogout}
      >
        <main className=\"nd-app-boundary-state\">
          <section className=\"empty-state\" role=\"status\" aria-labelledby=\"workspace-no-scope-title\">
            <h1 id=\"workspace-no-scope-title\">当前账号没有可用工作范围</h1>
            <p>请联系管理员分配授权范围。系统不会自动猜测、扩大或允许手工输入 Tenant、国家和渠道。</p>
          </section>
        </main>
      </AppShell>
    )
  }

  return (
    <AppShell
      activeRoute=\"workspace\"
      capabilities={capabilities}
      userLabel={session.data?.display_name || session.data?.username || '操作员'}
      scopes={authorizedScopes}
      selectedScope={selectedScope}
      onScopeChange={(scope) => setRequestedScopeKey(authorizedScopeKey(scope))}
      onLogout={handleLogout}
    >
      <Suspense fallback={<WorkspaceLoading />}>
        <LazyOperatorWorkspacePage
          key={selectedKey}
          scope={workspaceScopeFromAuthorized(selectedScope)}
        />
      </Suspense>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/workspace',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: AuthorizedWorkspaceRoutePage,
})
""")

# Remove the free-text ScopeEditor and make the page consume only the route-projected scope.
page_path = "webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx"
page = read(page_path)
page = replace_once(
    page,
    "import { loadWorkspaceScope, operatorWorkspaceApi, saveWorkspaceScope } from '@/lib/operatorWorkspaceApi'",
    "import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'",
    label="workspace API import",
)
page, count = re.subn(r"\nfunction ScopeEditor\(.*?\n\}\n\nfunction QueueFilters", "\nfunction QueueFilters", page, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"ScopeEditor function: expected one match, found {count}")
page = replace_once(
    page,
    "export function OperatorWorkspacePage() {",
    "export function OperatorWorkspacePage({ scope }: { scope: WorkspaceScope }) {",
    label="workspace component signature",
)
page, count = re.subn(
    r"\n  const \[scopeDraft, setScopeDraft\] = useState<WorkspaceScope>\(\(\) => loadWorkspaceScope\(\)\)\n  const \[scope, setScope\] = useState<WorkspaceScope \| null>\(\(\) => \{.*?\n  \}\)\n",
    "\n",
    page,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"scope state block: expected one match, found {count}")
page, count = re.subn(
    r"\n  const applyScope = \(\) => runWithReplyDraftGuard\(\(\) => \{.*?\n  \}\)\n",
    "\n",
    page,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"applyScope block: expected one match, found {count}")
page, count = re.subn(
    r"\n  const appliedScopeMatches = Boolean\(.*?\n  \)\n",
    "\n",
    page,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"applied scope block: expected one match, found {count}")
page, count = re.subn(
    r"\n            <ScopeEditor\n.*?\n            />",
    "",
    page,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"ScopeEditor render: expected one match, found {count}")
for forbidden in ("scopeDraft", "setScopeDraft", "applyScope", "loadWorkspaceScope", "saveWorkspaceScope", "<ScopeEditor"):
    if forbidden in page:
        raise SystemExit(f"workspace page still contains {forbidden}")
write(page_path, page)

# Update canonical contracts to reject any local/manual fallback vocabulary.
contract_path = "webapp/tests/canonical-shell-contract.test.mjs"
contract = read(contract_path)
contract = replace_once(
    contract,
    "  assert.match(route, /saveWorkspaceScope\\(workspaceScopeFromAuthorized\\(selectedScope\\)\\)/)\n",
    "  assert.match(route, /scope=\\{workspaceScopeFromAuthorized\\(selectedScope\\)\\}/)\n  assert.doesNotMatch(route, /loadWorkspaceScope|saveWorkspaceScope|LegacyWorkspaceFallback/)\n",
    label="canonical route projection assertion",
)
contract = replace_once(
    contract,
    "  assert.match(route, /requires_explicit_admin_scope/)\n  assert.match(route, /LegacyWorkspaceFallback/)\n",
    "  assert.doesNotMatch(route, /requires_explicit_admin_scope|LegacyWorkspaceFallback/)\n  assert.match(route, /不会回退到手工 Tenant、国家或渠道/)\n",
    label="fail closed route assertion",
)
write(contract_path, contract)

# Reconcile backend current-scope tests to the single grant authority and response shape.
current_scope_test = "backend/tests/test_operator_queue_current_scopes.py"
test_source = read(current_scope_test)
test_source = test_source.replace("    assert validated.requires_explicit_admin_scope is False\n", "")
test_source = test_source.replace("    assert result.requires_explicit_admin_scope is False\n", "")
test_source = test_source.replace("    assert result.requires_explicit_admin_scope is True\n", "")
test_source = test_source.replace(
    "def test_current_user_receives_only_own_active_team_country_scopes(db_session):",
    "def test_current_user_receives_only_own_active_grant_scopes(db_session):",
)
test_source = replace_once(
    test_source,
    "    assert [item.model_dump() for item in validated.items] == [\n        {\n            \"tenant_key\": \"tenant-me\",\n            \"tenant_hash\": validated.items[0].tenant_hash,\n            \"country_code\": \"ME\",\n            \"channel_key\": \"webchat\",\n        }\n    ]\n",
    "    assert [(item.tenant_key, item.country_code, item.channel_key) for item in validated.items] == [\n        (\"tenant-wrong-country\", \"CH\", \"webchat\"),\n        (\"tenant-me\", \"ME\", \"webchat\"),\n    ]\n",
    label="current scope projection expectation",
)
test_source = test_source.replace("    assert \"tenant-wrong-country\" not in serialized\n", "    assert \"tenant-wrong-country\" in serialized\n")
write(current_scope_test, test_source)

# A grant, not a team-country inference, is the exact queue authority.
unified_test = "backend/tests/test_unified_operator_queue.py"
unified = read(unified_test)
unified = replace_once(
    unified,
    "def test_team_country_intersection_cannot_be_expanded_by_grant(db_session):\n    admin, agent, *_ = _seed_all(db_session)\n    _grant(db_session, admin=admin, user=agent, country=\"CH\")\n    db_session.commit()\n    with pytest.raises(HTTPException) as exc:\n        _list(db_session, agent, country_code=\"CH\")\n    assert exc.value.detail == \"operator_queue_team_scope_mismatch\"\n",
    "def test_exact_grant_country_is_authorized_without_team_country_inference(db_session):\n    admin, agent, *_ = _seed_all(db_session)\n    _grant(db_session, admin=admin, user=agent, country=\"CH\")\n    db_session.commit()\n    result = _list(db_session, agent, country_code=\"CH\")\n    assert result[\"scope\"][\"country_code\"] == \"CH\"\n",
    label="team-country legacy test",
)
write(unified_test, unified)

print("policy projection frontend patch applied")
