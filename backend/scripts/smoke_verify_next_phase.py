from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN = (ROOT / 'backend' / 'app' / 'api' / 'admin.py').read_text(encoding='utf-8')
LOOKUPS = (ROOT / 'backend' / 'app' / 'api' / 'lookups.py').read_text(encoding='utf-8')
ROUTER = (ROOT / 'webapp' / 'src' / 'router.tsx').read_text(encoding='utf-8')
ROUTE = (ROOT / 'webapp' / 'src' / 'routes' / 'ai-control.tsx').read_text(encoding='utf-8')

assert "/ai-configs" in ADMIN
assert "/ai-configs" in LOOKUPS
assert "AIControlRoute" in ROUTER
assert "智能助手规则与知识配置" in ROUTE
print('next phase max push smoke verification passed')
