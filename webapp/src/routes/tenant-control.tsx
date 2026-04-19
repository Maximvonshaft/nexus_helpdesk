import { useEffect, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { getToken } from '@/lib/api'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { Button } from '@/components/ui/Button'

async function req(path: string, method = 'GET', body?: unknown, tenantId?: number) {
  const token = getToken()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  if (tenantId) headers['X-Tenant-Id'] = String(tenantId)
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`)
  return res.json()
}

function TenantControlPage() {
  const [tenants, setTenants] = useState<any[]>([])
  const [tenantId, setTenantId] = useState<number | undefined>(undefined)
  const [profile, setProfile] = useState({ display_name: 'Support Assistant', brand_name: '', role_prompt: '', tone_style: 'professional' })
  const [knowledge, setKnowledge] = useState<any[]>([])
  const [entry, setEntry] = useState({ title: '', category: 'faq', content: '' })

  useEffect(() => {
    req('/api/tenants').then((rows) => {
      setTenants(rows || [])
      const first = rows?.[0]?.tenant?.id
      if (first) setTenantId(first)
    }).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!tenantId) return
    req('/api/tenants/current/ai-profile', 'GET', undefined, tenantId).then((row) => setProfile({
      display_name: row.display_name || 'Support Assistant',
      brand_name: row.brand_name || '',
      role_prompt: row.role_prompt || '',
      tone_style: row.tone_style || 'professional',
    })).catch(() => undefined)
    req('/api/tenants/current/knowledge', 'GET', undefined, tenantId).then((rows) => setKnowledge(rows || [])).catch(() => undefined)
  }, [tenantId])

  return (
    <AppShell>
      <PageHeader eyebrow="多租户" title="租户级客服人格与知识库" description="最小可用入口：切租户、改客服人格、维护知识条目。" />
      <div className="page-grid split-grid-wide">
        <Card>
          <CardHeader title="租户与客服人格" subtitle="每个租户独立的品牌客服名字、语气和角色 Prompt。" />
          <CardBody>
            <Field label="租户"><Select value={String(tenantId || '')} onChange={(e) => setTenantId(e.target.value ? Number(e.target.value) : undefined)}>{tenants.map((item) => <option key={item.tenant.id} value={item.tenant.id}>{item.tenant.name}</option>)}</Select></Field>
            <Field label="客服显示名"><Input value={profile.display_name} onChange={(e) => setProfile((s) => ({ ...s, display_name: e.target.value }))} /></Field>
            <Field label="品牌名"><Input value={profile.brand_name} onChange={(e) => setProfile((s) => ({ ...s, brand_name: e.target.value }))} /></Field>
            <Field label="语气风格"><Input value={profile.tone_style} onChange={(e) => setProfile((s) => ({ ...s, tone_style: e.target.value }))} /></Field>
            <Field label="角色 Prompt"><Textarea value={profile.role_prompt} onChange={(e) => setProfile((s) => ({ ...s, role_prompt: e.target.value }))} rows={6} /></Field>
            <Button onClick={() => tenantId && req('/api/tenants/current/ai-profile', 'PUT', { ...profile, forbidden_claims: [], escalation_policy: null, signature_style: 'Best regards', language_policy: null, system_prompt_overrides: null, system_context: {}, enable_auto_reply: true, enable_auto_summary: true, enable_auto_classification: true, allowed_actions: ['draft_reply', 'summarize', 'classify'], default_model_key: null }, tenantId)}>保存租户人格</Button>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="租户知识库" subtitle="先用本地 DB 版知识条目，把 FAQ / SOP / Policy 搭起来。" />
          <CardBody>
            <Field label="标题"><Input value={entry.title} onChange={(e) => setEntry((s) => ({ ...s, title: e.target.value }))} /></Field>
            <Field label="类别"><Select value={entry.category} onChange={(e) => setEntry((s) => ({ ...s, category: e.target.value }))}><option value="faq">FAQ</option><option value="sop">SOP</option><option value="policy">Policy</option></Select></Field>
            <Field label="内容"><Textarea value={entry.content} onChange={(e) => setEntry((s) => ({ ...s, content: e.target.value }))} rows={8} /></Field>
            <Button onClick={async () => { if (!tenantId) return; await req('/api/tenants/current/knowledge', 'POST', { ...entry, source_type: 'manual', priority: 100 }, tenantId); const rows = await req('/api/tenants/current/knowledge', 'GET', undefined, tenantId); setKnowledge(rows || []); setEntry({ title: '', category: 'faq', content: '' }) }}>新增知识条目</Button>
            <div className="list">{knowledge.map((item) => <div key={item.id} className="list-item"><div><strong>{item.title}</strong></div><div className="section-subtitle">{item.category}</div><div className="section-subtitle">{item.content}</div></div>)}</div>
          </CardBody>
        </Card>
      </div>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/tenant-control',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: TenantControlPage,
})
