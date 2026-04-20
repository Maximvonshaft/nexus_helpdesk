import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { AuthUser } from '@/lib/types'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Field, Input, Select } from '@/components/ui/Field'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/ui/PageHeader'
import { Toast } from '@/components/ui/Toast'
import { MetricCard } from '@/components/ui/MetricCard'
import { SegmentedControl } from '@/components/ui/SegmentedControl'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useSession } from '@/hooks/useAuth'

function canManageUsers(role?: string, capabilities?: string[]) {
  return ['admin'].includes(role?.toLowerCase() || '') || (capabilities || []).includes('user.manage')
}

function emptyForm(): Partial<AuthUser> & { password?: string } {
  return {
    username: '',
    password: '',
    display_name: '',
    email: '',
    role: 'agent',
    team_id: undefined,
    capabilities: [],
  }
}

function UsersPage() {
  const client = useQueryClient()
  const autoRefresh = useAutoRefresh(true)
  const session = useSession()
  const navigate = useNavigate()
  
  const permitted = canManageUsers(session.data?.role, session.data?.capabilities)
  
  const usersQuery = useQuery({ queryKey: ['users'], queryFn: api.users, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: permitted })
  const teamsQuery = useQuery({ queryKey: ['teams'], queryFn: api.teams, enabled: permitted })
  const catalogQuery = useQuery({ queryKey: ['catalog'], queryFn: api.capabilityCatalog, enabled: permitted })

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [roleFilter, setRoleFilter] = useState('all')
  const [form, setForm] = useState<Partial<AuthUser> & { password?: string }>(emptyForm())
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [session.data, permitted, navigate])

  const filtered = useMemo(() => {
    if (!usersQuery.data) return []
    let list = usersQuery.data as AuthUser[]
    if (roleFilter !== 'all') list = list.filter((u) => u.role === roleFilter)
    return list
  }, [usersQuery.data, roleFilter])

  const upsert = useMutation({
    mutationFn: async () => {
      const payload = {
        username: form.username,
        password: form.password,
        display_name: form.display_name,
        email: form.email || null,
        role: form.role,
        team_id: form.team_id || null,
        capabilities: form.capabilities || [],
      }
      
      const res = await fetch('/api/admin/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify(payload)
      })
      if (!res.ok) throw new Error(await res.text())
      return res.json()
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['users'] })
      setToast({ message: '账号开通成功', tone: 'success' })
      setForm(emptyForm())
      setSelectedId(null)
    },
    onError: (err) => setToast({ message: String(err), tone: 'danger' })
  })

  if (!permitted) return <AppShell><div className="empty-state">权限不足，无法访问账号管理模块。</div></AppShell>

  return (
    <AppShell>
      {toast && <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} />}
      <div className="layout-root">
        <PageHeader
          title="员工账号管理"
          description="管理内部客服账号和角色分配。"
          actions={<Button variant="primary" onClick={() => { setSelectedId(null); setForm(emptyForm()) }}>新增账号</Button>}
        />
        
        <div className="layout-body max-w-7xl mx-auto space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <MetricCard label="账号总数" value={usersQuery.data?.length ?? '-'} />
            <MetricCard label="当前过滤" value={filtered.length} />
          </div>
          
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-1 space-y-4">
              <Card>
                <CardHeader title="账号列表" subtitle="点击编辑详细信息" />
                <div className="border-b px-4 pb-4">
                  <SegmentedControl
                    options={[
                      { label: '全部', value: 'all' },
                      { label: 'Agent', value: 'agent' },
                      { label: 'Lead', value: 'lead' },
                      { label: 'Manager', value: 'manager' },
                    ]}
                    value={roleFilter}
                    onChange={setRoleFilter}
                  />
                </div>
                <CardBody>
                  {usersQuery.isLoading ? (
                    <div className="p-8 text-center text-sm text-neutral-500">加载中...</div>
                  ) : filtered.length === 0 ? (
                    <EmptyState text="没有找到账号。" />
                  ) : (
                    <div className="divide-y">
                      {filtered.map((u) => (
                        <div
                          key={u.id}
                          className={`p-4 cursor-pointer hover:bg-neutral-50 transition-colors ${selectedId === u.id ? 'bg-neutral-50 border-l-2 border-primary-500' : 'border-l-2 border-transparent'}`}
                          onClick={() => {
                            setSelectedId(u.id)
                            setForm({ ...u, password: '' })
                          }}
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-medium">{sanitizeDisplayText(u.display_name)}</span>
                            <Badge tone={u.role === 'admin' ? 'danger' : 'default'}>{u.role}</Badge>
                          </div>
                          <div className="text-xs text-neutral-500 mt-1 flex justify-between">
                            <span>@{u.username}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CardBody>
              </Card>
            </div>
            
            <div className="lg:col-span-2">
              <Card>
                <CardHeader
                  title={selectedId ? '编辑账号' : '新增账号'}
                  subtitle={selectedId ? '注意：目前暂不支持通过 UI 修改已存在的账号信息，仅支持新增。' : '填写必填项以完成注册。'}
                />
                <CardBody>
                  <div className="space-y-8">
                    {/* Basic Info Section */}
                    <section>
                      <h4 className="text-sm font-semibold text-neutral-900 border-b pb-2 mb-4">基本信息</h4>
                      <div className="grid grid-cols-2 gap-4">
                        <Field label="显示名称">
                          <Input
                            value={form.display_name}
                            onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                            placeholder="例如：John Doe"
                            disabled={selectedId !== null}
                          />
                        </Field>
                        <Field label="邮箱绑定 (选填)">
                          <Input
                            value={form.email || ''}
                            onChange={(e) => setForm({ ...form, email: e.target.value })}
                            placeholder="john@speedaf.com"
                            disabled={selectedId !== null}
                          />
                        </Field>
                      </div>
                    </section>

                    {/* System & Auth Section */}
                    <section>
                      <h4 className="text-sm font-semibold text-neutral-900 border-b pb-2 mb-4">系统账号</h4>
                      <div className="grid grid-cols-2 gap-4">
                        <Field label="系统用户名 (唯一)">
                          <Input
                            value={form.username}
                            onChange={(e) => setForm({ ...form, username: e.target.value })}
                            placeholder="例如：john.doe"
                            disabled={selectedId !== null}
                          />
                        </Field>
                        {!selectedId ? (
                          <Field label="初始密码 (至少6位)">
                            <Input
                              type="password"
                              value={form.password}
                              onChange={(e) => setForm({ ...form, password: e.target.value })}
                              placeholder="********"
                            />
                          </Field>
                        ) : (
                          <div className="text-sm text-neutral-500 pt-8">(密码不可通过 UI 修改)</div>
                        )}
                      </div>
                    </section>

                    {/* Roles & Permissions Section */}
                    <section>
                      <h4 className="text-sm font-semibold text-neutral-900 border-b pb-2 mb-4">角色与权限</h4>
                      <div className="grid grid-cols-2 gap-4 mb-4">
                        <Field label="基础角色分配">
                          <Select
                            value={form.role}
                            onChange={(e) => setForm({ ...form, role: e.target.value })}
                            disabled={selectedId !== null}
                          >
                            <option value="agent">Agent (普通客服)</option>
                            <option value="lead">Lead (主管)</option>
                            <option value="manager">Manager (经理)</option>
                            <option value="auditor">Auditor (质检员)</option>
                            <option value="admin">Admin (系统管理员)</option>
                          </Select>
                        </Field>
                        <Field label="所属小组">
                          <Select
                            value={form.team_id || ''}
                            onChange={(e) => setForm({ ...form, team_id: e.target.value ? Number(e.target.value) : undefined })}
                            disabled={selectedId !== null}
                          >
                            <option value="">(无分组)</option>
                            {(Array.isArray(teamsQuery.data) ? teamsQuery.data : []).map((t: any) => (
                              <option key={t.id} value={t.id}>{t.name}</option>
                            ))}
                          </Select>
                        </Field>
                      </div>

                      <Field label="高级特权勾选 (仅需勾选需覆盖默认角色的额外权限)">
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mt-2 max-h-64 overflow-y-auto border border-neutral-200 bg-neutral-50/50 p-4 rounded-md">
                          {(Array.isArray(catalogQuery.data) ? catalogQuery.data : []).map(cap => (
                            <label key={cap} className="flex items-start space-x-3 cursor-pointer group">
                              <input 
                                type="checkbox" 
                                checked={(form.capabilities || []).includes(cap)}
                                onChange={() => {
                                  const caps = form.capabilities || [];
                                  const newCaps = caps.includes(cap) ? caps.filter((c: string) => c !== cap) : [...caps, cap];
                                  setForm({ ...form, capabilities: newCaps });
                                }}
                                disabled={selectedId !== null}
                                className="mt-1 rounded border-neutral-300 text-primary-600 focus:ring-primary-500 disabled:opacity-50"
                              />
                              <span className="text-sm font-mono text-neutral-700 group-hover:text-neutral-900 break-all">{cap}</span>
                            </label>
                          ))}
                        </div>
                      </Field>
                    </section>
                  </div>
                  
                  <div className="pt-6 flex justify-end">
                    {!selectedId && (
                      <Button variant="primary" disabled={upsert.isPending} onClick={() => upsert.mutate()}>
                        确认开通
                      </Button>
                    )}
                  </div>
                </CardBody>
              </Card>
            </div>
          </div>
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
