import { useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { routeAccess } from '@/lib/rbac'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { DataTable } from '@/components/ui/DataTable'
import { EmptyState } from '@/components/ui/EmptyState'
import { Skeleton } from '@/components/ui/Skeleton'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Textarea } from '@/components/ui/Field'
import { MetricCard } from '@/components/ui/MetricCard'
import { RequireCapability } from '@/components/security/RequireCapability'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import type { BadgeTone, PersonaBuilderSimulationScenario, PersonaRuntimeEvidenceResult } from '@/lib/types'

function safeTone(value: string | null | undefined): BadgeTone {
  return value === 'danger' || value === 'warning' || value === 'success' ? value : 'default'
}

function statusTone(value: string): BadgeTone {
  if (value === 'implemented') return 'success'
  if (value === 'linked') return 'warning'
  if (value === 'not_implemented') return 'danger'
  return 'default'
}

function profileTone(value: { is_active: boolean; published_ready: boolean; needs_publish: boolean }): BadgeTone {
  if (!value.is_active) return 'danger'
  if (value.needs_publish) return 'warning'
  if (value.published_ready) return 'success'
  return 'default'
}

function formatScope(item: PersonaBuilderSimulationScenario) {
  return [
    item.market_id == null ? 'market:global' : `market:${item.market_id}`,
    `channel:${item.channel || 'global'}`,
    `lang:${item.language || 'global'}`,
  ].join(' / ')
}

function parseMarketId(raw: string) {
  const trimmed = raw.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : Number.NaN
}

function runtimeEvidenceSnapshot(data: PersonaRuntimeEvidenceResult) {
  const personaContext = data.persona_context ?? {}
  return JSON.stringify({
    matched_profile_key: data.matched_profile_key,
    match_rank: data.match_rank,
    expected_profile_key: data.expected_profile_key,
    matched_expected: data.matched_expected,
    evidence: data.evidence,
    metadata_filters: data.runtime_context.metadata_filters,
    identity_context: typeof personaContext === 'object' && personaContext ? personaContext.identity_context : null,
  }, null, 2)
}

