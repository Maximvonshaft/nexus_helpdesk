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
  'Only lead or above can assign': '缺少 ticket.assign，无法分配工单。请联系组长或主管处理。',
  'Resolution category is required before closing a ticket': '关闭工单前必须填写解决分类，请先补全工单结论。',
  'This status change requires a note in workflow_update': '该状态变更必须填写内部备注，请说明原因后再保存。',
}

export function mapApiErrorMessage(status: number, detail: unknown, fallback: string) {
  const raw = typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : ''
  if (raw && detailMessages[raw]) return detailMessages[raw]
  if (status === 403) return raw ? `权限不足：${raw}。请联系主管或管理员开通对应 capability。` : '权限不足，请联系主管或管理员开通对应 capability。'
  if (status === 400) return raw || '请求内容不完整，请检查必填项后重试。'
  if (status === 409) return raw || '当前对象状态已变化，请刷新后重试。'
  return raw || fallback
}
