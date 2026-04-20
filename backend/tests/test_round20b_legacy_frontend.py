from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def test_round20b_legacy_frontend_removes_api_base_field_and_uses_role_hints():
    html = (PROJECT / 'frontend' / 'index.html').read_text(encoding='utf-8')
    js = (PROJECT / 'frontend' / 'app.js').read_text(encoding='utf-8')

    assert 'api-base-url' not in html
    assert 'HELPDESK_API_BASE_OVERRIDE' not in js
    assert 'sidebar-role-hint' in html
    assert 'function applyRoleAccess()' in js
    assert 'window.location.origin' in js
    assert "api('/lookups/bulletins')" in js
    assert "api('/lookups/markets')" in js


def test_round20b_legacy_frontend_keeps_customer_roles_out_of_ops_and_channel_editing():
    html = (PROJECT / 'frontend' / 'index.html').read_text(encoding='utf-8')
    js = (PROJECT / 'frontend' / 'app.js').read_text(encoding='utf-8')

    assert 'bulletin-readonly-note' in html
    assert 'account-readonly-note' in html
    assert "setHidden('workspace-ops-card', !canViewOps())" in js
    assert "setDisabledWithin('#bulletin-editor-form', !canEditBulletins())" in js
    assert "setDisabledWithin('#account-editor-form', !canManageChannels())" in js
    assert '当前账号无需查看发送线路配置' in js


def test_round20b_overview_metrics_switch_from_ops_to_customer_service_mode():
    html = (PROJECT / 'frontend' / 'index.html').read_text(encoding='utf-8')
    js = (PROJECT / 'frontend' / 'app.js').read_text(encoding='utf-8')

    assert 'overview-metric-label-1' in html
    assert "['当前工单', '处理中', '待客户回复', '高优先级', '生效公告', '已分配工单', '待分配工单', '已解决']" in js
    assert "['待发送消息', '待处理任务', '异常任务', '已绑定会话', '待补同步', '待执行同步任务', '附件任务', '渠道设置']" in js



def test_round20b_legacy_frontend_avoids_innerhtml_rendering_paths():
    js = (PROJECT / 'frontend' / 'app.js').read_text(encoding='utf-8')

    assert '.innerHTML' not in js
    assert 'replaceNodeChildren' in js
    assert 'createNode(' in js
