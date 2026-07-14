import { useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { ServiceAppShell } from '@/components/layout/ServiceAppShell'
import { useLogout, useSession } from '@/hooks/useAuth'
import { supportApi } from '@/lib/supportApi'

function stateTone(ok: boolean, status: string) {
  if (ok && ['ready', 'healthy', 'ok', 'active'].includes(status.toLowerCase())) return 'success' as const
  if (!ok || ['failed', 'error', 'unavailable'].includes(status.toLowerCase())) return 'danger' as const
  return 'warning' as const
}

export function SystemPage() {
  const session = useSession()
  const logout = useLogout()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const canRead = capabilities.has('runtime.manage')
  const status = useQuery({
    queryKey: ['serviceSystemStatus'],
    queryFn: supportApi.providerRuntimeStatus,
    enabled: Boolean(session.data && canRead),
    retry: false,
    refetchInterval: 30000,
  })

  useEffect(() => { document.title = '系统保障 · Nexus 客服中心' }, [])

  if (!session.data && session.isLoading) return <main className="service-entry-state"><EmptyState title="正在验证账号" description="正在加载系统保障权限。" /></main>
  if (!session.data || session.isError) return <main className="service-entry-state"><ErrorSummary title="无法读取当前账号" errors={['请重新登录']} /></main>

  const data = status.data
  const configuredNodes = data?.providers.filter((item) => item.configured).length ?? 0
  const enabledNodes = data?.providers.filter((item) => item.feature_enabled).length ?? 0
  const selectedNodes = data?.providers.filter((item) => item.selected).length ?? 0
  const tone = stateTone(Boolean(data?.ok), data?.status || 'unknown')

  return (
    <ServiceAppShell
      active="system"
      userName={session.data.display_name || session.data.username}
      capabilities={capabilities}
      title="系统保障"
      description="确认客户回复、知识查询和后台处理服务是否可用。客服只需要看到是否正常以及该怎么处理异常。"
      meta={<Badge tone={tone}>{data?.ok ? '服务可用' : '需要检查'}</Badge>}
      onLogout={logout}
    >
      <div className="system-page">
        {!canRead ? <EmptyState title="当前账号不能查看系统保障" description="请联系管理员补充系统保障权限。" /> : null}
        {canRead ? (
          <>
            <div className="workspace-section-heading">
              <div><h2>服务状态</h2><p>此页面只显示客服需要理解的运行结果，不展示内部技术参数。</p></div>
              <Button variant="secondary" loading={status.isFetching} loadingLabel="检查中…" onClick={() => status.refetch()}>重新检查</Button>
            </div>
            {status.isError ? <ErrorSummary title="系统状态暂不可用" errors={[status.error instanceof Error ? status.error.message : '请稍后重试']} /> : null}
            {data ? (
              <div className="system-page-grid">
                <article className="system-card">
                  <Badge tone={tone}>总体状态</Badge>
                  <h2>{data.ok ? '客户服务可以正常使用' : '部分客户服务需要检查'}</h2>
                  <p>{data.ok ? '客服可以继续处理案例和回复客户。' : '请避免重复提交操作，并通知系统管理员。'}</p>
                </article>
                <article className="system-card">
                  <Badge tone={configuredNodes ? 'success' : 'danger'}>服务配置</Badge>
                  <h2>{configuredNodes} 个已配置节点</h2>
                  <p>{enabledNodes} 个已启用，{selectedNodes} 个当前承担服务。</p>
                </article>
                <article className="system-card">
                  <Badge tone={data.warnings.length ? 'warning' : 'success'}>检查提示</Badge>
                  <h2>{data.warnings.length ? `${data.warnings.length} 条提示待处理` : '没有待处理提示'}</h2>
                  <p>{data.warnings.length ? '详细技术原因仅由工程人员查看，客服按异常处理流程上报。' : '当前检查没有发现需要客服关注的问题。'}</p>
                </article>
                <article className="system-card">
                  <Badge tone={data.webchat_runtime_enabled ? 'success' : 'warning'}>自动接待</Badge>
                  <h2>{data.webchat_runtime_enabled ? '自动接待可用' : '自动接待未启用'}</h2>
                  <p>自动接待不可用时，客户案例仍应进入人工待办。</p>
                </article>
                <article className="system-card">
                  <Badge tone={data.boundary?.customer_message_sent ? 'warning' : 'success'}>检查边界</Badge>
                  <h2>{data.boundary?.customer_message_sent ? '检查期间产生了客户消息' : '检查未发送客户消息'}</h2>
                  <p>状态检查不应主动联系客户或修改客户案例。</p>
                </article>
                <article className="system-card">
                  <Badge tone={data.boundary?.secret_values_exposed ? 'danger' : 'success'}>信息保护</Badge>
                  <h2>{data.boundary?.secret_values_exposed ? '发现敏感信息暴露风险' : '未显示敏感配置'}</h2>
                  <p>客服界面不展示密钥、内部地址或技术凭据。</p>
                </article>
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </ServiceAppShell>
  )
}
