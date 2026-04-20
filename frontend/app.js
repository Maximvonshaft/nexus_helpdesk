function resolveApiBase() {
  return `${window.location.origin.replace(/\/$/, '')}/api`;
}

const state = {
  apiBase: resolveApiBase(),
  token: sessionStorage.getItem('HELPDESK_TOKEN') || '',
  currentUser: null,
  users: [],
  teams: [],
  markets: [],
  statuses: [],
  priorities: [],
  cases: [],
  selectedCaseId: null,
  selectedCase: null,
  selectedTicket: null,
  selectedBulletinId: null,
  selectedAccountId: null,
  activeView: 'overview',
  filterStatus: '',
  query: '',
  previousMap: new Map(),
  pollHandle: null,
  commandFilter: '',
  caseEditor: {
    caseId: null,
    dirty: false,
    saving: false,
    remoteChanged: false,
    lastLoadedAt: null,
  },
  ops: {
    queueSummary: null,
    runtimeHealth: null,
    signoff: null,
    readiness: null,
    bulletins: [],
    channelAccounts: [],
    jobs: [],
  },
};

const $ = (id) => document.getElementById(id);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const CASE_EDITOR_INPUT_IDS = [
  'edit-missing-fields',
  'edit-required-action',
  'edit-customer-update',
  'edit-resolution-summary',
  'status-select',
  'assign-user',
  'human-note',
  'ai-summary',
  'ai-case-type',
  'ai-required-action',
  'ai-missing-fields',
];

function createNode(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  const { className, text, dataset, attrs } = options;
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (dataset) Object.entries(dataset).forEach(([key, value]) => {
    if (value !== undefined && value !== null) node.dataset[key] = String(value);
  });
  if (attrs) Object.entries(attrs).forEach(([key, value]) => {
    if (value !== undefined && value !== null) node.setAttribute(key, String(value));
  });
  const normalizedChildren = Array.isArray(children) ? children : [children];
  normalizedChildren.flat().filter((child) => child !== undefined && child !== null && child !== false).forEach((child) => {
    if (typeof child === 'string') {
      node.appendChild(document.createTextNode(child));
    } else {
      node.appendChild(child);
    }
  });
  return node;
}

function createEmptyState(message, className = 'empty-state') {
  return createNode('div', { className, text: message });
}

function createOption(value, label) {
  const option = document.createElement('option');
  option.value = value === undefined || value === null ? '' : String(value);
  option.textContent = label;
  return option;
}

function replaceNodeChildren(target, children) {
  if (!target) return;
  const nodes = (Array.isArray(children) ? children : [children]).flat().filter(Boolean);
  target.replaceChildren(...nodes);
}

function showToast(message, isError = false) {
  const el = $('toast');
  el.textContent = message;
  el.classList.remove('hidden');
  el.classList.toggle('toast-error', !!isError);
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => el.classList.add('hidden'), 2200);
}

function setSaveHint(message) {
  const el = $('save-hint');
  if (el) el.textContent = message;
}

function resetCaseEditor(caseId, updatedAt) {
  state.caseEditor.caseId = caseId;
  state.caseEditor.dirty = false;
  state.caseEditor.saving = false;
  state.caseEditor.remoteChanged = false;
  state.caseEditor.lastLoadedAt = updatedAt || null;
  setSaveHint('就绪');
}

function markCaseEditorDirty() {
  if (!state.selectedCaseId || state.caseEditor.saving) return;
  state.caseEditor.caseId = state.selectedCaseId;
  state.caseEditor.dirty = true;
  setSaveHint(state.caseEditor.remoteChanged ? '检测到远端更新，当前保留你的编辑' : '有未保存编辑');
}

