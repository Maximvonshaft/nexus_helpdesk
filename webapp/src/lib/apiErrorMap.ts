const detailMessages: Record<string, string> = {
  'Permission denied': '当前账号没有执行此操作的权限。请联系系统管理员。',
  'Not authorized to manage users': '当前账号没有用户管理权限。请联系系统管理员。',
  'Not authorized to manage runtime': '当前账号没有系统运行管理权限。请联系系统管理员。',
  'Not authorized to manage channel accounts': '当前账号没有渠道管理权限。请联系系统管理员。',
  'Not authorized to manage AI config': '当前账号没有自动处理配置权限。请联系系统管理员。',
  speedaf_work_order_requires_capability: '当前账号没有创建催派工单的权限。',
  speedaf_address_update_requires_capability: '当前账号没有修改收件地址的权限。',
  speedaf_cancel_requires_capability: '当前账号没有取消运单的权限。',
  speedaf_work_order_create_disabled: '催派工单功能当前未启用。请联系系统管理员。',
  speedaf_mcp_disabled: 'Speedaf 系统连接当前未启用。请联系系统管理员。',
  speedaf_update_address_disabled: '修改收件地址功能当前未启用。请联系系统管理员。',
  speedaf_cancel_disabled: '取消运单功能当前未启用。请联系系统管理员。',
  webcall_voice_read_requires_capability: '当前账号没有查看语音会话的权限。',
  webcall_voice_queue_requires_capability: '当前账号没有查看来电队列的权限。',
  webcall_voice_accept_requires_capability: '当前账号没有接听来电的权限。',
  webcall_voice_reject_requires_capability: '当前账号没有拒接来电的权限。',
  webcall_voice_end_requires_capability: '当前账号没有结束通话的权限。',
  webcall_voice_control_requires_capability: '当前账号没有记录通话操作的权限。',
  'Only lead or above can assign': '当前账号没有分配工单的权限。请联系组长或主管。',
  'Resolution category is required before closing a ticket': '关闭工单前必须填写解决分类。',
  'This status change requires a note in workflow_update': '该状态变更必须填写内部备注。',
  email_subject_required: '邮件主题不能为空。',
  invalid_inbound_email_from_address: '客户邮箱格式不正确。',
  inbound_email_body_required: '邮件正文不能为空。',
  delivery_receipt_email_only: '当前送达回执只能用于邮件消息。',
  smtp_configuration_missing: '没有可用的邮件发送账号。请先在渠道管理中完成配置和测试。',
  smtp_auth_failed: '邮件账号验证失败。请核对账号、密码和服务商授权设置。',
  smtp_tls_failed: '邮件服务安全连接失败。请核对安全模式、端口和证书。',
  smtp_connect_timeout: '连接邮件服务超时。请检查网络和服务商状态。',
  smtp_connect_failed: '无法连接邮件服务。请检查服务器地址、端口和网络设置。',
  smtp_sender_rejected: '邮件服务拒绝当前发件地址。请确认发件地址已获得授权。',
  smtp_recipient_rejected: '邮件服务拒绝收件地址。请核对客户邮箱。',
  smtp_rate_limited: '邮件发送过于频繁。请稍后重试。',
  smtp_message_rejected: '邮件服务拒绝发送此邮件。请检查主题、正文和发件域设置。',
  smtp_unexpected_error: '邮件发送失败。请稍后重试或联系系统管理员。',
}

function detailCode(detail: unknown) {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'error_code' in detail) {
    const code = (detail as { error_code?: unknown }).error_code
    return typeof code === 'string' ? code : ''
  }
  return ''
}

export function mapApiErrorMessage(status: number, detail: unknown, fallback: string) {
  const code = detailCode(detail)
  if (code && detailMessages[code]) return detailMessages[code]
  if (status === 401) return '登录状态已失效，请重新登录。'
  if (status === 403) return '权限不足，请联系系统管理员。'
  if (status === 400 || status === 422) return '提交内容不完整或格式不正确，请检查后重试。'
  if (status === 404) return '未找到相关记录，可能已被删除或当前账号无权查看。'
  if (status === 409) return '当前记录已发生变化，请刷新后重新确认。'
  if (status >= 500) return '系统暂时无法完成此操作，请稍后重试。'
  return fallback
}
