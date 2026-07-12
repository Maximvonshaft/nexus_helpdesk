from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "webapp/src/features/support-console/SupportConsolePage.tsx"
CSS = ROOT / "webapp/src/features/support-console/support-console.css"
E2E = ROOT / "webapp/e2e/smoke.spec.ts"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match, found {count}")
    return updated


def patch_console() -> None:
    text = CONSOLE.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "import { Field, Input, Select, Textarea } from '@/components/ui/Field'\n",
        "import { Field, Input, Select, Textarea } from '@/components/ui/Field'\n"
        "import { TechnicalDetails } from '@/components/ui/TechnicalDetails'\n"
        "import {\n"
        "  channelPresentation,\n"
        "  controlledActionPresentation,\n"
        "  healthPresentation,\n"
        "  runtimePresentation,\n"
        "  sourceConversationPresentation,\n"
        "} from '@/lib/supportStatus'\n",
        "support status imports",
    )

    text = replace_once(
        text,
        "    query: params.get('q') || '',\n",
        "    query: '',\n",
        "do not restore PII search from URL",
    )
    text = replace_once(
        text,
        "    if (query.trim()) params.set('q', query.trim())\n    else params.delete('q')\n",
        "    params.delete('q')\n",
        "do not persist PII search in URL",
    )

    text = regex_once(
        text,
        r"\nfunction toneForChannel\(channel: string\): BadgeTone \{.*?\nfunction authorLabel",
        "\nfunction authorLabel",
        "remove ambiguous local status helpers",
    )

    old_row = """function ConversationRow({ item, active, onSelect }: { item: SupportConversation; active: boolean; onSelect: () => void }) {
  return (
    <button type=\"button\" className={`support-row${active ? ' active' : ''}`} onClick={onSelect} aria-pressed={active}>
      <span className=\"support-row-top\">
        <span className=\"support-row-title\">{sanitizeDisplayText(item.display_name || item.customer_contact || '客户')}</span>
        <Badge tone={toneForChannel(item.channel)}>{item.channel === 'whatsapp' ? 'WhatsApp' : 'WebChat'}</Badge>
      </span>
      <span className=\"support-row-preview\">
        {item.latest_author ? `${authorLabel(item.latest_author)}：` : null}
        {sanitizeDisplayText(item.latest_message || item.title || '暂无消息')}
      </span>
      <span className=\"support-row-bottom\">
        <Badge tone={toneForConversation(item)}>{stateLabel(item)}</Badge>
        <span>{item.updated_at ? formatDateTime(item.updated_at) : '未更新'}</span>
      </span>
    </button>
  )
}
"""
    new_row = """function ConversationRow({ item, active, onSelect }: { item: SupportConversation; active: boolean; onSelect: () => void }) {
  const channelState = channelPresentation(item.channel)
  const conversationState = sourceConversationPresentation(item)
  return (
    <button type=\"button\" className={`support-row${active ? ' active' : ''}`} onClick={onSelect} aria-pressed={active}>
      <span className=\"support-row-top\">
        <span className=\"support-row-title\">{sanitizeDisplayText(item.display_name || item.customer_contact || '客户')}</span>
        <Badge tone={channelState.tone}>{channelState.label}</Badge>
      </span>
      <span className=\"support-row-preview\">
        {item.latest_author ? `${authorLabel(item.latest_author)}：` : null}
        {sanitizeDisplayText(item.latest_message || item.title || '暂无消息')}
      </span>
      <span className=\"support-row-bottom\">
        <Badge tone={conversationState.tone}>{conversationState.label}</Badge>
        <span>{item.updated_at ? formatDateTime(item.updated_at) : '未更新'}</span>
      </span>
    </button>
  )
}
"""
    text = replace_once(text, old_row, new_row, "truthful conversation row")

    text = replace_once(
        text,
        '<EmptyState title="暂无记忆证据" description="当前会话还没有可展示的知识、工具或接管证据。" />',
        '<EmptyState title="暂无案例证据" description="当前会话还没有可展示的事实、知识、工具或接管依据。" />',
        "case evidence copy",
    )

    runtime_trace_old = """      {runtimeTrace ? (
        <div className=\"support-side-note\">
          <span>Runtime trace</span>
          <div className=\"support-runtime-trace\">
            <strong>{sanitizeDisplayText(String(runtimeTrace.latency_class || 'standard'))}</strong>
            <small>{sanitizeDisplayText(String(runtimeTrace.model || 'model unknown'))}</small>
            <small>
              eval {compactLatency(evalElapsed)}
              {promptElapsed !== null ? ` · prompt ${compactLatency(promptElapsed)}` : ''}
            </small>
          </div>
        </div>
      ) : null}
"""
    runtime_trace_new = """      {runtimeTrace ? (
        <TechnicalDetails title=\"技术详情\" summary=\"AI Runtime 诊断\">
          <div className=\"support-runtime-trace\">
            <strong>{sanitizeDisplayText(String(runtimeTrace.latency_class || 'standard'))}</strong>
            <small>{sanitizeDisplayText(String(runtimeTrace.model || 'model unknown'))}</small>
            <small>
              eval {compactLatency(evalElapsed)}
              {promptElapsed !== null ? ` · prompt ${compactLatency(promptElapsed)}` : ''}
            </small>
          </div>
        </TechnicalDetails>
      ) : null}
"""
    text = replace_once(text, runtime_trace_old, runtime_trace_new, "progressive runtime details")

    text = replace_once(
        text,
        "  const actionResult = workOrderMutation.data || addressMutation.data || cancelConfirmMutation.data\n  const lookupResult = waybillLookupMutation.data\n",
        "  const actionResult = workOrderMutation.data || addressMutation.data || cancelConfirmMutation.data\n"
        "  const actionPresentation = actionResult\n"
        "    ? controlledActionPresentation(actionResult.status, actionResult.message)\n"
        "    : null\n"
        "  const lookupResult = waybillLookupMutation.data\n",
        "controlled action presentation",
    )

    action_old = """        {actionResult ? (
          <div className=\"support-action-result success\">
            <strong>{sanitizeDisplayText(actionResult.message || actionResult.status)}</strong>
            {actionResult.jobId ? <small>Job #{actionResult.jobId}</small> : null}
          </div>
        ) : null}
"""
    action_new = """        {actionResult && actionPresentation ? (
          <div className={`support-action-result ${actionPresentation.tone}`} role=\"status\" aria-live=\"polite\">
            <strong>{actionPresentation.label}</strong>
            {actionPresentation.detail ? <small>{sanitizeDisplayText(actionPresentation.detail)}</small> : null}
            {actionResult.jobId ? (
              <TechnicalDetails title=\"技术详情\" summary=\"请求追踪信息\">
                <code translate=\"no\">Job #{actionResult.jobId}</code>
              </TechnicalDetails>
            ) : null}
          </div>
        ) : null}
"""
    text = replace_once(text, action_old, action_new, "truthful action result")
    text = replace_once(
        text,
        "<div className={`support-action-result ${cancelPreview.cancelAllowed ? 'success' : 'warning'}`}>",
        "<div className={`support-action-result ${cancelPreview.cancelAllowed ? 'default' : 'warning'}`} role=\"status\" aria-live=\"polite\">",
        "cancel eligibility is not business success",
    )

    text = replace_once(
        text,
        "function OverviewPanel({\n  activeConversation,\n  supportMemory,\n  onDone,\n}: {\n  activeConversation?: SupportConversation\n  supportMemory?: SupportMemoryLedger | null\n  onDone: () => Promise<void>\n}) {\n  return (",
        "function OverviewPanel({\n  activeConversation,\n  supportMemory,\n  onDone,\n}: {\n  activeConversation?: SupportConversation\n  supportMemory?: SupportMemoryLedger | null\n  onDone: () => Promise<void>\n}) {\n  const conversationState = activeConversation ? sourceConversationPresentation(activeConversation) : null\n  return (",
        "overview conversation state",
    )
    text = replace_once(
        text,
        "{activeConversation ? <Badge tone={toneForConversation(activeConversation)}>{stateLabel(activeConversation)}</Badge> : null}",
        "{conversationState ? <Badge tone={conversationState.tone}>{conversationState.label}</Badge> : null}",
        "overview source status badge",
    )

    channel_table_old = """          <div className=\"support-table\">
            <div className=\"support-table-row head\">
              <span>渠道</span>
              <span>账号</span>
              <span>状态</span>
              <span>优先级</span>
            </div>
            {activeAccounts.slice(0, 12).map((item: ChannelAccount) => (
              <div className=\"support-table-row\" key={item.id}>
                <span data-label=\"渠道\">{item.provider}</span>
                <span data-label=\"账号\">{sanitizeDisplayText(item.display_name || item.account_id)}</span>
                <span data-label=\"状态\"><Badge tone={toneForHealth(item.health_status)}>{item.health_status}</Badge></span>
                <span data-label=\"优先级\">{item.priority}</span>
              </div>
            ))}
            {!activeAccounts.length ? <EmptyState title=\"暂无渠道账号\" description=\"当前没有可展示的发送线路。\" /> : null}
          </div>
"""
    channel_table_new = """          <div className=\"support-table-wrap\">
            <table className=\"support-table\">
              <caption className=\"sr-only\">当前启用的渠道账号</caption>
              <thead>
                <tr>
                  <th scope=\"col\">渠道</th>
                  <th scope=\"col\">账号</th>
                  <th scope=\"col\">状态</th>
                  <th scope=\"col\">优先级</th>
                </tr>
              </thead>
              <tbody>
                {activeAccounts.slice(0, 12).map((item: ChannelAccount) => {
                  const health = healthPresentation(item.health_status)
                  return (
                    <tr key={item.id}>
                      <td data-label=\"渠道\">{sanitizeDisplayText(item.provider)}</td>
                      <td data-label=\"账号\">{sanitizeDisplayText(item.display_name || item.account_id)}</td>
                      <td data-label=\"状态\"><Badge tone={health.tone}>{sanitizeDisplayText(health.label)}</Badge></td>
                      <td data-label=\"优先级\">{item.priority}</td>
                    </tr>
                  )
                })}
                {!activeAccounts.length ? (
                  <tr><td colSpan={4}><EmptyState title=\"暂无渠道账号\" description=\"当前没有可展示的发送线路。\" /></td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
"""
    text = replace_once(text, channel_table_old, channel_table_new, "semantic channel table")

    whatsapp_badge_old = """          <Badge tone={toneForHealth(whatsappStatus.data?.status || whatsappAccount?.health_status)}>
            {whatsappStatus.data?.status || whatsappAccount?.health_status || 'unknown'}
          </Badge>
"""
    whatsapp_badge_new = """          {(() => {
            const health = healthPresentation(whatsappStatus.data?.status || whatsappAccount?.health_status)
            return <Badge tone={health.tone}>{sanitizeDisplayText(health.label)}</Badge>
          })()}
"""
    text = replace_once(text, whatsapp_badge_old, whatsapp_badge_new, "native status exact mapping")

    text = replace_once(
        text,
        "  const privateRuntime = runtime.data?.providers?.find((item) => item.name === 'private_ai_runtime')\n  const runtimeDiagnostics = privateRuntime?.diagnostics ?? {}\n  const latency = metrics.data?.runtime_latency\n",
        "  const privateRuntime = runtime.data?.providers?.find((item) => item.name === 'private_ai_runtime')\n"
        "  const runtimeDiagnostics = privateRuntime?.diagnostics ?? {}\n"
        "  const latency = metrics.data?.runtime_latency\n"
        "  const runtimeState = runtimePresentation({\n"
        "    isLoading: runtime.isLoading,\n"
        "    isError: runtime.isError,\n"
        "    ok: runtime.data?.ok,\n"
        "    warnings: runtime.data?.warnings,\n"
        "  })\n",
        "runtime fail-closed state",
    )
    runtime_badge_old = """          <Badge tone={(runtime.data?.warnings?.length ?? 0) ? 'warning' : 'success'}>
            {(runtime.data?.warnings?.length ?? 0) ? '需要关注' : '正常'}
          </Badge>
"""
    runtime_badge_new = """          <Badge tone={runtimeState.tone}>{runtimeState.label}</Badge>
"""
    text = replace_once(text, runtime_badge_old, runtime_badge_new, "runtime header truth")

    text = replace_once(
        text,
        "  const state = useQuery({\n    queryKey: ['supportConversationState'],\n    queryFn: () => supportApi.supportConversationState(),\n    refetchInterval: 10000,\n  })",
        "  const state = useQuery({\n    queryKey: ['supportConversationState'],\n    queryFn: () => supportApi.supportConversationState(),\n    enabled: activeView === 'conversations',\n    refetchInterval: activeView === 'conversations' ? 10000 : false,\n  })",
        "bounded state polling",
    )
    text = replace_once(
        text,
        '<Badge tone="success">{state.data?.my_handoffs ?? 0} 个我的接管</Badge>',
        '<Badge tone="default">{state.data?.my_handoffs ?? 0} 个我的接管</Badge>',
        "ownership is not business success",
    )
    text = replace_once(
        text,
        "<Badge tone={toneForChannel(activeConversation.channel)}>{activeConversation.channel === 'whatsapp' ? 'WhatsApp' : 'WebChat'}</Badge>",
        "<Badge tone={channelPresentation(activeConversation.channel).tone}>{channelPresentation(activeConversation.channel).label}</Badge>",
        "thread channel category",
    )
    text = replace_once(
        text,
        "<span>{stateLabel(activeConversation)}</span>",
        "<span>{sourceConversationPresentation(activeConversation).label}</span>",
        "thread source status",
    )

    forbidden = ["toneForHealth", "toneForChannel", "toneForConversation", "stateLabel(", "已结束"]
    for token in forbidden:
        if token in text:
            raise RuntimeError(f"forbidden legacy truth token remains: {token}")

    CONSOLE.write_text(text, encoding="utf-8")


