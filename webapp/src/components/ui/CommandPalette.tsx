import { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Field'
import { useSession } from '@/hooks/useAuth'
import { canEditBulletins, canManageChannels, canViewOps } from '@/lib/access'

const actions = [
  { id: 'overview', label: '前往首页总览', to: '/' },
  { id: 'workspace', label: '前往工单处理', to: '/workspace' },
  { id: 'bulletins', label: '前往通知公告', to: '/bulletins' },
  { id: 'accounts', label: '前往发送线路', to: '/accounts', permission: 'channels' },
  { id: 'runtime', label: '前往运营保障', to: '/runtime', permission: 'ops' },
  { id: 'refresh', label: '刷新全部数据', action: 'refresh' },
  { id: 'new-bulletin', label: '新建公告', to: '/bulletins', permission: 'bulletinsManage' },
  { id: 'new-account', label: '新建渠道账号', to: '/accounts', permission: 'channels' },
]

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState('')
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const session = useSession()
  const visibleActions = useMemo(() => actions.filter((item) => {
    if (item.permission === 'ops') return canViewOps(session.data)
    if (item.permission === 'channels') return canManageChannels(session.data)
    if (item.permission === 'bulletinsManage') return canEditBulletins(session.data)
    return true
  }), [session.data])
  const filtered = useMemo(() => visibleActions.filter((item) => item.label.toLowerCase().includes(query.toLowerCase())), [visibleActions, query])

  if (!open) return null

  return (
    <div className="command-backdrop" onClick={onClose}>
      <div className="command-card" onClick={(e) => e.stopPropagation()}>
        <div className="command-head">快捷操作</div>
        <Input autoFocus placeholder="输入关键词，例如：工单、公告、刷新" value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="command-list">
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
