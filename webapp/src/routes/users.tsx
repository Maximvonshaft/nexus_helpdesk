import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { AdminUser } from '@/lib/types'
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

function formFromUser(user: AdminUser | null): UserForm {
  if (!user) return emptyForm()
  return {
    username: user.username,
    password: '',
    display_name: user.display_name,
    email: user.email ?? '',
    role: user.role,
    team_id: user.team_id ? String(user.team_id) : '',
    capabilities: [...(user.capabilities ?? [])],
  }
}

function UsersPage() {
  const client = useQueryClient()
  const autoRefresh = useAutoRefresh(true)
  const session = useSession()
  const navigate = useNavigate()
  const permitted = canManageUsers(session.data)

  const usersQuery = useQuery({ queryKey: ['adminUsers'], queryFn: api.adminUsers, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: permitted })
  const teamsQuery = useQuery({ queryKey: ['teams'], queryFn: api.teams, enabled: permitted })
  const catalogQuery = useQuery({ queryKey: ['catalog'], queryFn: api.capabilityCatalog, enabled: permitted })

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [roleFilter, setRoleFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('active')
  const [search, setSearch] = useState('')
  const [form, setForm] = useState<UserForm>(emptyForm())
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [resetPassword, setResetPassword] = useState('')

  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [session.data, permitted, navigate])

  const allUsers = usersQuery.data ?? []
  const selectedUser = useMemo(() => allUsers.find((user) => user.id === selectedId) ?? null, [allUsers, selectedId])

  useEffect(() => {
    setForm(formFromUser(selectedUser))
    setResetPassword('')
  }, [selectedUser])

  const filteredUsers = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    return allUsers.filter((user) => {
      const roleOk = roleFilter === 'all' || user.role === roleFilter
      const statusOk = statusFilter === 'all' || (statusFilter === 'active' ? user.is_active : !user.is_active)
      const searchOk = !keyword || [user.display_name, user.username, user.email || ''].some((value) => String(value || '').toLowerCase().includes(keyword))
      return roleOk && statusOk && searchOk
    })
  }, [allUsers, roleFilter, statusFilter, search])

  const createMutation = useMutation({
    mutationFn: async () => api.createUser({
      username: form.username.trim(),
      password: form.password,
      display_name: form.display_name.trim(),
      email: form.email.trim() || null,
      role: form.role,
      team_id: form.team_id ? Number(form.team_id) : null,
      capabilities: form.capabilities,
    }),
    onSuccess: async (saved) => {
      await client.invalidateQueries({ queryKey: ['adminUsers'] })
      setSelectedId(saved.id)
      setToast({ message: '账号开通成功', tone: 'success' })
    },
    onError: (err: Error) => setToast({ message: err.message, tone: 'danger' }),
  })

  const updateMutation = useMutation({
    mutationFn: async () => {
      if (!selectedUser) throw new Error('未选中账号')
      return api.updateUser(selectedUser.id, {
        display_name: form.display_name.trim(),
        email: form.email.trim() || null,
        role: form.role,
        team_id: form.team_id ? Number(form.team_id) : null,
        capabilities: form.capabilities,
      })
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['adminUsers'] })
      setToast({ message: '账号信息已更新', tone: 'success' })
    },
    onError: (err: Error) => setToast({ message: err.message, tone: 'danger' }),
  })

  const toggleActiveMutation = useMutation({
    mutationFn: async () => {
      if (!selectedUser) throw new Error('未选中账号')
      return selectedUser.is_active ? api.deactivateUser(selectedUser.id) : api.activateUser(selectedUser.id)
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['adminUsers'] })
      setToast({ message: '账号状态已更新', tone: 'success' })
    },
    onError: (err: Error) => setToast({ message: err.message, tone: 'danger' }),
  })

  const resetPasswordMutation = useMutation({
    mutationFn: async () => {
      if (!selectedUser) throw new Error('未选中账号')
      return api.resetUserPassword(selectedUser.id, resetPassword)
    },
    onSuccess: async () => {
      setResetPassword('')
      setToast({ message: '密码已重置', tone: 'success' })
    },
    onError: (err: Error) => setToast({ message: err.message, tone: 'danger' }),
  })

  const roleOptions = [
    { label: '全部角色', value: 'all' },
    { label: 'Agent', value: 'agent' },
    { label: 'Lead', value: 'lead' },
    { label: 'Manager', value: 'manager' },
    { label: 'Auditor', value: 'auditor' },
    { label: 'Admin', value: 'admin' },
  ]

  return (
    <AppShell>
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
      <PageHeader
        eyebrow="账号管理"
        title="员工账号管理"
        description="当前版本按真实后端能力组织页面：列表支持 active / inactive，右侧支持新增、编辑、启停和重置密码。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button variant="primary" onClick={() => setSelectedId(null)}>新增账号</Button></div>}
      />
      <div className="metrics-grid">
        <MetricCard label="账号总数" value={allUsers.length} />
        <MetricCard label="启用中" value={allUsers.filter((user) => user.is_active).length} />
        <MetricCard label="已停用" value={allUsers.filter((user) => !user.is_active).length} />
        <MetricCard label="管理员" value={allUsers.filter((user) => user.role === 'admin').length} />
      </div>
      <div className="page-grid split-grid-wide">
        <Card>
          <CardHeader title="账号列表" subtitle="支持按角色、状态和关键字筛选。" />
          <CardBody>
            <div className="stack">
              <Field label="搜索账号"><Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="按显示名、用户名、邮箱搜索" /></Field>
              <Field label="角色筛选"><Select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>{roleOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</Select></Field>
              <Field label="状态筛选"><Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="all">全部状态</option><option value="active">仅启用</option><option value="inactive">仅停用</option></Select></Field>
              <div className="list">
                {!filteredUsers.length ? <EmptyState text="没有匹配到账号。" /> : null}
                {filteredUsers.map((user) => (
                  <button key={user.id} className={`queue-card ${selectedId === user.id ? 'selected' : ''}`} onClick={() => setSelectedId(user.id)}>
                    <div className="badges">
                      <Badge tone={user.role === 'admin' ? 'danger' : user.role === 'manager' ? 'warning' : 'default'}>{labelize(user.role)}</Badge>
                      <Badge tone={user.is_active ? 'success' : 'danger'}>{user.is_active ? '启用中' : '已停用'}</Badge>
                    </div>
                    <div className="queue-card-title">{sanitizeDisplayText(user.display_name)}</div>
                    <div className="queue-card-meta">@{sanitizeDisplayText(user.username)}{user.email ? ` · ${sanitizeDisplayText(user.email)}` : ''}</div>
                  </button>
                ))}
              </div>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title={selectedUser ? '账号详情 / 编辑' : '新增账号'} subtitle={selectedUser ? '已选中账号支持基础资料编辑、启停和密码重置。' : '新增账号时必须提供初始密码。'} />
          <CardBody>
            <div className="stack">
              <div className="form-grid">
                <Field label="显示名称"><Input value={form.display_name} onChange={(e) => setForm((prev) => ({ ...prev, display_name: e.target.value }))} /></Field>
                <Field label="邮箱"><Input value={form.email} onChange={(e) => setForm((prev) => ({ ...prev, email: e.target.value }))} /></Field>
                <Field label="系统用户名"><Input value={form.username} onChange={(e) => setForm((prev) => ({ ...prev, username: e.target.value }))} disabled={Boolean(selectedUser)} /></Field>
                {!selectedUser ? <Field label="初始密码（至少 6 位）"><Input type="password" value={form.password} onChange={(e) => setForm((prev) => ({ ...prev, password: e.target.value }))} /></Field> : null}
                <Field label="基础角色"><Select value={form.role} onChange={(e) => setForm((prev) => ({ ...prev, role: e.target.value }))}><option value="agent">Agent</option><option value="lead">Lead</option><option value="manager">Manager</option><option value="auditor">Auditor</option><option value="admin">Admin</option></Select></Field>
                <Field label="所属小组"><Select value={form.team_id} onChange={(e) => setForm((prev) => ({ ...prev, team_id: e.target.value }))}><option value="">(无分组)</option>{(teamsQuery.data ?? []).map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</Select></Field>
              </div>
              <Field label="高级权限覆盖">
                <div className="list compact">
                  {(catalogQuery.data ?? []).map((capability) => (
                    <label key={capability} className="list-item" style={{ cursor: 'pointer' }}>
                      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                        <input
                          type="checkbox"
                          checked={form.capabilities.includes(capability)}
                          onChange={() => setForm((prev) => ({ ...prev, capabilities: prev.capabilities.includes(capability) ? prev.capabilities.filter((item) => item !== capability) : [...prev.capabilities, capability] }))}
                        />
                        <div><strong>{capability}</strong></div>
                      </div>
                    </label>
                  ))}
                </div>
              </Field>
              <div className="button-row">
                {!selectedUser ? <Button variant="primary" disabled={createMutation.isPending} onClick={() => createMutation.mutate()}>{createMutation.isPending ? '开通中…' : '确认开通'}</Button> : <Button variant="primary" disabled={updateMutation.isPending} onClick={() => updateMutation.mutate()}>{updateMutation.isPending ? '保存中…' : '保存修改'}</Button>}
                {selectedUser ? <Button variant="secondary" disabled={toggleActiveMutation.isPending} onClick={() => toggleActiveMutation.mutate()}>{selectedUser.is_active ? '停用账号' : '启用账号'}</Button> : null}
                <Button onClick={() => setForm(formFromUser(selectedUser))}>重置表单</Button>
              </div>
              {selectedUser ? (
                <>
                  <div className="message" data-role="agent">当前状态：{selectedUser.is_active ? '已启用' : '已停用'} · 用户名不可直接修改，避免破坏引用关系。</div>
                  <Field label="重置密码（至少 6 位）">
                    <Input type="password" value={resetPassword} onChange={(e) => setResetPassword(e.target.value)} placeholder="输入新密码" />
                  </Field>
                  <div className="button-row"><Button variant="secondary" disabled={resetPasswordMutation.isPending || !resetPassword} onClick={() => resetPasswordMutation.mutate()}>{resetPasswordMutation.isPending ? '重置中…' : '确认重置密码'}</Button></div>
                </>
              ) : null}
            </div>
          </CardBody>
        </Card>
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