def patch_css() -> None:
    text = CSS.read_text(encoding="utf-8")
    text = replace_once(text, "  min-height: 34px;\n  padding: 7px 12px;", "  min-height: 44px;\n  padding: 7px 12px;", "top tabs target")
    text = replace_once(text, "  min-height: 32px;\n  min-width: 0;", "  min-height: 44px;\n  min-width: 0;", "segments target")
    text = replace_once(text, "  min-height: 34px;\n  padding: 6px 10px;", "  min-height: 44px;\n  padding: 6px 10px;", "thread back target")
    text = replace_once(text, "  background: #f06423;\n  color: #ffffff;", "  background: #9a3412;\n  color: #ffffff;", "agent message contrast")
    text = replace_once(text, "    min-height: 32px;\n    padding: 5px 9px;", "    min-height: 44px;\n    padding: 5px 9px;", "mobile back target")
    text = replace_once(text, "    min-height: 32px;\n    padding: 6px 10px;", "    min-height: 44px;\n    padding: 6px 10px;", "mobile action target")
    text = replace_once(text, "    min-height: 42px;\n    padding: 8px 18px;", "    min-height: 44px;\n    padding: 8px 18px;", "mobile composer target")

    text += """

/* Operational truth and semantic table hardening (#641). */
.support-action-result.default {
  background: #f8fafc;
  border-color: #dbe3ef;
}

.support-action-result.danger {
  background: #fef2f2;
  border-color: #fecaca;
}

.support-action-result .technical-details {
  margin-top: 6px;
}

.support-action-result code {
  color: #475569;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.support-table-wrap {
  border: 1px solid #dbe3ef;
  border-radius: 8px;
  overflow: auto;
}

.support-table {
  border: 0;
  border-collapse: collapse;
  display: table;
  table-layout: fixed;
  width: 100%;
}

.support-table th,
.support-table td {
  border-bottom: 1px solid #dbe3ef;
  min-width: 0;
  overflow-wrap: anywhere;
  padding: 11px 12px;
  text-align: left;
  vertical-align: top;
}

.support-table th {
  background: #f8fafc;
  color: #64748b;
  font-size: 12px;
  font-weight: 800;
}

.support-table tr:last-child td {
  border-bottom: 0;
}

@media (max-width: 640px) {
  .support-table-wrap {
    border: 0;
    overflow: visible;
  }

  .support-table,
  .support-table tbody,
  .support-table tr,
  .support-table td {
    display: block;
    width: 100%;
  }

  .support-table thead {
    display: none;
  }

  .support-table tr {
    background: #ffffff;
    border: 1px solid #dbe3ef;
    border-radius: 8px;
    margin-bottom: 8px;
    padding: 10px;
  }

  .support-table td {
    border: 0;
    display: grid;
    gap: 4px;
    padding: 4px 0;
  }

  .support-table td::before {
    color: #64748b;
    content: attr(data-label);
    font-size: 12px;
    font-weight: 750;
  }
}
"""
    if "#f06423" in text.lower():
        raise RuntimeError("deprecated low-contrast orange remains")
    CSS.write_text(text, encoding="utf-8")


