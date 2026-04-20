import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { AuthUser } from '@/lib/types'
import { labelize, sanitizeDisplayText } from '@/lib/format'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Field, Input, Select } from '@/components/ui/Field'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/ui/PageHeader'
import { Toast } from '@/components/ui/Toast'
import { MetricCard } from '@/components/ui/MetricCard'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useSession } from '@/hooks/useAuth'
import { canManageUsers } from '@/lib/access'

type UserForm = {
  username: string
  password: string
  display_name: string
  email: string
  role: string
  team_id: string
  capabilities: string[]
}

function emptyForm(): UserForm {
  return {
    username: '',
    password: '',
    display_name: '',
    email: '',
    role: 'agent',
    team_id: '',
    capabilities: [],
  }
}

function UsersPage() {
  const client = useQueryClient()
  const autoRefresh = useAutoRefresh(true)
  const session = useSession()
  const navigate = useNavigate()
  const permitted = canManageUsers(session.data)

  const usersQuery = useQuery({ queryKey: ['users'], queryFn: api.users, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: permitted })
  const teamsQuery = useQuery({ queryKey: ['teams'], queryFn: api.teams, enabled: permitted })
  const catalogQuery = useQuery({ queryKey: ['catalog'], queryFn: api.capabilityCatalog, enabled: permitted })

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [roleFilter, setRoleFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [form, setForm] = useState<UserForm>(emptyForm())
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [session.data, permitted, navigate])

  const allUsers = usersQuery.data ?? []

  const filteredUsers = useMemo(() => {
    return allUsers.filter((user) => {
      const roleOk = roleFilter === 'all' || user.role === roleFilter
      const keyword = search.trim().toLowerCase()
      const searchOk = !keyword || [user.display_name, user.username, user.email || ''].some((value) => String(value || '').toLowerCase().includes(keyword))
      return roleOk && searchOk
    })
  }, [allUsers, roleFilter, search])

  const selectedUser = useMemo(() => allUsers.find((user) => user.id === selectedId) ?? null, [allUsers, selectedId])
  const selectedTeam = useMemo(() => (teamsQuery.data ?? []).find((team: any) => team.id === selectedUser?.team_id) ?? null, [teamsQuery.data, selectedUser?.team_id])

  useEffect(() => {
    if (selectedId && !selectedUser) {
      setSelectedId(null)
    }
  }, [selectedId, selectedUser])

  const createUserMutation = useMutation({
    mutationFn: async () => {
      const payload = {
        username: form.username.trim(),
        password: form.password,
        display_name: form.display_name.trim(),
        email: form.email.trim() || null,
        role: form.role,
        team_id: form.team_id ? Number(form.team_id) : null,
        capabilities: form.capabilities,
      }
      return api.createUser(payload)
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['users'] })
      setToast({ message: '账号开通成功', tone: 'success' })
      setForm(emptyForm())
    },
    onError: (err) => setToast({ message: String(err), tone: 'danger' }),
  })

  const roleOptions = [
    { label: '全部角色', value: 'all' },
    { label: 'Agent', value: 'agent' },
    { label: 'Lead', value: 'lead' },
    { label: 'Manager', value: 'manager' },
    { label: 'Auditor', value: 'auditor' },
    { label: 'Admin', value: 'admin' },
  ]

  if (!permitted) {
    return (
      <AppShell>
        <Card>
          <CardHeader title="权限不足" subtitle="只有被授权的管理员才能访问账号管理。" />
          <CardBody>
            <div className="message" data-role="agent">请联系管理员授予账号管理权限，或返回工单工作台继续处理当前业务。</div>
          </CardBody>
        </Card>
      </AppShell>
    )
  }

  return (
    <AppShell>
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
      <PageHeader
        eyebrow="账号管理"
        title="员工账号管理"
        description="把“已有账号详情”和“新增账号”拆开处理，避免把只读信息伪装成可编辑表单。当前页面重点支持：查看现有账号、搜索筛选、开通新账号。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button variant="primary" onClick={() => { setSelectedId(null); setForm(emptyForm()) }}>新增账号</Button></div>}
      />

      <div className="metrics-grid">
        <MetricCard label="账号总数" value={allUsers.length} hint="当前可见的启用账号" />
        <MetricCard label="筛选结果" value={filteredUsers.length} hint="按角色和关键字筛出来的账号" />
        <MetricCard label="管理员" value={allUsers.filter((user) => user.role === 'admin').length} hint="拥有系统级配置权限" />
        <MetricCard label="主管/经理" value={allUsers.filter((user) => ['lead', 'manager'].includes(user.role)).length} hint="负责分配、升级和现场管理" />
      </div>

      <div className="page-grid split-grid-wide">
        <Card>
          <CardHeader title="账号列表" subtitle="当前列表展示已启用账号。先筛选、再点开详情，避免误把详情页当成编辑页。" />
          <CardBody>
            <div className="stack">
              <Field label="搜索账号">
                <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="按显示名、用户名、邮箱搜索" />
              </Field>
              <Field label="角色筛选">
                <Select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
                  {roleOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </Select>
              </Field>
              <div className="list">
                {usersQuery.isLoading ? <div className="empty">账号列表加载中…</div> : null}
                {!usersQuery.isLoading && !filteredUsers.length ? <EmptyState text="没有匹配到账号。" /> : null}
                {filteredUsers.map((user) => (
                  <button
                    key={user.id}
                    className={`queue-card ${selectedId === user.id ? 'selected' : ''}`}
                    onClick={() => setSelectedId(user.id)}
                  >
                    <div className="badges">
                      <Badge tone={user.role === 'admin' ? 'danger' : user.role === 'manager' ? 'warning' : 'default'}>{labelize(user.role)}</Badge>
                    </div>
                    <div className="queue-card-title">{sanitizeDisplayText(user.display_name)}</div>
                    <div className="queue-card-meta">@{sanitizeDisplayText(user.username)}{user.email ? ` · ${sanitizeDisplayText(user.email)}` : ''}</div>
                  </button>
                ))}
              </div>
            </div>
          </CardBody>
        </Card>

        <div className="stack">
          <Card>
            <CardHeader
              title={selectedUser ? '账号详情' : '新增账号'}
              subtitle={selectedUser ? '当前版本把已有账号以详情模式展示，避免出现“看起来能改、实际上不能改”的误导。' : '仅把真正有效的开通动作放在这里，减少管理员误操作。'}
            />
            <CardBody>
              {selectedUser ? (
                <div className="stack">
                  <div className="kv-grid">
                    <div className="kv"><label>显示名称</label><div>{sanitizeDisplayText(selectedUser.display_name)}</div></div>
                    <div className="kv"><label>系统用户名</label><div>@{sanitizeDisplayText(selectedUser.username)}</div></div>
                    <div className="kv"><label>邮箱</label><div>{sanitizeDisplayText(selectedUser.email || '未绑定')}</div></div>
                    <div className="kv"><label>角色</label><div>{labelize(selectedUser.role)}</div></div>
                    <div className="kv"><label>所属小组</label><div>{sanitizeDisplayText(selectedTeam?.name || '未分组')}</div></div>
                    <div className="kv"><label>当前状态</label><div><Badge tone="success">已启用</Badge></div></div>
                  </div>
                  <div className="message" data-role="agent">当前仓库这版后端只完整支持“新增账号”，并不完整支持“编辑/停用/重置密码”。本次前端先把页面语义修正为“详情 + 新增”，避免管理员被误导。</div>
                </div>
              ) : (
                <div className="stack">
                  <div className="form-grid">
                    <Field label="显示名称">
                      <Input value={form.display_name} onChange={(e) => setForm((prev) => ({ ...prev, display_name: e.target.value }))} placeholder="例如：John Doe" />
                    </Field>
                    <Field label="邮箱绑定（选填）">
                      <Input value={form.email} onChange={(e) => setForm((prev) => ({ ...prev, email: e.target.value }))} placeholder="john@example.com" />
                    </Field>
                    <Field label="系统用户名（唯一）">
                      <Input value={form.username} onChange={(e) => setForm((prev) => ({ ...prev, username: e.target.value }))} placeholder="例如：john.doe" />
                    </Field>
                    <Field label="初始密码（至少 6 位）">
                      <Input type="password" value={form.password} onChange={(e) => setForm((prev) => ({ ...prev, password: e.target.value }))} placeholder="请输入初始密码" />
                    </Field>
                    <Field label="基础角色分配">
                      <Select value={form.role} onChange={(e) => setForm((prev) => ({ ...prev, role: e.target.value }))}>
                        <option value="agent">Agent（普通客服）</option>
                        <option value="lead">Lead（主管）</option>
                        <option value="manager">Manager（经理）</option>
                        <option value="auditor">Auditor（质检）</option>
                        <option value="admin">Admin（系统管理员）</option>
                      </Select>
                    </Field>
                    <Field label="所属小组">
                      <Select value={form.team_id} onChange={(e) => setForm((prev) => ({ ...prev, team_id: e.target.value }))}>
                        <option value="">(无分组)</option>
                        {(Array.isArray(teamsQuery.data) ? teamsQuery.data : []).map((team: any) => <option key={team.id} value={team.id}>{team.name}</option>)}
                      </Select>
                    </Field>
                  </div>

                  <Field label="高级特权勾选" hint="只勾选需要覆盖基础角色的额外权限，不要把原始 capability 当作普通业务配置随意堆满。">
                    <div className="list compact">
                      {(Array.isArray(catalogQuery.data) ? catalogQuery.data : []).map((capability) => (
                        <label key={capability} className="list-item" style={{ cursor: 'pointer' }}>
                          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                            <input
                              type="checkbox"
                              checked={form.capabilities.includes(capability)}
                              onChange={() => {
                                setForm((prev) => ({
                                  ...prev,
                                  capabilities: prev.capabilities.includes(capability)
                                    ? prev.capabilities.filter((item) => item !== capability)
                                    : [...prev.capabilities, capability],
                                }))
                              }}
                            />
                            <div>
                              <strong>{capability}</strong>
                            </div>
                          </div>
                        </label>
                      ))}
                    </div>
                  </Field>

                  <div className="button-row">
                    <Button variant="primary" disabled={createUserMutation.isPending} onClick={() => createUserMutation.mutate()}>
                      {createUserMutation.isPending ? '开通中…' : '确认开通'}
                    </Button>
                    <Button onClick={() => setForm(emptyForm())}>重置表单</Button>
                  </div>
                </div>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/users',
  component: UsersPage,
  beforeLoad: () => {
    if (!getToken()) throw redirect({ to: '/login' })
  },
})
