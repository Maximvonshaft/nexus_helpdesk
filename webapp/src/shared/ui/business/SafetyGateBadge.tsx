import { Badge, type BadgeTone } from '../primitives'

export type SafetyGateState =
  | 'safe'
  | 'needs_review'
  | 'blocked'
  | 'unsupported_fact'
  | 'sensitive_content'
  | 'policy_violation'

const toneByState: Record<SafetyGateState, BadgeTone> = {
  safe: 'success',
  needs_review: 'warning',
  blocked: 'danger',
  unsupported_fact: 'warning',
  sensitive_content: 'danger',
  policy_violation: 'danger',
}

const labelByState: Record<SafetyGateState, string> = {
  safe: 'Safety Safe',
  needs_review: 'Needs Review',
  blocked: 'Blocked',
  unsupported_fact: 'Unsupported Fact',
  sensitive_content: 'Sensitive Content',
  policy_violation: 'Policy Violation',
}

export interface SafetyGateBadgeProps {
  state: SafetyGateState
  label?: string
}

export function SafetyGateBadge({ state, label }: SafetyGateBadgeProps) {
  return <Badge tone={toneByState[state]}>{label ?? labelByState[state]}</Badge>
}