def patch_e2e() -> None:
    text = E2E.read_text(encoding="utf-8")
    text = replace_once(text, "        health_status: 'healthy',", "        health_status: 'offline',", "mock account health")
    text = replace_once(text, "      status: 'connected',", "      status: 'disconnected',", "mock native disconnected")
    text = replace_once(text, "      channel_health_status: 'healthy',", "      channel_health_status: 'offline',", "mock native channel offline")
    text = replace_once(
        text,
        "  await expect(page.getByText('connected')).toBeVisible()",
        "  const disconnected = page.getByText('disconnected', { exact: true })\n"
        "  await expect(disconnected).toBeVisible()\n"
        "  await expect(disconnected).toHaveClass(/danger/)\n"
        "  await expect(page.getByRole('table', { name: '当前启用的渠道账号' })).toBeVisible()",
        "browser disconnected truth and table semantics",
    )

    text += """

test('runtime failure never presents normal operation', async ({ page }) => {
  await mockAuthenticatedConsole(page)
  await page.route('**/api/admin/provider-runtime/status', (route) => route.fulfill({
    status: 503,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify({ detail: 'runtime unavailable' }),
  }))
  await page.goto('/webchat')
  await page.getByRole('button', { name: '运行' }).click()
  await expect(page.getByText('不可用', { exact: true })).toBeVisible()
  await expect(page.getByText('正常', { exact: true })).toHaveCount(0)
})

test('queued controlled action remains pending and hides technical id by default', async ({ page }) => {
  await mockAuthenticatedConsole(page)
  await page.route('**/api/tickets/11/speedaf/work-orders', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify({
      ok: true,
      status: 'queued',
      message: 'Speedaf work order queued.',
      jobId: 91,
      dedupeKey: 'bounded-test-key',
    }),
  }))
  await page.goto('/webchat')
  await page.getByLabel('运单').fill('WB123456')
  await page.getByLabel('Caller ID').fill('+41790000000')
  await page.getByLabel('说明').fill('Follow up delivery')
  await page.getByRole('button', { name: '创建工单' }).click()

  const result = page.locator('.support-action-result').filter({ hasText: '请求已排队' })
  await expect(result).toBeVisible()
  await expect(result).not.toHaveClass(/success/)
  await expect(page.getByText('Job #91')).not.toBeVisible()
  await result.getByText('技术详情').click()
  await expect(page.getByText('Job #91')).toBeVisible()
})

test('mobile navigation and segment controls meet the 44px target floor', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockAuthenticatedConsole(page)
  await page.goto('/webchat')

  const topTab = page.getByTestId('support-workbench-tabs').getByRole('button').first()
  const segment = page.locator('.support-segments').first().getByRole('button').first()
  expect((await topTab.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  expect((await segment.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)

  await page.getByRole('button', { name: /WebChat Visitor/ }).click()
  const back = page.getByRole('button', { name: '‹ 会话' })
  expect((await back.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
})
"""
    E2E.write_text(text, encoding="utf-8")


def main() -> None:
    patch_console()
    patch_css()
    patch_e2e()
    print("frontend truth codemod applied")


if __name__ == "__main__":
    main()
