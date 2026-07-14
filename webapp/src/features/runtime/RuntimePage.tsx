import { lazy, Suspense, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { TechnicalDetails } from '@/components/ui/TechnicalDetails'
import { sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import { runtimePresentation } from '@/lib/supportStatus'
import '@/features/admin-routes/admin-routes.css'

const LazyAiDebugConsole = lazy(async () => {
  const module = await import('@/features/support-console/AiDebugConsolePage')
  return { default: module.AiDebugConsolePage }
})

function compactLatency(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '暂无'
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}s`
  return `${Math.max(0, Math.round(value))}ms`
}

function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

export function RuntimePage() {
  const [showEvidenceConsole, setShowEvidenceConsole] = useState(false)
  const runtime = useQuery({
    queryKey: ['canonicalProviderRuntimeStatus'],
    queryFn: supportApi.providerRuntimeStatus,
    refetchInterval: 15_000,
    retry: false,
  })
  const metrics = useQuery({
    queryKey: ['canonicalConversationMetrics'],
    queryFn: () => supportApi.supportConversationMetrics(24),
    refetchInterval: 15_000,
    retry: false,
  })
  const state = runtimePresentation({
    isLoading: runtime.isLoading,
    isError: runtime.isError,
    ok: runtime.data?.ok,
    warnings: runtime.data?.warnings,
  })
  const selectedProvider = runtime.data?.providers?.find((item) => item.selected)
  const latency = metrics.data?.runtime_latency

  return (
    <main className="nd-admin-page">
      <header className="nd-admin-page__header">
        <div>
          <h1>运行与审计</h1>
          <p>查看客服自动化服务是否可用、是否处于降级状态，以及最近 24 小时的工作负载。技术实现信息默认收起。</p>
        </div>
        <div className="nd-admin-stack">
          <Badge tone={state.tone}>{state.label}</Badge>
          <Button variant="secondary" onClick={() => setShowEvidenceConsole((current) => !current)}>
            {showEvidenceConsole ? '返回运行概览' : '打开 AI 证据审计'}
          </Button>
        </div>
      </header>

      {showEvidenceConsole ? (
        <section aria-label="AI 证据审计">
          <Suspense fallback={<EmptyState title="正在加载证据审计" description="正在读取 AI turn、工具调用、知识命中和 Safety 证据。" />}>
            <LazyAiDebugConsole />
          </Suspense>
        </section>
      ) : (
        <div className="nd-admin-grid">
          <section className="nd-admin-panel" aria-labelledby="service-readiness-title">
            <div className="nd-admin-panel__head">
              <h2 id="service-readiness-title">服务就绪状态</h2>
              {runtime.isFetching ? <Badge>正在检查</Badge> : null}
            </div>
            <div className="nd-admin-panel__body nd-admin-stack">
              {runtime.isError ? (
                <ErrorSummary title="无法读取运行状态" errors={[errorCopy(runtime.error, '请稍后重试')]} />
              ) : runtime.isLoading ? (
                <EmptyState title="正在检查服务" description="正在读取当前运行状态和降级路径。" />
              ) : (
                <>
                  <dl className="nd-admin-facts">
                    <div><dt>当前状态</dt><dd>{state.label}</dd></div>
                    <div><dt>服务模式</dt><dd>{runtime.data?.webchat_runtime_enabled ? '自动处理已启用' : '仅人工处理或未启用'}</dd></div>
                    <div><dt>降级路径</dt><dd>{runtime.data?.fallback_provider ? '已配置备用路径' : '无备用路径'}</dd></div>
                    <div><dt>配置检查</dt><dd>{selectedProvider?.configured ? '已配置' : '需要检查'}</dd></div>
                  </dl>

                  {runtime.data?.warnings?.length ? (
                    <div className="nd-admin-warning-list" aria-label="运行提醒">
                      {runtime.data.warnings.map((item) => <div key={String(item)}>{sanitizeDisplayText(String(item))}</div>)}
                    </div>
                  ) : (
                    <p className="nd-admin-muted">当前没有运行提醒。</p>
                  )}

                  <TechnicalDetails title="技术运行详情" summary="仅供运行与审计人员查看">
                    <dl>
                      <div><dt>状态代码</dt><dd><code>{sanitizeDisplayText(runtime.data?.status || 'unknown')}</code></dd></div>
                      <div><dt>当前 Provider</dt><dd><code>{sanitizeDisplayText(runtime.data?.configured_provider || selectedProvider?.name || '未配置')}</code></dd></div>
                      <div><dt>备用 Provider</dt><dd><code>{sanitizeDisplayText(runtime.data?.fallback_provider || '无')}</code></dd></div>
                      <div><dt>运行环境</dt><dd><code>{sanitizeDisplayText(runtime.data?.app_env || 'unknown')}</code></dd></div>
                      <div><dt>Provider 诊断</dt><dd><pre>{JSON.stringify(selectedProvider?.diagnostics || {}, null, 2)}</pre></dd></div>
                      <div><dt>安全边界</dt><dd><pre>{JSON.stringify(runtime.data?.boundary || {}, null, 2)}</pre></dd></div>
                    </dl>
                  </TechnicalDetails>
                </>
              )}
            </div>
          </section>

          <aside className="nd-admin-panel" aria-labelledby="runtime-workload-title">
            <div className="nd-admin-panel__head">
              <h2 id="runtime-workload-title">最近 24 小时</h2>
              {metrics.isFetching ? <Badge>正在刷新</Badge> : null}
            </div>
            <div className="nd-admin-panel__body nd-admin-stack">
              {metrics.isError ? (
                <ErrorSummary title="无法读取工作负载" errors={[errorCopy(metrics.error, '请稍后重试')]} />
              ) : (
                <>
                  <dl className="nd-admin-facts">
                    <div><dt>会话总量</dt><dd>{metrics.data?.total ?? 0}</dd></div>
                    <div><dt>等待人工</dt><dd>{metrics.data?.needs_human ?? 0}</dd></div>
                    <div><dt>自动处理中</dt><dd>{metrics.data?.ai_active ?? 0}</dd></div>
                    <div><dt>WhatsApp</dt><dd>{metrics.data?.by_channel?.whatsapp ?? 0}</dd></div>
                  </dl>
                  {latency ? (
                    <TechnicalDetails title="延迟指标" summary="默认收起">
                      <dl>
                        <div><dt>样本数</dt><dd>{latency.sample_count}</dd></div>
                        <div><dt>端到端 p50 / p90</dt><dd>{compactLatency(latency.total_turn.p50_ms)} / {compactLatency(latency.total_turn.p90_ms)}</dd></div>
                        <div><dt>运行处理 p50 / p90</dt><dd>{compactLatency(latency.runtime_total.p50_ms)} / {compactLatency(latency.runtime_total.p90_ms)}</dd></div>
                        <div><dt>生成 p50 / p90</dt><dd>{compactLatency(latency.runtime_eval.p50_ms)} / {compactLatency(latency.runtime_eval.p90_ms)}</dd></div>
                        <div><dt>冷加载</dt><dd>{latency.cold_load_count}</dd></div>
                        <div><dt>慢输入处理</dt><dd>{latency.slow_prompt_eval_count}</dd></div>
                      </dl>
                    </TechnicalDetails>
                  ) : null}
                </>
              )}
            </div>
          </aside>
        </div>
      )}
    </main>
  )
}