function PersonaBuilderPage() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const autoRefresh = useAutoRefresh()
  const [previewMarketId, setPreviewMarketId] = useState('1')
  const [previewChannel, setPreviewChannel] = useState('webchat')
  const [previewLanguage, setPreviewLanguage] = useState('en')
  const [runtimeBody, setRuntimeBody] = useState('Who are you and can you help with delivery appointments?')
  const builder = useQuery({
    queryKey: ['personaBuilder'],
    queryFn: api.personaBuilder,
    refetchInterval: autoRefresh.enabled ? 30000 : false,
  })
  const marketId = parseMarketId(previewMarketId)
  const marketIdInvalid = Number.isNaN(marketId)
  const normalizedMarketId = marketIdInvalid ? null : marketId
  const resolvePreview = useMutation({
    mutationFn: () => api.resolvePersonaPreview({
      market_id: normalizedMarketId,
      channel: previewChannel.trim() || null,
      language: previewLanguage.trim() || null,
    }),
  })
  const runtimeEvidence = useMutation({
    mutationFn: () => api.personaRuntimeEvidence({
      tenant_key: 'default',
      body: runtimeBody,
      market_id: normalizedMarketId,
      channel: previewChannel.trim() || null,
      language: previewLanguage.trim() || null,
      audience_scope: 'customer',
      expected_profile_key: resolvePreview.data?.profile?.profile_key || null,
    }),
  })
  const submitReview = useMutation({
    mutationFn: (profileId: number) => api.submitPersonaReview(profileId, { notes: 'submit from Persona Builder' }),
    onSuccess: () => void refresh(),
  })
  const approveReview = useMutation({
    mutationFn: (reviewId: number) => api.approvePersonaReview(reviewId, { decision_note: 'approved from Persona Builder' }),
    onSuccess: () => void refresh(),
  })
  const rejectReview = useMutation({
    mutationFn: (reviewId: number) => api.rejectPersonaReview(reviewId, { decision_note: 'rejected from Persona Builder' }),
    onSuccess: () => void refresh(),
  })
  const publishReview = useMutation({
    mutationFn: (reviewId: number) => api.publishPersonaReview(reviewId, 'publish approved Persona review'),
    onSuccess: () => void refresh(),
  })

  const canManage = Boolean(builder.data?.capabilities.includes('ai_config.manage'))
  const suggestedScenarios = useMemo(() => (builder.data?.simulation_scenarios ?? []).slice(0, 4), [builder.data?.simulation_scenarios])

  const goTarget = (href: string) => {
    if (href === '/ai-control') navigate({ to: '/ai-control' })
    else navigate({ to: '/persona-builder' })
  }

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['personaBuilder'] }),
      client.invalidateQueries({ queryKey: ['controlTower'] }),
    ])
  }

  const applyScenario = (item: PersonaBuilderSimulationScenario) => {
    setPreviewMarketId(item.market_id == null ? '' : String(item.market_id))
    setPreviewChannel(item.channel || '')
    setPreviewLanguage(item.language || '')
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="AI Persona Builder"
        title="AI Persona Builder / 人格配置与运行证据"
        description="AI Ops 从真实 PersonaProfile、PersonaProfileVersion 和 resolve-preview 契约查看人格草稿、发布、匹配、回滚和运行时身份上下文。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={() => void refresh()} disabled={builder.isFetching}>刷新</Button><Button variant="primary" onClick={() => navigate({ to: '/ai-control' })} disabled={!canManage}>编辑 Persona</Button></div>}
      />

      <RequireCapability requirement={routeAccess['/persona-builder']}>
        {builder.isLoading ? <Skeleton lines={6} /> : null}
        {builder.isError ? <ErrorSummary title="Persona Builder 加载失败" errors={[builder.error instanceof Error ? builder.error.message : '请稍后重试']} action={<Button variant="secondary" onClick={() => void refresh()}>重试</Button>} /> : null}
        {builder.data ? (
          <div className="stack" data-testid="persona-builder-template-blocks">
            <div className="metrics-grid metrics-grid-wide" data-testid="persona-builder-real-kpis">
              {builder.data.kpis.map((item) => (
                <div className="stack" key={item.key}>
                  <MetricCard label={item.label} value={item.value} hint={item.hint} />
                  <Badge tone={safeTone(item.tone)}>{labelize(item.tone)}</Badge>
                </div>
              ))}
            </div>

            <Card className="soft" data-testid="persona-builder-profile-library">
              <CardHeader title="Persona Profiles / Release Readiness" subtitle="读取真实 PersonaProfile，不使用前端 fixture；草稿差异、发布状态和 scope 风险由后端 read-model 派生。" />
              <CardBody>
                <DataTable
                  columns={['Persona', 'Scope', '发布', '身份 / 边界', '风险', '证据', '入口']}
                  rows={builder.data.profiles.map((item) => [
                    <div className="stack"><strong>{sanitizeDisplayText(item.name)}</strong><small>{sanitizeDisplayText(item.profile_key)}</small></div>,
                    <div className="stack"><span>{sanitizeDisplayText(item.scope_label)}</span><small>specificity {item.scope_specificity}</small></div>,
                    <div className="badges"><Badge tone={profileTone(item)}>{item.is_active ? 'active' : 'inactive'}</Badge><Badge tone={item.published_ready ? 'success' : 'warning'}>v{item.published_version}</Badge><Badge tone={item.needs_publish ? 'warning' : 'success'}>{item.needs_publish ? 'needs publish' : 'published'}</Badge></div>,
                    <div className="badges"><Badge tone={item.identity_ready ? 'success' : 'danger'}>{item.identity_ready ? 'identity' : 'missing identity'}</Badge><Badge tone={item.boundary_ready ? 'success' : 'warning'}>{item.boundary_ready ? 'boundary' : 'boundary gap'}</Badge><Badge>{item.guardrail_count} controls</Badge></div>,
                    item.risk_flags.length ? <div className="badges">{item.risk_flags.map((flag) => <Badge key={flag} tone="warning">{sanitizeDisplayText(flag)}</Badge>)}</div> : <Badge tone="success">clear</Badge>,
                    sanitizeDisplayText(item.evidence),
                    <div className="button-row"><Button variant="secondary" onClick={() => goTarget(item.href)}>打开</Button><Button disabled={!canManage || !item.draft_ready || submitReview.isPending} onClick={() => submitReview.mutate(item.id)}>送审</Button></div>,
                  ])}
                  empty={<EmptyState title="还没有 Persona" description="通过 AI 规则创建默认 WebChat Persona 并发布。" reason="没有已发布 Persona 时，运行时只能使用基础规则。" />}
                />
              </CardBody>
            </Card>

            <div className="page-grid split-grid-wide">
              <Card data-testid="persona-builder-resolve-preview">
                <CardHeader title="Simulation / Resolve Preview" subtitle="调用真实 /api/persona-profiles/resolve-preview，验证 market/channel/language 的发布人格选择。" />
                <CardBody>
                  <div className="stack">
                    <div className="form-grid">
                      <Field label="Market ID" hint="留空表示 global；只接受数字。" error={marketIdInvalid ? 'Market ID 必须是数字。' : undefined}><Input value={previewMarketId} onChange={(event) => setPreviewMarketId(event.target.value)} placeholder="1" /></Field>
                      <Field label="Channel"><Input value={previewChannel} onChange={(event) => setPreviewChannel(event.target.value)} placeholder="webchat" /></Field>
                      <Field label="Language"><Input value={previewLanguage} onChange={(event) => setPreviewLanguage(event.target.value)} placeholder="en" /></Field>
                    </div>
                    {suggestedScenarios.length ? <div className="badges">{suggestedScenarios.map((item, index) => <Button key={`${formatScope(item)}-${index}`} variant="secondary" onClick={() => applyScenario(item)}>{sanitizeDisplayText(formatScope(item))}</Button>)}</div> : null}
                    <div className="button-row"><Button variant="primary" disabled={marketIdInvalid || resolvePreview.isPending} onClick={() => resolvePreview.mutate()}>运行匹配测试</Button></div>
                    {resolvePreview.isError ? <ErrorSummary title="匹配测试失败" errors={[resolvePreview.error instanceof Error ? resolvePreview.error.message : '请检查权限或稍后重试']} /> : null}
                    {resolvePreview.data ? (
                      <div className="kv-grid">
                        <div className="kv"><label>匹配 Persona</label><strong>{sanitizeDisplayText(resolvePreview.data.profile?.profile_key || 'no_match')}</strong></div>
                        <div className="kv"><label>Match Rank</label><strong>{sanitizeDisplayText(resolvePreview.data.match_rank ?? '—')}</strong></div>
                        <div className="kv"><label>Published</label><strong>v{sanitizeDisplayText(resolvePreview.data.profile?.published_version ?? '—')}</strong></div>
                      </div>
                    ) : null}
                    <DataTable
                      columns={['Scope', '匹配', 'Rank', '原因', 'Fallback']}
                      rows={builder.data.simulation_scenarios.map((item) => [
                        sanitizeDisplayText(formatScope(item)),
                        sanitizeDisplayText(item.matched_profile_key || item.status),
                        sanitizeDisplayText(item.match_rank ?? '—'),
                        <div className="badges">{item.reasons.map((reason) => <Badge key={reason}>{sanitizeDisplayText(reason)}</Badge>)}</div>,
                        <Badge tone={item.fallback ? 'warning' : item.status === 'matched' ? 'success' : 'danger'}>{item.fallback ? 'fallback' : item.status}</Badge>,
                      ])}
                    />
                  </div>
                </CardBody>
              </Card>

              <Card data-testid="persona-builder-runtime-evidence">
                <CardHeader title="Runtime Evidence" subtitle="调用真实 /api/persona-profiles/runtime-evidence，验证 WebChat 运行时注入的已发布 Persona、身份上下文、guardrails 和匹配证据。" />
                <CardBody>
                  <div className="stack">
                    <div className="kv-grid">
                      <div className="kv"><label>Identity Ready</label><strong>{sanitizeDisplayText(builder.data.facts.identity_ready_profiles)}</strong></div>
                      <div className="kv"><label>Boundary Ready</label><strong>{sanitizeDisplayText(builder.data.facts.boundary_ready_profiles)}</strong></div>
                      <div className="kv"><label>Fallback Profiles</label><strong>{sanitizeDisplayText(builder.data.facts.global_fallback_profiles)}</strong></div>
                      <div className="kv"><label>Runtime Evidence Endpoint</label><strong>{sanitizeDisplayText(String(builder.data.facts.dedicated_runtime_evidence_endpoint))}</strong></div>
                    </div>
                    <Field label="Runtime Query" hint="使用当前 market/channel/language，向后端请求真实运行时上下文证据。">
                      <Textarea rows={4} value={runtimeBody} onChange={(event) => setRuntimeBody(event.target.value)} />
                    </Field>
                    <div className="button-row">
                      <Button
                        variant="primary"
                        data-testid="persona-builder-runtime-evidence-command"
                        disabled={marketIdInvalid || !runtimeBody.trim() || runtimeEvidence.isPending}
                        onClick={() => runtimeEvidence.mutate()}
                      >
                        生成运行证据
                      </Button>
                    </div>
                    {runtimeEvidence.isError ? <ErrorSummary title="运行证据查询失败" errors={[runtimeEvidence.error instanceof Error ? runtimeEvidence.error.message : '请检查权限或稍后重试']} /> : null}
                    {runtimeEvidence.data ? (
                      <div className="stack">
                        <div className="kv-grid">
                          <div className="kv"><label>Matched Persona</label><strong>{sanitizeDisplayText(runtimeEvidence.data.matched_profile_key)}</strong></div>
                          <div className="kv"><label>Match Rank</label><strong>{sanitizeDisplayText(runtimeEvidence.data.match_rank ?? null)}</strong></div>
                          <div className="kv"><label>Expected Match</label><strong>{sanitizeDisplayText(runtimeEvidence.data.matched_expected ?? null)}</strong></div>
                          <div className="kv"><label>Brand</label><strong>{sanitizeDisplayText(String(runtimeEvidence.data.evidence.brand_name ?? '—'))}</strong></div>
                          <div className="kv"><label>Assistant</label><strong>{sanitizeDisplayText(String(runtimeEvidence.data.evidence.assistant_name ?? '—'))}</strong></div>
                          <div className="kv"><label>Guardrails</label><strong>{sanitizeDisplayText(String(runtimeEvidence.data.evidence.guardrail_count ?? '—'))}</strong></div>
                        </div>
                        <pre className="code-block">{runtimeEvidenceSnapshot(runtimeEvidence.data)}</pre>
                      </div>
                    ) : null}
                  </div>
                  <DataTable
                    columns={['模板块', '后端契约', '状态']}
                    rows={builder.data.template_blocks.filter((item) => item.key === 'runtime-evidence' || item.key === 'resolve-preview').map((item) => [
                      sanitizeDisplayText(item.label),
                      sanitizeDisplayText(item.backend_contract),
                      <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>,
                    ])}
                  />
                </CardBody>
              </Card>
            </div>

            <div className="page-grid split-grid-wide">
              <Card data-testid="persona-builder-approval-queue">
                <CardHeader title="Approval Queue / Release Window" subtitle="真实写入 persona_profile_reviews；审批后可以按 release window 发布已审批快照。" />
                <CardBody>
                  <div className="stack">
                    {submitReview.isError || approveReview.isError || rejectReview.isError || publishReview.isError ? (
                      <ErrorSummary
                        title="审批动作失败"
                        errors={[submitReview.error, approveReview.error, rejectReview.error, publishReview.error].filter(Boolean).map((error) => error instanceof Error ? error.message : '请检查权限或状态')}
                      />
                    ) : null}
                    <DataTable
                      columns={['Review', 'Scope', '状态', 'Release Window', '证据', '动作']}
                      rows={builder.data.approval_queue.map((item) => [
                        <div className="stack"><strong>{sanitizeDisplayText(item.profile_name || item.profile_key)}</strong><small>review #{item.review_version} · {sanitizeDisplayText(item.summary)}</small></div>,
                        sanitizeDisplayText(item.scope_label),
                        <Badge tone={item.status === 'published' ? 'success' : item.status === 'approved' ? 'warning' : item.status === 'rejected' ? 'danger' : 'default'}>{labelize(item.status)}</Badge>,
                        <div className="stack"><span>{formatDateTime(item.release_window_start)}</span><small>{formatDateTime(item.release_window_end)}</small></div>,
                        sanitizeDisplayText(item.evidence),
                        <div className="button-row">
                          <Button disabled={!canManage || item.status !== 'pending' || approveReview.isPending} onClick={() => approveReview.mutate(item.id)}>批准</Button>
                          <Button variant="danger" disabled={!canManage || item.status !== 'pending' || rejectReview.isPending} onClick={() => rejectReview.mutate(item.id)}>拒绝</Button>
                          <Button variant="primary" disabled={!canManage || item.status !== 'approved' || publishReview.isPending} onClick={() => publishReview.mutate(item.id)}>发布</Button>
                        </div>,
                      ])}
                      empty={<EmptyState title="没有待审批 Persona" description="在 Persona 列表中选择有草稿的配置送审。" reason="审批记录保存在后端，发布时使用审批快照而不是前端状态。" />}
                    />
                  </div>
                </CardBody>
              </Card>

              <Card data-testid="persona-builder-release-lifecycle">
                <CardHeader title="Release Lifecycle" subtitle="对应模板里的 Draft、Simulation、Impact Preview、Approval、Published、Rollback 和 Runtime Evidence。" />
                <CardBody>
                  <DataTable
                    columns={['步骤', 'Owner', 'Artifact', '数量', '状态', '入口']}
                    rows={builder.data.release_lifecycle.map((item) => [
                      sanitizeDisplayText(item.step),
                      sanitizeDisplayText(item.owner),
                      sanitizeDisplayText(item.artifact),
                      String(item.count),
                      <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>,
                      <Button variant="secondary" disabled={!item.enabled} onClick={() => goTarget(item.href)}>{item.enabled ? '打开' : '未落地'}</Button>,
                    ])}
                  />
                </CardBody>
              </Card>

              <Card data-testid="persona-builder-template-closure">
                <CardHeader title="v1.7.8 AI Persona Builder 模板块落地状态" subtitle="真实后端已接入的 Persona 草稿、审批、发布、resolve preview 和 runtime evidence 能力在同一处明示。" />
                <CardBody>
                  <DataTable
                    columns={['模板块', '后端契约', '状态', '证据', '入口']}
                    rows={builder.data.template_blocks.map((item) => [
                      sanitizeDisplayText(item.label),
                      sanitizeDisplayText(item.backend_contract),
                      <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>,
                      sanitizeDisplayText(item.evidence),
                      <Button variant="secondary" onClick={() => goTarget(item.href)}>查看</Button>,
                    ])}
                  />
                  <div className="section-subtitle" style={{ marginTop: 12 }}>Generated {formatDateTime(builder.data.generated_at)} · approval {sanitizeDisplayText(String(builder.data.facts.approval_endpoint))} · submit review {sanitizeDisplayText(String(builder.data.facts.submit_review_endpoint))}</div>
                </CardBody>
              </Card>
            </div>
          </div>
        ) : null}
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/persona-builder',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: PersonaBuilderPage,
})
