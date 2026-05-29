import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Field'
import { useSession } from '@/hooks/useAuth'
import { CAPABILITIES, canAccess, routeAccess } from '@/lib/rbac'

const actions = [
  { id: 'overview', label: '查看今日总览', keywords: '首页 总览 今日 优先', to: '/' },
  { id: 'workspace', label: '处理工单 / 客户回复', keywords: '工单 回复 客户 闭环', to: '/workspace' },
  { id: 'webchat', label: '打开 WebChat 收件箱', keywords: 'webchat 网站聊天 收件箱 客户来信', to: '/webchat' },
  { id: 'webcall-workbench', label: '打开 WebCall 工作台', keywords: 'webcall 语音 来电 接管 ai 建议 handoff', to: '/webcall', access: routeAccess['/webcall'] },
  { id: 'email-workbench', label: '打开 Email 工作台', keywords: 'email 邮件 工作台 草稿 回复 发送', to: '/email', access: routeAccess['/email'] },
  { id: 'runtime', label: '进入运行恢复 / dead 重排', keywords: 'runtime 运行恢复 dead requeue 重排 队列', to: '/runtime', access: routeAccess['/runtime'] },
  { id: 'accounts', label: '检查发送线路', keywords: '发送线路 渠道 账号 outbound', to: '/accounts', access: routeAccess['/accounts'] },
  { id: 'outbound-email', label: '维护 Outbound Email 账号', keywords: 'email smtp 邮件 账号 test-send 测试发送 outbound', to: '/outbound-email', access: routeAccess['/outbound-email'] },
  { id: 'bulletins', label: '查看公告口径', keywords: '公告 口径 通知', to: '/bulletins' },
  { id: 'speedaf-action-center', label: '打开 Speedaf 动作中心', keywords: 'speedaf 催派 地址 更新 取消 运单', to: '/speedaf', access: routeAccess['/speedaf'] },
  { id: 'refresh', label: '刷新全部数据', keywords: '刷新 reload invalidate', action: 'refresh' },
  { id: 'runtime-refresh', label: '刷新运行状态', keywords: '刷新 runtime 运行 状态', action: 'runtime-refresh', access: routeAccess['/runtime'] },
  { id: 'new-bulletin', label: '新建公告', keywords: '公告 新建 口径', to: '/bulletins', access: { allOf: [CAPABILITIES.bulletinManage] } },
  { id: 'new-account', label: '新建渠道账号', keywords: '渠道 账号 新建', to: '/accounts', access: routeAccess['/accounts'] },
  { id: 'new-email-account', label: '新建 SMTP 账号', keywords: 'smtp email 邮件 新建 密码 轮换', to: '/outbound-email', access: routeAccess['/outbound-email'] },
]

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState('')
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const session = useSession()
  const visibleActions = useMemo(() => actions.filter((item) => {
    if ('access' in item && item.access) return canAccess(session.data, item.access)
    return true
  }), [session.data])
  const normalizedQuery = query.trim().toLowerCase()
  const filtered = useMemo(() => visibleActions.filter((item) => {
    if (!normalizedQuery) return true
    return `${item.label} ${item.keywords || ''}`.toLowerCase().includes(normalizedQuery)
  }), [visibleActions, normalizedQuery])

  if (!open) return null

  return (
    <div className="command-backdrop" onClick={onClose}>
      <div className="command-card" onClick={(e) => e.stopPropagation()}>
        <div className="command-head">快捷操作</div>
        <Input autoFocus placeholder="输入关键词，例如：工单、WebChat、dead、重排、公告" value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="command-list" data-testid="operator-command-palette-actions">
          {filtered.map((item) => (
            <button
              key={item.id}
              className="command-item"
              onClick={async () => {
                if (item.action === 'refresh') {
                  await queryClient.invalidateQueries()
                  onClose()
                  return
                }
                if (item.action === 'runtime-refresh') {
                  await Promise.all([
                    queryClient.invalidateQueries({ queryKey: ['runtimeHealth'] }),
                    queryClient.invalidateQueries({ queryKey: ['queueSummary'] }),
                    queryClient.invalidateQueries({ queryKey: ['jobs'] }),
                    queryClient.invalidateQueries({ queryKey: ['openclawConnectivity'] }),
                  ])
                  navigate({ to: '/runtime' })
                  onClose()
                  return
                }
                if (item.to) navigate({ to: item.to })
                onClose()
              }}
            >
              {item.label}
            </button>
          ))}
          {!filtered.length ? <div className="empty">没有匹配的操作。</div> : null}
        </div>
        <div className="command-foot">
          <Button onClick={onClose}>关闭</Button>
        </div>
      </div>
    </div>
  )
}
