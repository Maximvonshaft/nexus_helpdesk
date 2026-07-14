import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { ServiceAppShell } from '@/components/layout/ServiceAppShell'
import { useLogout, useSession } from '@/hooks/useAuth'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import type { ChannelAccount } from '@/lib/types'

function healthTone(status: string) {
  const normalized = status.toLowerCase()
  if (['healthy', 'ready', 'connected', 'ok', 'active'].includes(normalized)) return 'success' as const
  if (['failed', 'error', 'disconnected', 'dead'].includes(normalized)) return 'danger' as const
  return 'warning' as const
}

function providerLabel(provider: string) {
  const normalized = provider.toLowerCase()
  if (normalized === 'whatsapp') return 'WhatsApp'
  if (normalized === 'webchat') return '网页客服'
  if (normalized === 'email') return '邮件'
  return sanitizeDisplayText(provider)
}

export function ChannelsPage() {
  const session = useSession()
  const logout = useLogout()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const canManage = capabilities.has('channel_account.manage')
  const [selected, setSelected] = useState<ChannelAccount | null>(null)

  useEffect(() => { document.title = '渠道状态 · Nexus 客服中心' }, [])

  const accounts = useQuery({
    queryKey: ['serviceChannels'],
    queryFn: supportApi.channelAccounts,
    enabled: Boolean(session.data && canManage),
    retry: false,
    refetchInterval: 30000,
  })
  const activeAccounts = (accounts.data ?? []).filter((item) => item.is_active)

  useEffect(() => {
    if (!selected && activeAccounts.length) setSelected(activeAccounts[0])
  }, [activeAccounts, selected])

  const whatsappStatus = useQuery({
    queryKey: ['serviceChannelStatus', selected?.account_id],
    queryFn: () => supportApi.whatsappNativeStatus(selected?.account_id || ''),
    enabled: Boolean(selected?.provider === 'whatsapp' && selected.account_id),
    retry: false,
    refetchInterval: 15000,
  })

  if (!session.data && session.isLoading) return <main className="service-entry-state"><EmptyState title="正在验证账号" description="正在加载渠道权限。" /></main>
  if (!session.data || session.isError) return <main className="service-entry-state"><ErrorSummary title="无法读取当前账号" errors={['请重新登录']} /></main>

  return (
    <ServiceAppShell
      active="channels"
      userName={session.data.display_name || session.data.username}
      capabilities={capabilities}
      title="渠道状态"
      description="确认客户能否通过网页、WhatsApp 或邮件正常联系，并及时处理断连和异常。"
      meta={<span>{activeAccounts.length} 个启用渠道</span>}
      onLogout={logout}
    >
      <div className="system-page">
        {!canManage ? <EmptyState title="当前账号不能查看渠道状态" description="请联系管理员补充渠道管理权限。" /> : null}
        {canManage ? (
          <div className="channel-layout">
            <section className="channel-list-panel">
              <div className="workspace-section-heading">
                <div><h2>客户联系渠道</h2><p>只显示当前启用的渠道账号。</p></div>
                <Button variant="secondary" loading={accounts.isFetching} loadingLabel="刷新中…" onClick={() => accounts.refetch()}>刷新</Button>
              </div>
              {accounts.isError ? <ErrorSummary title="渠道列表不可用" errors={[accounts.error instanceof Error ? accounts.error.message : '请稍后重试']} /> : null}
              <div className="channel-cards">
                {activeAccounts.map((account) => (
                  <button key={account.id} type="button" className={selected?.id === account.id ? 'is-selected' : ''} onClick={() => setSelected(account)}>
                    <span>
                      <strong>{sanitizeDisplayText(account.display_name || account.account_id)}</strong>
                      <Badge tone={healthTone(account.health_status)}>{providerLabel(account.provider)}</Badge>
                    </span>
                    <small>{sanitizeDisplayText(account.health_status || '状态未知')} · 优先级 {account.priority}</small>
                  </button>
                ))}
                {!accounts.isLoading && !activeAccounts.length ? <EmptyState title="没有启用的渠道" description="客户当前可能无法通过已配置渠道联系。" /> : null}
              </div>
            </section>

            <section className="channel-detail-panel">
              {selected ? (
                <>
                  <div className="workspace-section-heading">
                    <div><h2>{sanitizeDisplayText(selected.display_name || selected.account_id)}</h2><p>{providerLabel(selected.provider)} 客户联系状态</p></div>
                    <Badge tone={healthTone(selected.health_status)}>{sanitizeDisplayText(selected.health_status || '状态未知')}</Badge>
                  </div>
                  <dl className="channel-detail-grid">
                    <div><dt>渠道</dt><dd>{providerLabel(selected.provider)}</dd></div>
                    <div><dt>账号标识</dt><dd>{sanitizeDisplayText(selected.account_id)}</dd></div>
                    <div><dt>当前状态</dt><dd>{sanitizeDisplayText(selected.health_status || '状态未知')}</dd></div>
                    <div><dt>最后更新</dt><dd>{formatDateTime(selected.updated_at)}</dd></div>
                    <div><dt>优先级</dt><dd>{selected.priority}</dd></div>
                    <div><dt>备用账号</dt><dd>{sanitizeDisplayText(selected.fallback_account_id || '未配置')}</dd></div>
                  </dl>

                  {selected.provider === 'whatsapp' ? (
                    <section className="channel-live-status">
                      <div className="workspace-section-heading compact"><div><h3>连接状态</h3><p>实时检查 WhatsApp 是否已连接。</p></div></div>
                      {whatsappStatus.isError ? <ErrorSummary title="无法读取连接状态" errors={[whatsappStatus.error instanceof Error ? whatsappStatus.error.message : '请稍后重试']} /> : null}
                      {whatsappStatus.data ? (
                        <dl className="channel-detail-grid">
                          <div><dt>连接</dt><dd>{sanitizeDisplayText(whatsappStatus.data.status)}</dd></div>
                          <div><dt>二维码</dt><dd>{sanitizeDisplayText(whatsappStatus.data.qr_status)}</dd></div>
                          <div><dt>号码</dt><dd>{sanitizeDisplayText(whatsappStatus.data.phone_number || '未识别')}</dd></div>
                          <div><dt>重连次数</dt><dd>{whatsappStatus.data.reconnect_count}</dd></div>
                          <div><dt>上次连接</dt><dd>{whatsappStatus.data.last_connected_at ? formatDateTime(whatsappStatus.data.last_connected_at) : '暂无'}</dd></div>
                          <div><dt>最近错误</dt><dd>{sanitizeDisplayText(whatsappStatus.data.last_error_message || '无')}</dd></div>
                        </dl>
                      ) : null}
                    </section>
                  ) : null}
                </>
              ) : <EmptyState title="选择一个渠道" description="查看客户联系渠道的当前状态。" />}
            </section>
          </div>
        ) : null}
      </div>
    </ServiceAppShell>
  )
}
