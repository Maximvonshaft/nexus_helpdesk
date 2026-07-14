import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/Badge'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { TechnicalDetails } from '@/components/ui/TechnicalDetails'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import { healthPresentation } from '@/lib/supportStatus'
import type { ChannelAccount } from '@/lib/types'
import '@/features/admin-routes/admin-routes.css'

function providerLabel(value: string) {
  if (value === 'webchat') return '网页客服'
  if (value === 'whatsapp') return 'WhatsApp'
  if (value === 'email') return '邮件'
  if (value === 'voice') return '语音'
  return sanitizeDisplayText(value)
}

function maskPhone(value: string | null | undefined) {
  const text = String(value || '').trim()
  if (!text) return '未返回'
  const digits = text.replace(/\D/g, '')
  return digits.length > 4 ? `•••• ${digits.slice(-4)}` : '已配置'
}

function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

export function ChannelsPage() {
  const accounts = useQuery({
    queryKey: ['canonicalChannelAccounts'],
    queryFn: supportApi.channelAccounts,
    refetchInterval: 30_000,
    retry: false,
  })
  const activeAccounts = useMemo(
    () => (accounts.data ?? []).filter((item: ChannelAccount) => item.is_active),
    [accounts.data],
  )
  const whatsappAccount = useMemo(
    () => activeAccounts.find((item: ChannelAccount) => item.provider === 'whatsapp'),
    [activeAccounts],
  )
  const whatsappStatus = useQuery({
    queryKey: ['canonicalWhatsappStatus', whatsappAccount?.account_id],
    queryFn: () => supportApi.whatsappNativeStatus(whatsappAccount?.account_id || ''),
    enabled: Boolean(whatsappAccount?.account_id),
    refetchInterval: 10_000,
    retry: false,
  })
  const whatsappHealth = healthPresentation(
    whatsappStatus.data?.channel_health_status
      || whatsappStatus.data?.status
      || whatsappAccount?.health_status,
  )

  return (
    <main className="nd-admin-page">
      <header className="nd-admin-page__header">
        <div>
          <h1>渠道管理</h1>
          <p>查看已启用的客户沟通渠道和连接状态。渠道权限不代表案例访问权限。</p>
        </div>
        {accounts.isFetching ? <Badge>正在刷新</Badge> : null}
      </header>

      <div className="nd-admin-grid">
        <section className="nd-admin-panel" aria-labelledby="channel-accounts-title">
          <div className="nd-admin-panel__head">
            <h2 id="channel-accounts-title">已启用渠道</h2>
            <Badge>{activeAccounts.length} 个账号</Badge>
          </div>
          <div className="nd-admin-panel__body">
            {accounts.isError ? (
              <ErrorSummary title="无法读取渠道账号" errors={[errorCopy(accounts.error, '请稍后重试')]} />
            ) : activeAccounts.length ? (
              <div className="nd-admin-table-wrap">
                <table className="nd-admin-table">
                  <caption className="sr-only">当前启用的渠道账号</caption>
                  <thead>
                    <tr>
                      <th scope="col">渠道</th>
                      <th scope="col">显示名称</th>
                      <th scope="col">运行状态</th>
                      <th scope="col">最近更新</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeAccounts.map((item) => {
                      const health = healthPresentation(item.health_status)
                      return (
                        <tr key={item.id}>
                          <td data-label="渠道">{providerLabel(item.provider)}</td>
                          <td data-label="显示名称">{sanitizeDisplayText(item.display_name || `${providerLabel(item.provider)} 账号`)}</td>
                          <td data-label="运行状态"><Badge tone={health.tone}>{health.label}</Badge></td>
                          <td data-label="最近更新">{formatDateTime(item.updated_at)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="暂无已启用渠道" description="请联系渠道管理员完成账号配置和启用。" />
            )}
          </div>
        </section>

        <aside className="nd-admin-panel" aria-labelledby="whatsapp-health-title">
          <div className="nd-admin-panel__head">
            <h2 id="whatsapp-health-title">WhatsApp 连接</h2>
            <Badge tone={whatsappHealth.tone}>{whatsappHealth.label}</Badge>
          </div>
          <div className="nd-admin-panel__body nd-admin-stack">
            {!whatsappAccount ? (
              <EmptyState title="未启用 WhatsApp" description="当前没有启用的 WhatsApp 渠道账号。" />
            ) : whatsappStatus.isError ? (
              <ErrorSummary title="无法读取 WhatsApp 状态" errors={[errorCopy(whatsappStatus.error, '请稍后重试')]} />
            ) : (
              <>
                <dl className="nd-admin-facts">
                  <div><dt>连接状态</dt><dd>{whatsappHealth.label}</dd></div>
                  <div><dt>绑定号码</dt><dd>{maskPhone(whatsappStatus.data?.phone_number)}</dd></div>
                  <div><dt>登录确认</dt><dd>{sanitizeDisplayText(whatsappStatus.data?.qr_status || '状态未知')}</dd></div>
                  <div><dt>重连次数</dt><dd>{whatsappStatus.data?.reconnect_count ?? 0}</dd></div>
                </dl>
                {whatsappStatus.data?.last_error_message ? (
                  <ErrorSummary
                    title="最近一次连接异常"
                    errors={[sanitizeDisplayText(whatsappStatus.data.last_error_message)]}
                  />
                ) : null}
                <TechnicalDetails title="渠道技术详情" summary="默认收起">
                  <dl>
                    <div><dt>Provider</dt><dd><code>{sanitizeDisplayText(whatsappAccount.provider)}</code></dd></div>
                    <div><dt>账号标识</dt><dd><code>{sanitizeDisplayText(whatsappAccount.account_id)}</code></dd></div>
                    <div><dt>连接记录</dt><dd>{whatsappStatus.data?.last_connected_at ? formatDateTime(whatsappStatus.data.last_connected_at) : '暂无'}</dd></div>
                    <div><dt>错误代码</dt><dd>{sanitizeDisplayText(whatsappStatus.data?.last_error_code || '无')}</dd></div>
                  </dl>
                </TechnicalDetails>
              </>
            )}
          </div>
        </aside>
      </div>
    </main>
  )
}
