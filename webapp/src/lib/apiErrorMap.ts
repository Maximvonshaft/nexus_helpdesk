const detailMessages: Record<string, string> = {
  'Permission denied': '当前账号缺少执行该动作的权限。请联系主管或管理员确认 capability。',
  'Not authorized to manage users': '缺少 user.manage，无法管理账号权限。请联系管理员授权。',
  'Not authorized to manage runtime': '缺少 runtime.manage，无法执行运行恢复。请联系管理员或运维处理。',
  'Not authorized to manage channel accounts': '缺少 channel_account.manage，无法维护发送线路。',
  'Not authorized to manage AI config': '缺少 ai_config.manage，无法发布或回滚 AI 规则。',
  speedaf_work_order_requires_capability: '缺少 tool:speedaf.work_order.create:write，无法创建 Speedaf 催派工单。',
  speedaf_address_update_requires_capability: '缺少 tool:speedaf.order.update_address:write，无法提交 Speedaf 地址更新。',
  speedaf_cancel_requires_capability: '缺少 tool:speedaf.order.cancel:write，无法取消 Speedaf 运单。',
  speedaf_work_order_create_disabled: 'Speedaf 催派功能当前未启用，请联系运维确认 feature flag。',
  speedaf_update_address_disabled: 'Speedaf 地址更新功能当前未启用，请联系运维确认 feature flag。',
  speedaf_cancel_disabled: 'Speedaf 取消运单功能当前未启用，请联系运维确认 feature flag。',
  webcall_voice_read_requires_capability: '缺少 webcall.voice.read，无法查看 WebCall 语音会话。',
  webcall_voice_queue_requires_capability: '缺少 webcall.voice.queue.view，无法查看 WebCall 来电队列。',
  webcall_voice_accept_requires_capability: '缺少 webcall.voice.accept，无法接听 WebCall。',
  webcall_voice_reject_requires_capability: '缺少 webcall.voice.reject，无法拒接 WebCall。',
  webcall_voice_end_requires_capability: '缺少 webcall.voice.end，无法结束 WebCall。',
  webcall_voice_control_requires_capability: '缺少 webcall.voice.control，无法记录 WebCall 通话控制动作。',
  'Only lead or above can assign': '缺少 ticket.assign，无法分配工单。请联系组长或主管处理。',
  'Resolution category is required before closing a ticket': '关闭工单前必须填写解决分类，请先补全工单结论。',
  'This status change requires a note in workflow_update': '该状态变更必须填写内部备注，请说明原因后再保存。',
  email_subject_required: 'Email 主题不能为空。请填写主题后再发送客户邮件。',
  invalid_inbound_email_from_address: 'Inbound Email 的 From 地址无效，请填写客户邮箱。',
  inbound_email_body_required: 'Inbound Email 正文不能为空。',
  delivery_receipt_email_only: 'Delivery receipt 只能写入 Email outbound message。',
  smtp_configuration_missing: '没有可用的 Outbound Email SMTP 账号。请先配置并启用市场账号或全局 fallback。',
  smtp_auth_failed: 'SMTP 认证失败。请核对 username、密码和服务端授权方式后重试。',
  smtp_tls_failed: 'SMTP TLS/SSL 握手失败。请核对 security mode、端口和证书配置。',
  smtp_connect_timeout: '连接 SMTP 服务超时。请检查 host、port、网络策略和供应商状态。',
  smtp_connect_failed: '无法连接 SMTP 服务。请检查 host、port、DNS 和防火墙策略。',
  smtp_sender_rejected: 'SMTP 服务拒绝发件地址。请确认 From address 已被该账号授权。',
  smtp_recipient_rejected: 'SMTP 服务拒绝收件地址。请核对客户邮箱或使用测试收件人复测。',
  smtp_rate_limited: 'SMTP 服务触发限流。请稍后重试，必要时联系邮件供应商提升额度。',
  smtp_message_rejected: 'SMTP 服务拒绝邮件内容。请检查主题、正文、发件域策略和供应商限制。',
  smtp_unexpected_error: 'SMTP 发送出现未知错误。请查看技术详情或后端日志中的 request id。',
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
  const raw = typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : ''
  if (code && detailMessages[code]) return detailMessages[code]
  if (raw && detailMessages[raw]) return detailMessages[raw]
  if (status === 403) return raw ? `权限不足：${raw}。请联系主管或管理员开通对应 capability。` : '权限不足，请联系主管或管理员开通对应 capability。'
  if (status === 400) return raw || '请求内容不完整，请检查必填项后重试。'
  if (status === 409) return raw || '当前对象状态已变化，请刷新后重试。'
  return raw || fallback
}