function syncCaseEditorFromSelected(caseDetail, ticketDetail, force = false) {
  const nextUpdatedAt = caseDetail?.last_updated || null;
  const switchingCase = state.caseEditor.caseId !== caseDetail?.id;
  const shouldApply = force || switchingCase || !state.caseEditor.dirty || state.caseEditor.saving;

  if (shouldApply) {
    $('edit-missing-fields').value = caseDetail?.missing_fields || ticketDetail?.missing_fields || '';
    $('edit-required-action').value = caseDetail?.required_action || ticketDetail?.required_action || '';
    $('edit-customer-update').value = caseDetail?.customer_update || ticketDetail?.customer_update || '';
    $('edit-resolution-summary').value = caseDetail?.resolution_summary || ticketDetail?.resolution_summary || '';
    $('status-select').value = caseDetail?.status || 'new';
    $('human-note').value = '';
    $('assign-user').value = String(findUserIdByName(caseDetail?.assigned_to) || '');
    $('ai-summary').value = caseDetail?.ai_summary || ticketDetail?.ai_summary || '';
    $('ai-case-type').value = caseDetail?.ai_case_type || caseDetail?.case_type || ticketDetail?.case_type || '';
    $('ai-required-action').value = caseDetail?.ai_suggested_required_action || '';
    $('ai-missing-fields').value = caseDetail?.ai_missing_fields || caseDetail?.missing_fields || '';
    resetCaseEditor(caseDetail?.id || null, nextUpdatedAt);
    return;
  }

  if (state.caseEditor.lastLoadedAt !== nextUpdatedAt) {
    state.caseEditor.remoteChanged = true;
    setSaveHint('检测到远端更新，当前保留你的编辑');
    return;
  }

  setSaveHint('有未保存编辑');
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatDateTime(value) {
  if (!value) return '-';
  try { return new Date(value).toLocaleString(); } catch (_) { return String(value); }
}

const UI_LABELS = { admin: '管理员', manager: '主管', lead: '组长', agent: '客服', auditor: '审计', new: '新建', pending_human: '待人工处理', in_progress: '处理中', waiting_customer: '待客户回复', resolved: '已解决', closed: '已关闭', pending_assignment: '待分配', waiting_internal: '等待内部处理', escalated: '已升级', low: '低优先级', medium: '普通', high: '高优先级', urgent: '紧急', notice: '通知', delay: '延误', disruption: '异常', customs: '清关', customer: '客户', operator: '客服', both: '客户与客服', healthy: '正常', degraded: '受限', offline: '离线', unknown: '未知', ready: '已就绪', not_ready: '待处理', whatsapp: 'WhatsApp', email: '邮箱', web_chat: '网页聊天', info: '普通', warning: '提醒', critical: '紧急', internal: '内部可见' };

function sanitizeDisplayText(value) {
  if (value === undefined || value === null || value === '') return '-';
  return String(value)
    .replace(/OpenClaw/gi, '会话服务')
    .replace(/MCP/gi, '消息桥接')
    .replace(/CLI/gi, '备用通道')
    .replace(/NexusDesk/gi, '客服工作台')
    .replace(/daemon/gi, '守护进程')
    .replace(/runtime/gi, '运行状态');
}

function labelize(value) {
  if (!value) return '-';
  const normalized = String(value).trim().toLowerCase();
  if (UI_LABELS[normalized]) return UI_LABELS[normalized];
  return sanitizeDisplayText(String(value).replaceAll('_', ' ').replace(/\b\w/g, (x) => x.toUpperCase()));
}

function normalizedRole() {
  return String(state.currentUser?.role || '').trim().toLowerCase();
}

function isSupervisorRole() {
  return ['admin', 'manager'].includes(normalizedRole());
}

function canViewOps() {
  return isSupervisorRole();
}

function canManageUsers() {
  return ['admin'].includes(normalizedRole()) || (state.auth?.capabilities || []).includes('user.manage');
}

function canManageChannels() {
  return isSupervisorRole();
}

function canEditBulletins() {
  return isSupervisorRole();
}

function isViewAllowed(view) {
  if (view === 'accounts') return canManageChannels();
  if (view === 'signoff') return canViewOps();
  return true;
}

function visibleViews() {
  return ['overview', 'cases', 'bulletins'].concat(canManageChannels() ? ['accounts'] : []).concat(canViewOps() ? ['signoff'] : []);
}

function setHidden(id, hidden) {
  const el = $(id);
  if (el) el.classList.toggle('hidden', hidden);
}

function setDisabledWithin(selector, disabled) {
  const root = document.querySelector(selector);
  if (!root) return;
  root.querySelectorAll('input, textarea, select, button').forEach((el) => {
    if (el.dataset.keepEnabled === 'true') return;
    el.disabled = disabled;
  });
}

function roleWorkspaceHint() {
  return isSupervisorRole()
    ? '你当前可以查看工单、公告、发送线路与运营保障。'
    : '你当前以客服处理视角工作，重点使用工单处理和公告口径。';
}

function applyRoleAccess() {
  const allowed = new Set(visibleViews());
  $$('.nav-btn').forEach((btn) => btn.classList.toggle('hidden', !allowed.has(btn.dataset.view)));
  setHidden('sidebar-runtime-panel', !canViewOps());
  setHidden('overview-accounts-card', !canManageChannels());
  setHidden('overview-signoff-card', !canViewOps());
  setHidden('overview-runtime-card', !canViewOps());
  setHidden('workspace-ops-card', !canViewOps());
  setHidden('accounts-edit-actions', !canManageChannels());
  setHidden('bulletin-edit-actions', !canEditBulletins());
  setHidden('bulletin-readonly-note', canEditBulletins());
  setHidden('account-readonly-note', canManageChannels());
  setDisabledWithin('#bulletin-editor-form', !canEditBulletins());
  setDisabledWithin('#account-editor-form', !canManageChannels());
  if ($('bulletin-readonly-note') && !canEditBulletins()) {
    $('bulletin-readonly-note').textContent = `你当前角色为 ${labelize(state.currentUser?.role)}，只能查看公告，不能修改；如需调整口径，请联系管理员或运营主管。`;
  }
  if ($('account-readonly-note') && !canManageChannels()) {
    $('account-readonly-note').textContent = `你当前角色为 ${labelize(state.currentUser?.role)}，只能查看发送线路，不能修改。`;
  }
  if (!isViewAllowed(state.activeView)) {
    state.activeView = 'overview';
  }
}

function statusTone(status) {
  const key = String(status || '').toLowerCase();
  if (['resolved', 'closed'].includes(key)) return 'chip chip-success';
  if (['waiting_customer', 'ready_to_reply', 'replied_to_customer'].includes(key)) return 'chip chip-accent';
  if (['high', 'urgent', 'critical'].includes(key)) return 'chip chip-warning';
  if (['human_owned', 'in_progress'].includes(key)) return 'chip chip-brand';
  return 'chip';
}

function safeJson(res) {
  const contentType = res.headers.get('content-type') || '';
  return contentType.includes('application/json') ? res.json() : null;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (!headers['Content-Type'] && options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(`${state.apiBase}${path}`, { ...options, headers });
  if (res.status === 401) {
    logout();
    throw new Error('登录状态已失效');
  }
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const payload = await safeJson(res);
      msg = payload?.detail || JSON.stringify(payload);
    } catch (_) {}
    throw new Error(msg);
  }
  return safeJson(res);
}

async function optionalApi(path) {
  try {
    return await api(path);
  } catch (err) {
    const msg = String(err.message || err);
    if (/403|404/.test(msg)) return null;
    console.warn('legacy optionalApi failed', path, err);
    return null;
  }
}

function logout() {
  clearInterval(state.pollHandle);
  sessionStorage.removeItem('HELPDESK_TOKEN');
  state.token = '';
  state.currentUser = null;
  state.activeView = 'overview';
  resetCaseEditor(null, null);
  $('app').classList.add('hidden');
  $('login-screen').classList.remove('hidden');
}

