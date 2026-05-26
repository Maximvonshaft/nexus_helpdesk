import type { ReactNode } from 'react'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { useSession } from '@/hooks/useAuth'
import { canAccess } from '@/lib/rbac'
import type { AccessRequirement } from '@/lib/rbac'

export function NoAccessCard({
  title = '无权限访问',
  description = '当前账号没有进入该页面或执行该动作所需的权限。',
  action = '请联系管理员或主管开通对应 capability。',
}: {
  title?: string
  description?: string
  action?: string
}) {
  return (
    <Card>
      <CardHeader title={title} subtitle={description} />
      <CardBody>
        <div className="message" data-role="agent">{action}</div>
      </CardBody>
    </Card>
  )
}

export function RequireCapability({
  requirement,
  children,
  fallback,
}: {
  requirement: AccessRequirement
  children: ReactNode
  fallback?: ReactNode
}) {
  const session = useSession()
  if (session.isLoading || session.isFetching) return null
  if (!canAccess(session.data, requirement)) return <>{fallback ?? <NoAccessCard />}</>
  return <>{children}</>
}
