from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def test_round20a_webapp_keeps_support_workbench_routes_only():
    routes_dir = PROJECT / 'webapp' / 'src' / 'routes'
    routes = sorted(path.name for path in routes_dir.glob('*.tsx'))
    assert routes == ['index.tsx', 'login.tsx', 'root.tsx', 'webchat.tsx']


def test_round20a_legacy_copy_keeps_bound_source_status():
    legacy = (PROJECT / 'frontend' / 'app.js').read_text(encoding='utf-8')
    assert '会话编号' not in legacy
    assert '已绑定来信来源' in legacy
