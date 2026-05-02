import { Badge, type BadgeTone } from '../primitives'

export type OperationalStatus =
  | 'healthy'
  | 'degraded'
  | 'warning'
  | 'blocked'
  | 'offline'
  | 'pending'
  | 'unknown'

const toneByStatus: Record<OperationalStatus, BadgeTone> = {
  healthy: 'success',
  degraded: 'warning',
  warning: 'warning',
  blocked: 'danger',
  offline: 'danger',
  pending: 'info',
  unknown: 'default',
}

const labelByStatus: Record<OperationalStatus, string> = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  warning: 'Warning',
  blocked: 'Blocked',
  offline: 'Offline',
  pending: 'Pending',
  unknown: 'Unknown',
}

export interface StatusBadgeProps {
  status: OperationalStatus
  label?: string
}

export function StatusBadge({ status, label }: StatusBadgeProps) {
  return <Badge tone={toneByStatus[status]}>{label ?? labelByStatus[status]}</Badge>
}