async function login(username, password) {
  const payload = await api('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  state.token = payload.access_token;
  sessionStorage.setItem('HELPDESK_TOKEN', state.token);
}

function setView(view) {
  if (!isViewAllowed(view)) view = 'overview';
  state.activeView = view;
  $$('.nav-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === view));
  ['overview', 'cases', 'bulletins', 'accounts', 'signoff'].forEach((key) => {
    $(`view-${key}`).classList.toggle('hidden', key !== view);
  });
  const meta = {
    overview: ['首页总览', '客服工作台', '跨市场工单、消息同步、公告和系统状态总览。'],
    cases: ['工单处理', '客服处理工作台', '查看客户消息、附件证据、公告口径和处理动作。'],
    bulletins: ['通知公告', '公告与回复口径中心', '维护客户可用、客服可执行的公告与口径。'],
    accounts: ['渠道设置', '渠道账号与发送线路', '管理不同市场的主账号、备用账号和状态。'],
    signoff: ['运营保障', '发送健康与上线检查', '查看环境准备度、消息处理积压和后台任务情况。'],
  }[view];
  $('page-eyebrow').textContent = meta[0];
  $('page-title').textContent = meta[1];
  $('page-description').textContent = meta[2];
}

function buildQuery() {
  const params = new URLSearchParams();
  if (state.query) params.set('q', state.query);
  if (state.filterStatus) params.set('status', state.filterStatus);
  return params.toString() ? `?${params.toString()}` : '';
}

function diffCases(newCases) {
  const changedIds = new Set();
  const nextMap = new Map();
  newCases.forEach((item) => {
    nextMap.set(item.id, item.last_updated);
    const oldUpdated = state.previousMap.get(item.id);
    if (!oldUpdated || oldUpdated !== item.last_updated) changedIds.add(item.id);
  });
  state.previousMap = nextMap;
  return changedIds;
}

function renderHeadlineMetrics() {
  const total = state.cases.length;
  const open = state.cases.filter((item) => !['resolved', 'closed'].includes(item.status)).length;
  const waiting = state.cases.filter((item) => item.status === 'waiting_customer').length;
  const high = state.cases.filter((item) => ['high', 'urgent'].includes(item.priority)).length;
  $('global-total').textContent = String(total);
  $('global-open').textContent = String(open);
  $('global-waiting').textContent = String(waiting);
  $('global-high-priority').textContent = String(high);
  const countrySet = new Set(state.cases.map((item) => item.country_code).filter(Boolean));
  const accountCount = (state.ops.channelAccounts || []).length;
  $('queue-market').textContent = countrySet.size ? `已覆盖 ${countrySet.size} 个市场` : '全部市场';
  $('sidebar-market-summary').textContent = canManageChannels()
    ? `${countrySet.size || 0} 个市场 · ${accountCount} 条发送线路`
    : `${countrySet.size || 0} 个市场 · 公告 ${state.ops.bulletins.length || 0} 条`;
  $('sidebar-role-hint').textContent = roleWorkspaceHint();
}

function renderInfoList(targetId, rows, emptyText = '暂无数据。') {
  const el = $(targetId);
  if (!el) return;
  if (!rows || !rows.length) {
    replaceNodeChildren(el, createEmptyState(emptyText, 'empty-inline'));
    return;
  }
  replaceNodeChildren(el, rows.map((row) => createNode('div', { className: 'ops-item' }, [
    createNode('span', { text: row.label }),
    createNode('strong', { text: String(labelize(row.value)) }),
  ])));
}

function renderArrayCards(targetId, items, emptyText, mapFn) {
  const el = $(targetId);
  if (!el) return;
  if (!items || !items.length) {
    replaceNodeChildren(el, createEmptyState(emptyText));
    return;
  }
  replaceNodeChildren(el, items.map((item) => mapFn(item)));
}

function findUserIdByName(displayName) {
  const user = state.users.find((u) => u.display_name === displayName);
  return user ? user.id : '';
}

function findMarketName(marketId) {
  const market = state.markets.find((m) => m.id === marketId);
  return market ? `${market.code} · ${market.name}` : '全局';
}

function populateMarketSelect(selectId, includeEmpty = true) {
  const select = $(selectId);
  if (!select) return;
  const options = [];
  if (includeEmpty) options.push(createOption('', '全部市场 / 不区分市场'));
  state.markets.forEach((m) => {
    options.push(createOption(m.id, `${m.code} · ${m.name}`));
  });
  replaceNodeChildren(select, options);
}

function renderAssigneeSelect() {
  const select = $('assign-user');
  if (!select) return;
  replaceNodeChildren(select, [
    createOption('', '未分配'),
    ...state.users.map((u) => createOption(u.id, u.display_name)),
  ]);
}

async function bootstrap() {
  state.currentUser = await api('/auth/me');
  $('current-user').textContent = `${state.currentUser.display_name} · ${labelize(state.currentUser.role)}`;
  $('sidebar-role-hint').textContent = roleWorkspaceHint();
  const meta = await api('/lite/meta');
  state.users = meta.users || [];
  state.teams = meta.teams || [];
  state.statuses = meta.statuses || [];
  state.priorities = meta.priorities || [];
  state.markets = await api('/lookups/markets');
  renderAssigneeSelect();
  populateMarketSelect('bulletin-market');
  populateMarketSelect('account-market');
  applyRoleAccess();
  $('login-screen').classList.add('hidden');
  $('app').classList.remove('hidden');
  setView('overview');
  await refreshAll(true);
  clearInterval(state.pollHandle);
  state.pollHandle = setInterval(() => refreshAll(false).catch(console.error), 10000);
}

async function refreshAll(selectFirst = false) {
  await Promise.all([loadOpsData(), loadCases(selectFirst)]);
  renderOverview();
  renderSignoff();
  renderBulletinCenter();
  renderAccountCenter();
}

async function loadCases(selectFirst = false, silent = false) {
  const list = await api(`/lite/cases${buildQuery()}`);
  const changedIds = diffCases(list);
  const hadSelected = !!state.selectedCaseId;
  state.cases = list;
  renderHeadlineMetrics();
  renderCaseList(changedIds);
  if (!hadSelected && selectFirst && list.length) {
    await selectCase(list[0].id);
  } else if (state.selectedCaseId) {
    const exists = list.find((item) => item.id === state.selectedCaseId);
    if (exists) {
      await loadCaseDetail(state.selectedCaseId, false);
    } else {
      state.selectedCaseId = null;
      state.selectedCase = null;
      state.selectedTicket = null;
      resetCaseEditor(null, null);
      $('case-detail').classList.add('hidden');
      $('detail-empty').classList.remove('hidden');
    }
  }
  if (changedIds.size && !selectFirst && !silent) showToast(changedIds.size > 1 ? '工单队列已刷新' : '工单已更新');
}

function renderCaseList(changedIds = new Set()) {
  $('queue-count').textContent = String(state.cases.length);
  const wrap = $('case-list');
  if (!wrap) return;
  if (!state.cases.length) {
    replaceNodeChildren(wrap, []);
    $('case-list-empty').classList.remove('hidden');
    return;
  }
  $('case-list-empty').classList.add('hidden');
  const cards = state.cases.map((item) => {
    const selected = item.id === state.selectedCaseId ? 'selected' : '';
    const flash = changedIds.has(item.id) ? 'flash' : '';
    const market = item.country_code || '全局';
    const article = createNode('article', { className: `case-card ${selected} ${flash}`.trim(), dataset: { caseId: item.id } }, [
      createNode('div', { className: 'case-top' }, [
        createNode('div', {}, [
          createNode('div', { className: 'case-title', text: item.issue_summary || item.case_type || item.case }),
          createNode('div', { className: 'case-code', text: `${item.case} · ${market}` }),
        ]),
        createNode('span', { className: statusTone(item.status), text: labelize(item.status) }),
      ]),
      createNode('div', { className: 'case-meta' }, [
        createNode('span', { className: 'chip chip-brand', text: labelize(item.priority) }),
        createNode('span', { className: 'chip', text: item.assigned_to || '未分配' }),
      ]),
      createNode('div', { className: 'case-extra' }, [
        createNode('span', { text: `运单号：${item.tracking_number || '-'}` }),
        createNode('span', { text: `联系方式：${item.customer_contact || '-'}` }),
        createNode('span', { text: `更新时间：${formatDateTime(item.last_updated)}` }),
      ]),
    ]);
    article.addEventListener('click', () => selectCase(Number(item.id)));
    return article;
  });
  replaceNodeChildren(wrap, cards);
}

async function selectCase(caseId) {
  if (state.caseEditor.dirty && state.selectedCaseId && state.selectedCaseId !== caseId) {
    const confirmed = window.confirm('当前工单有未保存编辑，切换后会丢失这些修改。确定继续切换吗？');
    if (!confirmed) return;
  }
  state.selectedCaseId = caseId;
  renderCaseList();
  await loadCaseDetail(caseId, true);
}

async function loadCaseDetail(caseId, switchMode = true) {
  const [liteCase, ticket] = await Promise.all([
    api(`/lite/cases/${caseId}`),
    optionalApi(`/tickets/${caseId}`),
  ]);
  state.selectedCase = liteCase;
  state.selectedTicket = ticket;
  renderCaseDetail();
  if (switchMode && !state.caseEditor.dirty) setSaveHint('就绪');
}

async function createCase() {
  const payload = {
    case_type: $('new-case-type').value.trim() || null,
    issue_summary: $('new-issue-summary').value.trim(),
    customer_request: $('new-customer-request').value.trim(),
    customer_name: $('new-customer-name').value.trim() || null,
    customer_contact: $('new-customer-contact').value.trim() || null,
    tracking_number: $('new-tracking-number').value.trim() || null,
    channel: $('new-channel').value || 'whatsapp',
    source_chat_id: $('new-source-chat-id').value.trim() || null,
    ai_summary: $('new-ai-summary').value.trim() || null,
    ai_case_type: $('new-case-type').value.trim() || null,
    last_customer_message: $('new-customer-request').value.trim() || null,
  };

  if (!payload.issue_summary || !payload.customer_request) {
    showToast('问题摘要和客户诉求不能为空', true);
    return;
  }

  const result = await api('/lite/cases', { method: 'POST', body: JSON.stringify(payload) });
  closeModal();
  $('new-case-type').value = '';
  $('new-issue-summary').value = '';
  $('new-customer-request').value = '';
  $('new-customer-name').value = '';
  $('new-customer-contact').value = '';
  $('new-tracking-number').value = '';
  $('new-channel').value = 'whatsapp';
  $('new-source-chat-id').value = '';
  $('new-ai-summary').value = '';
  showToast(result.action === 'updated' ? '已更新现有工单' : '工单已创建');
  await loadCases(false, true);
  await selectCase(result.case.id);
}

async function saveCase() {
  if (!state.selectedCaseId) {
    showToast('请先选择工单', true);
    return;
  }

  state.caseEditor.saving = true;
  setSaveHint('保存中');

  try {
    const payload = {
      missing_fields: $('edit-missing-fields').value.trim() || null,
      required_action: $('edit-required-action').value.trim() || null,
      customer_update: $('edit-customer-update').value.trim() || null,
      resolution_summary: $('edit-resolution-summary').value.trim() || null,
      assignee_id: $('assign-user').value ? Number($('assign-user').value) : null,
      status: $('status-select').value || null,
      human_note: $('human-note').value.trim() || null,
    };
    const updated = await api(`/lite/cases/${state.selectedCaseId}/workflow-update`, { method: 'POST', body: JSON.stringify(payload) });
    state.selectedCase = updated;
    resetCaseEditor(updated.id, updated.last_updated);
    showToast('工单处理已保存');
    await loadCases(false, true);
    await loadCaseDetail(state.selectedCaseId, false);
  } catch (err) {
    state.caseEditor.saving = false;
    state.caseEditor.dirty = true;
    setSaveHint(state.caseEditor.remoteChanged ? '检测到远端更新，当前保留你的编辑' : '保存失败，仍有未保存编辑');
    throw err;
  }
}

async function saveAiIntake() {
  if (!state.selectedCaseId) {
    showToast('请先选择工单', true);
    return;
  }

  state.caseEditor.saving = true;
  setSaveHint('保存中');

  try {
    const payload = {
      ai_summary: $('ai-summary').value.trim() || null,
      case_type: $('ai-case-type').value.trim() || null,
      suggested_required_action: $('ai-required-action').value.trim() || null,
      missing_fields: $('ai-missing-fields').value.trim() || null,
      last_customer_message: state.selectedCase?.last_customer_message || null,
    };
    const updated = await api(`/lite/cases/${state.selectedCaseId}/ai-intake`, { method: 'POST', body: JSON.stringify(payload) });
    state.selectedCase = updated;
    resetCaseEditor(updated.id, updated.last_updated);
    showToast('智能提炼已保存');
    await loadCases(false, true);
    await loadCaseDetail(state.selectedCaseId, false);
  } catch (err) {
    state.caseEditor.saving = false;
    state.caseEditor.dirty = true;
    setSaveHint('保存失败，仍有未保存编辑');
    throw err;
  }
}

function setText(id, value) {
  $(id).textContent = value || '-';
}

function renderTranscript(transcript) {
  const el = $('detail-transcript');
  if (!el) return;
  if (!transcript || !transcript.length) {
    replaceNodeChildren(el, createEmptyState('当前还没有同步到客户消息记录。'));
    return;
  }
  replaceNodeChildren(el, transcript.slice(-20).map((msg) => createNode('article', { className: 'transcript-item' }, [
    createNode('div', { className: 'transcript-head' }, [
      createNode('span', { className: 'transcript-role', text: labelize(msg.role || 'unknown') }),
      createNode('span', { className: 'transcript-time', text: formatDateTime(msg.received_at || msg.created_at) }),
    ]),
    createNode('div', { className: 'transcript-author', text: msg.author_name || '-' }),
    createNode('div', { className: 'text-block', text: msg.body_text || '-' }),
  ])));
}

function renderBulletinsInline(targetId, bulletins, emptyText = '当前没有生效公告。') {
  const el = $(targetId);
  if (!el) return;
  if (!bulletins || !bulletins.length) {
    replaceNodeChildren(el, createEmptyState(emptyText));
    return;
  }
  replaceNodeChildren(el, bulletins.map((item) => {
    const severityClass = item.severity === 'critical' ? 'chip chip-danger' : item.severity === 'warning' ? 'chip chip-warning' : 'chip chip-accent';
    return createNode('article', { className: 'notice-item' }, [
      createNode('div', { className: 'notice-head' }, [
        createNode('strong', { text: item.title }),
        createNode('span', { className: severityClass, text: labelize(item.severity || 'info') }),
      ]),
      createNode('div', { className: 'notice-meta', text: `${item.category || 'notice'} · ${item.audience || 'customer'} · ${findMarketName(item.market_id)}` }),
      createNode('div', { className: 'text-block', text: item.summary || item.body || '-' }),
    ]);
  }));
}

function renderEvidence(ticket) {
  const systemAttachments = ticket?.attachments || [];
  const openclawRefs = ticket?.openclaw_attachment_references || [];
  $('detail-attachments-count').textContent = String(systemAttachments.length + openclawRefs.length);
  const el = $('detail-evidence');
  if (!el) return;
  if (!systemAttachments.length && !openclawRefs.length) {
    replaceNodeChildren(el, createEmptyState('当前还没有附件或聊天证据。'));
    return;
  }
  const rows = [];
  systemAttachments.forEach((item) => rows.push({
    title: item.file_name || '附件',
    meta: `系统附件 · ${item.visibility || 'internal'}`,
    body: item.mime_type || '文件',
  }));
  openclawRefs.forEach((item) => rows.push({
    title: item.filename || item.remote_attachment_id,
    meta: `会话服务 evidence · ${item.storage_status}`,
    body: item.content_type || 'unknown type',
  }));
  replaceNodeChildren(el, rows.map((row) => createNode('article', { className: 'evidence-item' }, [
    createNode('div', { className: 'evidence-head' }, [
      createNode('strong', { text: row.title }),
      createNode('span', { className: 'evidence-meta', text: row.meta }),
    ]),
    createNode('div', { text: row.body }),
  ])));
}

function renderCaseDetail() {
  const c = state.selectedCase;
  const t = state.selectedTicket;
  if (!c) return;
  $('detail-empty').classList.add('hidden');
  $('case-detail').classList.remove('hidden');

  $('detail-case').textContent = `${c.case} · ${c.case_type || '工单'}`;
  $('detail-meta').textContent = `更新时间 ${formatDateTime(c.last_updated)} · 当前处理人 ${c.assigned_to || '未分配'}`;
  $('detail-status').className = statusTone(c.status);
  $('detail-status').textContent = labelize(c.status);
  $('detail-priority').className = 'chip chip-brand';
  $('detail-priority').textContent = labelize(c.priority);
  $('detail-market').className = 'chip';
  $('detail-market').textContent = t?.market_code || c.country_code || '全局';
  $('detail-conversation-state').className = 'chip chip-accent';
  $('detail-conversation-state').textContent = labelize(t?.conversation_state || 'no_conversation_state');

  setText('detail-customer-name', c.customer_name || t?.customer?.name);
  setText('detail-customer-contact', c.customer_contact || t?.customer?.phone || t?.customer?.email);
  setText('detail-tracking', c.tracking_number || t?.tracking_number);
  setText('detail-channel', labelize(c.channel || t?.source_channel));
  setText('detail-destination', c.destination || t?.destination);
  setText('detail-requested-time', c.requested_time || t?.requested_time);
  setText('detail-source-chat-id', canViewOps() ? (c.source_chat_id || t?.source_chat_id) : '一线客服无需查看');
  setText('detail-issue-summary', c.issue_summary || t?.issue_summary || t?.title);
  setText('detail-customer-request', c.customer_request || t?.customer_request || t?.description);
  setText('detail-last-customer-message', c.last_customer_message || t?.last_customer_message);
  setText('detail-reply-path', t?.preferred_reply_channel ? `${labelize(t.preferred_reply_channel)} → ${t.preferred_reply_contact || '-'}` : `${labelize(c.channel)} → ${c.customer_contact || '-'}`);

  syncCaseEditorFromSelected(c, t);

  renderInfoList('detail-openclaw-route', [
    { label: '来源状态', value: t?.openclaw_conversation ? '已绑定来信来源' : '未绑定' },
    { label: '渠道', value: t?.openclaw_conversation?.channel || c.channel || '-' },
    { label: '联系对象', value: t?.openclaw_conversation?.recipient || c.customer_contact || '-' },
    { label: '发送线路', value: t?.openclaw_conversation?.account_id || '-' },
    { label: '最近同步', value: formatDateTime(t?.openclaw_conversation?.last_synced_at) },
  ]);
  renderTranscript(t?.openclaw_transcript || []);
  renderBulletinsInline('detail-bulletins', t?.active_market_bulletins || []);
  renderEvidence(t);
}

async function loadOpsData() {
  const supervisor = canViewOps();
  const [queueSummary, runtimeHealth, signoff, readiness, bulletins, channelAccounts, jobs] = await Promise.all([
    supervisor ? optionalApi('/admin/queues/summary') : null,
    supervisor ? optionalApi('/admin/openclaw/runtime-health') : null,
    supervisor ? optionalApi('/admin/signoff-checklist') : null,
    supervisor ? optionalApi('/admin/production-readiness') : null,
    api('/lookups/bulletins'),
    canManageChannels() ? optionalApi('/admin/channel-accounts') : null,
    supervisor ? optionalApi('/admin/jobs?limit=25') : null,
  ]);
  state.ops = {
    queueSummary,
    runtimeHealth,
    signoff,
    readiness,
    bulletins: bulletins || [],
    channelAccounts: channelAccounts || [],
    jobs: jobs || [],
  };
  renderOpsPanels();
}

function renderOpsPanels() {
  const { queueSummary, runtimeHealth, signoff, bulletins, channelAccounts } = state.ops;
  const supervisor = canViewOps();
  renderInfoList('ops-queue-summary', queueSummary ? [
    { label: '待发送消息', value: queueSummary.pending_outbound },
    { label: '待处理任务', value: queueSummary.pending_jobs },
    { label: '异常任务', value: queueSummary.dead_jobs },
    { label: '已绑定会话', value: queueSummary.openclaw_links },
  ] : [], supervisor ? '当前账号暂时无法读取任务汇总。' : '当前账号无需查看任务汇总。');

  renderInfoList('ops-runtime-health', runtimeHealth ? [
    { label: '同步游标', value: runtimeHealth.sync_cursor || '-' },
    { label: '最近心跳', value: formatDateTime(runtimeHealth.sync_daemon_last_seen_at) },
    { label: '待补同步', value: runtimeHealth.stale_link_count },
    { label: '待执行同步任务', value: runtimeHealth.pending_sync_jobs },
    { label: '待处理附件任务', value: runtimeHealth.pending_attachment_jobs || 0 },
  ] : [], supervisor ? '消息同步状态暂不可用。' : '当前账号无需查看消息同步状态。');

  renderInfoList('ops-signoff', signoff ? [
    { label: '当前状态', value: labelize(signoff.status) },
    ...Object.entries(signoff.checks || {}).slice(0, 5).map(([key, val]) => ({ label: labelize(key), value: val ? '通过' : '未通过' })),
  ] : [], supervisor ? '上线检查数据暂不可用。' : '当前账号无需查看上线检查。');

  renderInfoList('ops-bulletins', bulletins.length ? [
    { label: '公告条数', value: bulletins.length },
    ...(bulletins.slice(0, 3).map((item) => ({ label: item.country_code || item.market_id || '全局', value: item.title }))),
  ] : [], '当前未读取到公告数据。');

  renderInfoList('ops-channel-accounts', canManageChannels() && channelAccounts.length ? [
    { label: '已配置账号', value: channelAccounts.length },
    ...(channelAccounts.slice(0, 3).map((item) => ({ label: item.display_name || item.account_id, value: `${labelize(item.provider)} · ${labelize(item.health_status)}` }))),
  ] : [], canManageChannels() ? '当前未读取到发送线路数据。' : '当前账号无需查看发送线路数据。');

  const sidebarParts = [];
  if (runtimeHealth) sidebarParts.push(`同步游标: ${runtimeHealth.sync_cursor || '-'}`);
  if (runtimeHealth) sidebarParts.push(`待补同步: ${runtimeHealth.stale_link_count}`);
  $('sidebar-runtime').textContent = canViewOps() ? (sidebarParts.length ? sidebarParts.join(' · ') : '系统状态数据不可用') : '当前账号无需查看运营保障；如遇发送异常，请联系主管。';
}

function renderOverview() {
  const q = state.ops.queueSummary || {};
  const r = state.ops.runtimeHealth || {};
  const supervisor = canViewOps();
  const caseStats = {
    total: state.cases.length,
    inProgress: state.cases.filter((item) => item.status === 'in_progress').length,
    waitingCustomer: state.cases.filter((item) => item.status === 'waiting_customer').length,
    highPriority: state.cases.filter((item) => ['high', 'urgent'].includes(item.priority)).length,
    activeBulletins: (state.ops.bulletins || []).filter((item) => item.is_active).length,
    assigned: state.cases.filter((item) => item.assigned_to).length,
    unassigned: state.cases.filter((item) => !item.assigned_to).length,
    resolved: state.cases.filter((item) => item.status === 'resolved').length,
  };
  const metricLabels = supervisor
    ? ['待发送消息', '待处理任务', '异常任务', '已绑定会话', '待补同步', '待执行同步任务', '附件任务', '渠道设置']
    : ['当前工单', '处理中', '待客户回复', '高优先级', '生效公告', '已分配工单', '待分配工单', '已解决'];
  const metricValues = supervisor
    ? [q.pending_outbound ?? 0, q.pending_jobs ?? 0, q.dead_jobs ?? 0, q.openclaw_links ?? 0, r.stale_link_count ?? 0, r.pending_sync_jobs ?? 0, r.pending_attachment_jobs ?? 0, (state.ops.channelAccounts || []).length]
    : [caseStats.total, caseStats.inProgress, caseStats.waitingCustomer, caseStats.highPriority, caseStats.activeBulletins, caseStats.assigned, caseStats.unassigned, caseStats.resolved];

  [
    'overview-pending-outbound',
    'overview-pending-jobs',
    'overview-dead-jobs',
    'overview-openclaw-links',
    'overview-stale-links',
    'overview-pending-sync',
    'overview-pending-attachments',
    'overview-channel-accounts',
  ].forEach((id, idx) => {
    $(id).textContent = String(metricValues[idx] ?? 0);
    $(`overview-metric-label-${idx + 1}`).textContent = metricLabels[idx];
  });

  renderInfoList('overview-signoff', supervisor && state.ops.signoff ? [
    { label: '当前状态', value: labelize(state.ops.signoff.status) },
    ...Object.entries(state.ops.signoff.checks || {}).slice(0, 4).map(([key, val]) => ({ label: labelize(key), value: val ? '通过' : '未通过' })),
  ] : [], supervisor ? '上线检查数据暂不可用。' : '当前账号无需查看上线检查。');
  renderInfoList('overview-runtime', supervisor && state.ops.runtimeHealth ? [
    { label: '同步游标', value: state.ops.runtimeHealth.sync_cursor || '-' },
    { label: '最近心跳', value: formatDateTime(state.ops.runtimeHealth.sync_daemon_last_seen_at) },
    { label: '待补同步', value: state.ops.runtimeHealth.stale_link_count },
    { label: '提醒项', value: (state.ops.runtimeHealth.warnings || []).length },
  ] : [], supervisor ? '当前未读取到运行状态数据。' : '当前账号无需查看系统运行状态。');
  renderBulletinsInline('overview-bulletins', state.ops.bulletins.slice(0, 6), '当前没有加载到生效公告。');
  renderInfoList('overview-accounts', canManageChannels() ? (state.ops.channelAccounts || []).slice(0, 6).map((item) => ({ label: item.display_name || item.account_id, value: `${labelize(item.provider)} · ${labelize(item.health_status)}` })) : [], canManageChannels() ? '当前没有渠道账号记录。' : '当前账号无需查看发送线路配置。');
}

function renderBulletinCenter() {
  const rows = state.ops.bulletins || [];
  renderArrayCards('bulletin-list', rows, '当前没有公告。', (item) => {
    const card = createNode('article', { className: `list-card ${item.id === state.selectedBulletinId ? 'selected' : ''}`.trim(), dataset: { bulletinId: item.id } }, [
      createNode('div', { className: 'list-card-top' }, [
        createNode('strong', { text: item.title }),
        createNode('span', { className: item.is_active ? 'chip chip-success' : 'chip', text: item.is_active ? '生效中' : '已停用' }),
      ]),
      createNode('div', { className: 'list-card-meta', text: `${findMarketName(item.market_id)} · ${item.country_code || '-'} · ${labelize(item.category)}` }),
      createNode('div', { className: 'list-card-copy', text: item.summary || item.body || '-' }),
      createNode('div', { className: 'list-card-meta', text: `${labelize(item.audience)} · ${labelize(item.severity)} · 智能助手 ${item.auto_inject_to_ai ? '可引用' : '不可引用'}` }),
    ]);
    card.addEventListener('click', () => selectBulletin(Number(item.id)));
    return card;
  });
}

function selectBulletin(id) {
  state.selectedBulletinId = id;
  const row = (state.ops.bulletins || []).find((item) => item.id === id);
  if (!row) return;
  $('bulletin-id').value = row.id;
  $('bulletin-title').value = row.title || '';
  $('bulletin-summary').value = row.summary || '';
  $('bulletin-body').value = row.body || '';
  $('bulletin-market').value = row.market_id || '';
  $('bulletin-country-code').value = row.country_code || '';
  $('bulletin-category').value = row.category || 'notice';
  $('bulletin-audience').value = row.audience || 'customer';
  $('bulletin-severity').value = row.severity || 'info';
  $('bulletin-channels').value = row.channels_csv || '';
  $('bulletin-auto-inject').checked = !!row.auto_inject_to_ai;
  $('bulletin-active').checked = !!row.is_active;
  renderBulletinCenter();
}

function clearBulletinForm() {
  state.selectedBulletinId = null;
  $('bulletin-id').value = '';
  $('bulletin-title').value = '';
  $('bulletin-summary').value = '';
  $('bulletin-body').value = '';
  $('bulletin-market').value = '';
  $('bulletin-country-code').value = '';
  $('bulletin-category').value = 'notice';
  $('bulletin-audience').value = 'customer';
  $('bulletin-severity').value = 'info';
  $('bulletin-channels').value = '';
  $('bulletin-auto-inject').checked = true;
  $('bulletin-active').checked = true;
  renderBulletinCenter();
}

async function saveBulletin() {
  if (!canEditBulletins()) {
    showToast('你当前只能查看公告，不能修改公告', true);
    return;
  }

  const id = $('bulletin-id').value;
  const payload = {
    title: $('bulletin-title').value.trim(),
    summary: $('bulletin-summary').value.trim() || null,
    body: $('bulletin-body').value.trim(),
    market_id: $('bulletin-market').value ? Number($('bulletin-market').value) : null,
    country_code: $('bulletin-country-code').value.trim() || null,
    category: $('bulletin-category').value.trim() || 'notice',
    audience: $('bulletin-audience').value,
    severity: $('bulletin-severity').value,
    channels_csv: $('bulletin-channels').value.trim() || null,
    auto_inject_to_ai: $('bulletin-auto-inject').checked,
    is_active: $('bulletin-active').checked,
  };
  if (!payload.title || !payload.body) {
    showToast('公告标题和详细内容不能为空', true);
    return;
  }
  if (id) {
    await api(`/admin/bulletins/${id}`, { method: 'PATCH', body: JSON.stringify(payload) });
    showToast('公告已更新');
  } else {
    await api('/admin/bulletins', { method: 'POST', body: JSON.stringify(payload) });
    showToast('公告已创建');
  }
  await loadOpsData();
  renderBulletinCenter();
  clearBulletinForm();
}

function renderAccountCenter() {
  const rows = state.ops.channelAccounts || [];
  renderArrayCards('account-list', rows, '当前没有渠道账号。', (item) => {
    const metaText = `健康状态：${labelize(item.health_status)}${item.fallback_account_id ? ` · 备用线路：${item.fallback_account_id}` : ''}`;
    const card = createNode('article', { className: `list-card ${item.id === state.selectedAccountId ? 'selected' : ''}`.trim(), dataset: { accountRowId: item.id } }, [
      createNode('div', { className: 'list-card-top' }, [
        createNode('strong', { text: item.display_name || item.account_id }),
        createNode('span', { className: item.is_active ? 'chip chip-success' : 'chip', text: item.is_active ? '启用中' : '已停用' }),
      ]),
      createNode('div', { className: 'list-card-meta', text: `${labelize(item.provider)} · ${findMarketName(item.market_id)}` }),
      createNode('div', { className: 'list-card-copy', text: `线路编号：${item.account_id} · 优先级：${item.priority}` }),
      createNode('div', { className: 'list-card-meta', text: metaText }),
    ]);
    card.addEventListener('click', () => selectAccount(Number(item.id)));
    return card;
  });
}

function selectAccount(id) {
  state.selectedAccountId = id;
  const row = (state.ops.channelAccounts || []).find((item) => item.id === id);
  if (!row) return;
  $('account-id').value = row.id;
  $('account-provider').value = row.provider || 'whatsapp';
  $('account-account-id').value = row.account_id || '';
  $('account-display-name').value = row.display_name || '';
  $('account-market').value = row.market_id || '';
  $('account-priority').value = row.priority ?? 100;
  $('account-health-status').value = row.health_status || 'unknown';
  $('account-fallback-id').value = row.fallback_account_id || '';
  $('account-active').checked = !!row.is_active;
  renderAccountCenter();
}

function clearAccountForm() {
  state.selectedAccountId = null;
  $('account-id').value = '';
  $('account-provider').value = 'whatsapp';
  $('account-account-id').value = '';
  $('account-display-name').value = '';
  $('account-market').value = '';
  $('account-priority').value = 100;
  $('account-health-status').value = 'unknown';
  $('account-fallback-id').value = '';
  $('account-active').checked = true;
  renderAccountCenter();
}

async function saveChannelAccount() {
  if (!canManageChannels()) {
    showToast('你当前不能修改发送线路，请联系主管或管理员', true);
    return;
  }

  const id = $('account-id').value;
  const payload = {
    provider: $('account-provider').value.trim() || 'whatsapp',
    account_id: $('account-account-id').value.trim(),
    display_name: $('account-display-name').value.trim() || null,
    market_id: $('account-market').value ? Number($('account-market').value) : null,
    priority: Number($('account-priority').value || 100),
    health_status: $('account-health-status').value,
    fallback_account_id: $('account-fallback-id').value.trim() || null,
    is_active: $('account-active').checked,
  };
  if (!payload.account_id) {
    showToast('必须填写账号编号', true);
    return;
  }
  if (id) {
    await api(`/admin/channel-accounts/${id}`, { method: 'PATCH', body: JSON.stringify(payload) });
    showToast('发送线路已更新');
  } else {
    const createPayload = { ...payload };
    delete createPayload.health_status;
    delete createPayload.is_active;
    await api('/admin/channel-accounts', { method: 'POST', body: JSON.stringify(createPayload) });
    showToast('发送线路已创建');
  }
  await loadOpsData();
  renderAccountCenter();
  clearAccountForm();
}

function renderSignoff() {
  const signoff = state.ops.signoff;
  const readiness = state.ops.readiness;
  const runtime = state.ops.runtimeHealth;
  const jobs = state.ops.jobs || [];
  const summary = $('signoff-summary');
  if (summary) {
    if (signoff) {
      replaceNodeChildren(summary, [
        createNode('div', { className: `summary-badge ${signoff.status === 'ready' ? 'summary-ready' : 'summary-warn'}`, text: labelize(signoff.status) }),
        createNode('div', { className: 'summary-copy', text: readiness?.warnings?.length ? `还有 ${readiness.warnings.length} 条提醒需要处理。` : '当前上线检查没有明显阻塞项。' }),
      ]);
    } else {
      replaceNodeChildren(summary, createEmptyState('运营保障数据暂不可用。'));
    }
  }
  renderArrayCards('signoff-checks', signoff ? Object.entries(signoff.checks || {}).map(([key, value]) => ({ key, value })) : [], '当前没有上线检查项目。', (item) => createNode('article', { className: `list-card compact ${item.value ? 'positive' : 'negative'}` }, [
    createNode('div', { className: 'list-card-top' }, [
      createNode('strong', { text: labelize(item.key) }),
      createNode('span', { className: item.value ? 'chip chip-success' : 'chip chip-warning', text: item.value ? '通过' : '复核' }),
    ]),
  ]));
  renderBulletinsInline('signoff-warnings', (readiness?.warnings || []).map((warning, idx) => ({ id: idx, title: '提醒', summary: warning, severity: 'warning', category: 'readiness', audience: 'operator' })), '上线检查当前没有提醒。');
  renderInfoList('signoff-runtime', runtime ? [
    { label: '同步游标', value: runtime.sync_cursor || '-' },
    { label: '最近心跳', value: formatDateTime(runtime.sync_daemon_last_seen_at) },
    { label: '待补同步', value: runtime.stale_link_count },
    { label: '待执行同步任务', value: runtime.pending_sync_jobs },
    { label: '待处理附件任务', value: runtime.pending_attachment_jobs || 0 },
  ] : [], '运营保障数据不可用。');
  renderArrayCards('jobs-list', jobs.slice(0, 16), '当前没有最近任务。', (job) => createNode('article', { className: 'list-card compact' }, [
    createNode('div', { className: 'list-card-top' }, [
      createNode('strong', { text: sanitizeDisplayText(job.job_type) }),
      createNode('span', { className: statusTone(job.status), text: labelize(job.status) }),
    ]),
    createNode('div', { className: 'list-card-meta', text: `任务队列：${job.queue_name} · 尝试次数：${job.attempt_count} / ${job.max_attempts}` }),
    createNode('div', { className: 'list-card-copy', text: job.last_error || '无错误信息' }),
  ]));
}


function openUserModal() { $('new-user-modal').classList.remove('hidden'); }
function closeUserModal() {
  $('new-user-modal').classList.add('hidden');
  $('new-user-username').value = '';
  $('new-user-password').value = '';
  $('new-user-display-name').value = '';
  $('new-user-email').value = '';
  $('new-user-role').value = 'agent';
}

async function handleCreateUser() {
  const username = $('new-user-username').value.trim();
  const password = $('new-user-password').value.trim();
  const displayName = $('new-user-display-name').value.trim();
  const email = $('new-user-email').value.trim() || null;
  const role = $('new-user-role').value;

  if (!username || !password || !displayName) {
    alert('请填写完整的必填项（用户名、密码、显示名称）');
    return;
  }
  if (password.length < 6) {
    alert('初始密码请至少设置6位');
    return;
  }

  $('create-user-btn').disabled = true;
  $('create-user-btn').textContent = '开通中...';

  try {
    const res = await apiCall('/api/admin/users', 'POST', { username, password, display_name: displayName, email, role });
    if (res.id) {
      alert(`账号 ${displayName} (@${username}) 开通成功！\n角色: ${role}`);
      closeUserModal();
    } else {
      alert('开通失败，请检查填写内容或重试。');
    }
  } catch (err) {
    alert(err.message || '无法开通账号');
  } finally {
    $('create-user-btn').disabled = false;
    $('create-user-btn').textContent = '确认开通';
  }
}

function openModal() { $('new-case-modal').classList.remove('hidden'); }
function closeModal() { $('new-case-modal').classList.add('hidden'); }
function openCommandPalette() { $('command-palette').classList.remove('hidden'); $('command-search').value = ''; state.commandFilter = ''; renderCommandPalette(); $('command-search').focus(); }
function closeCommandPalette() { $('command-palette').classList.add('hidden'); }

function commandEntries() {
  return [
    { key: 'overview', title: '前往首页总览', group: '导航', run: () => setView('overview') },
    { key: 'cases', title: '前往工单处理', group: '导航', run: () => setView('cases') },
    { key: 'bulletins', title: '前往通知公告', group: '导航', run: () => setView('bulletins') },
    ...(canManageChannels() ? [{ key: 'accounts', title: '前往渠道设置', group: '导航', run: () => setView('accounts') }] : []),
    ...(canViewOps() ? [{ key: 'signoff', title: '前往运营保障', group: '导航', run: () => setView('signoff') }] : []),
    { key: 'refresh', title: '刷新全部数据', group: '操作', run: () => refreshAll(false) },
    { key: 'new-case', title: '新建工单', group: '操作', run: () => openModal() },
    ...(canEditBulletins() ? [{ key: 'new-bulletin', title: '新建公告', group: '操作', run: () => { setView('bulletins'); clearBulletinForm(); } }] : []),
    ...(canManageChannels() ? [{ key: 'new-account', title: '新建渠道账号', group: '操作', run: () => { setView('accounts'); clearAccountForm(); } }] : []),
    ...(canManageUsers() ? [{ key: 'new-user', title: '新增员工账号', group: '系统', run: () => openUserModal() }] : []),
  ];
}

function renderCommandPalette() {
  const q = state.commandFilter.trim().toLowerCase();
  const items = commandEntries().filter((item) => !q || `${item.title} ${item.group}`.toLowerCase().includes(q));
  const el = $('command-list');
  if (!el) return;
  if (!items.length) {
    replaceNodeChildren(el, createEmptyState('没有匹配的操作。'));
    return;
  }
  const buttons = items.map((item, idx) => {
    const btn = createNode('button', { className: 'command-item', attrs: { type: 'button' }, dataset: { commandIndex: idx } }, [
      createNode('span', { text: item.title }),
      createNode('span', { className: 'command-group', text: item.group }),
    ]);
    btn.addEventListener('click', () => {
      const action = items[idx];
      closeCommandPalette();
      Promise.resolve(action.run()).catch((err) => showToast(err.message || String(err), true));
    });
    return btn;
  });
  replaceNodeChildren(el, buttons);
}

function bindEvents() {
  $('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await login($('login-username').value.trim(), $('login-password').value);
      $('login-error').classList.add('hidden');
      await bootstrap();
    } catch (err) {
      $('login-error').textContent = err.message || String(err);
      $('login-error').classList.remove('hidden');
    }
  });

  $('refresh-btn').addEventListener('click', () => refreshAll(false).catch((err) => showToast(err.message || String(err), true)));
  $('new-case-btn').addEventListener('click', openModal);
  $('close-modal-btn').addEventListener('click', closeModal);
  $('close-user-modal-btn')?.addEventListener('click', closeUserModal);
  $('create-user-btn')?.addEventListener('click', handleCreateUser);
  $('create-case-btn').addEventListener('click', () => createCase().catch((err) => showToast(err.message || String(err), true)));
  $('logout-btn').addEventListener('click', logout);
  $('save-case-btn').addEventListener('click', () => saveCase().catch((err) => showToast(err.message || String(err), true)));
  $('save-ai-btn').addEventListener('click', () => saveAiIntake().catch((err) => showToast(err.message || String(err), true)));
  $('new-bulletin-btn').addEventListener('click', clearBulletinForm);
  $('save-bulletin-btn').addEventListener('click', () => saveBulletin().catch((err) => showToast(err.message || String(err), true)));
  $('clear-bulletin-btn').addEventListener('click', clearBulletinForm);
  $('new-account-btn').addEventListener('click', clearAccountForm);
  $('save-account-btn').addEventListener('click', () => saveChannelAccount().catch((err) => showToast(err.message || String(err), true)));
  $('clear-account-btn').addEventListener('click', clearAccountForm);
  $('command-btn').addEventListener('click', openCommandPalette);
  $('close-command-btn').addEventListener('click', closeCommandPalette);
  $('command-search').addEventListener('input', (e) => { state.commandFilter = e.target.value || ''; renderCommandPalette(); });
  CASE_EDITOR_INPUT_IDS.forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('input', markCaseEditorDirty);
    el.addEventListener('change', markCaseEditorDirty);
  });

  $$('.nav-btn').forEach((btn) => btn.addEventListener('click', () => setView(btn.dataset.view)));
  $$('.filter-btn').forEach((btn) => btn.addEventListener('click', async () => {
    state.filterStatus = btn.dataset.status || '';
    $$('.filter-btn').forEach((b) => b.classList.toggle('active', b === btn));
    await loadCases(false);
  }));
  $('search-input').addEventListener('input', async (e) => {
    state.query = e.target.value.trim();
    await loadCases(false);
  });

  document.addEventListener('keydown', (e) => {
    const meta = e.metaKey || e.ctrlKey;
    if (meta && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      openCommandPalette();
    }
    if (e.key === 'Escape') {
      closeCommandPalette();
      closeModal();
    }
  });
}

bindEvents();
if (state.token) bootstrap().catch(console.error);
