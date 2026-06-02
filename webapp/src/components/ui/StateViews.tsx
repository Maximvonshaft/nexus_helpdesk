import type { ReactNode } from 'react'
import { Link } from '@tanstack/react-router'
import { Badge } from './Badge'
import { Button } from './Button'
import { Card, CardBody, CardHeader } from './Card'
import type { AccessRequirement } from '@/lib/rbac'

function listCapabilities(requirement?: AccessRequirement) {
  if (!requirement) return []
  return [...(requirement.allOf ?? []), ...(requirement.anyOf ?? [])]
}

export function LoadingState({ title = '正在加载', description = '系统正在读取最新数据。' }: { title?: string; description?: string }) {
  return <div className="message" role="status" aria-live="polite"><strong>{title}</strong><div>{description}</div></div>
}

export function ErrorState({ title = '加载失败', description, onRetry }: { title?: string; description: string; onRetry?: () => void }) {
  return (
    <div className="message" role="alert" data-role="agent">
      <strong>{title}</strong>
      <div>{description}</div>
      {onRetry ? <Button variant="secondary" onClick={onRetry}>重试</Button> : null}
    </div>
  )
}

export function SuccessState({ title = '操作成功', description }: { title?: string; description: string }) {
  return <div className="message" role="status" aria-live="polite"><strong>{title}</strong><div>{description}</div></div>
}

export function WarningState({ title = '需要注意', description, children }: { title?: string; description: string; children?: ReactNode }) {
  return <div className="message" role="status" aria-live="polite"><strong>{title}</strong><div>{description}</div>{children}</div>
}

export function PermissionDeniedState({
  requirement,
  currentRole,
  route,
  impact = '该能力用于保护客户资料、运行配置或高风险操作，避免未授权账号通过直接 URL 预览敏感内容。',
}: {
  requirement?: AccessRequirement
  currentRole?: string | null
  route?: string
  impact?: string
}) {
  const caps = listCapabilities(requirement)
  return (
    <Card data-testid="permission-denied-state">
      <CardHeader title="无权限访问" subtitle={route ? `Direct URL guard blocked: ${route}` : 'Direct URL guard blocked this route.'} />
      <CardBody>
        <div className="stack">
          <div className="badges">
            <Badge tone="danger">403 / Permission Denied</Badge>
            <Badge>{currentRole || 'unknown role'}</Badge>
          </div>
          <div className="kv-grid">
            <div className="kv">
              <label>当前缺失 capability</label>
              <div>{caps.length ? caps.join(' / ') : 'route-specific capability'}</div>
            </div>
            <div className="kv">
              <label>当前用户角色</label>
              <div>{currentRole || '未识别'}</div>
            </div>
            <div className="kv">
              <label>该能力影响什么</label>
              <div>{impact}</div>
            </div>
            <div className="kv">
              <label>下一步</label>
              <div>请联系主管或系统管理员开通对应 capability；后端仍必须保留权限校验。</div>
            </div>
          </div>
          <Link to="/" aria-label="返回今日工作台">
            <Button variant="secondary">返回今日工作台</Button>
          </Link>
        </div>
      </CardBody>
    </Card>
  )
}
