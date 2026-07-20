export function identityRoleLabel(role: string) {
  if (role === 'admin') return '管理员'
  if (role === 'manager') return '运营经理'
  if (role === 'lead') return '组长'
  if (role === 'agent') return '客服专员'
  if (role === 'auditor') return '审计员'
  return role
}
