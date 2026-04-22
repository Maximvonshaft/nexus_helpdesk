from __future__ import annotations

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PY_FILES = [
    ROOT / 'backend/app/models_control_plane.py',
    ROOT / 'backend/app/services/persona_service.py',
    ROOT / 'backend/app/services/knowledge_service.py',
    ROOT / 'backend/app/services/channel_control_service.py',
    ROOT / 'backend/app/api/persona_profiles.py',
    ROOT / 'backend/app/api/knowledge_items.py',
    ROOT / 'backend/app/api/channel_control.py',
    ROOT / 'backend/app/main.py',
    ROOT / 'backend/alembic/versions/20260422_control_plane_overlay_round1.py',
]

TEXT_ASSERTIONS = {
    ROOT / 'backend/app/main.py': [
        'persona_profiles_router',
        'knowledge_items_router',
        'channel_control_router',
    ],
    ROOT / 'webapp/src/router.tsx': [
        'PersonasRoute',
        'KnowledgeRoute',
    ],
    ROOT / 'webapp/src/routes/accounts.tsx': [
        'routeExplainMutation',
        'onboarding',
    ],
    ROOT / 'backend/app/api/persona_profiles.py': [
        '/api/admin/persona-profiles',
        'resolve-preview',
    ],
    ROOT / 'backend/app/api/knowledge_items.py': [
        '/api/admin/knowledge-items',
        '/upload',
    ],
    ROOT / 'backend/app/api/channel_control.py': [
        '/api/admin/channel-control',
        'onboarding-tasks',
    ],
}


def main() -> int:
    for path in PY_FILES:
        py_compile.compile(str(path), doraise=True)
        print(f'PY_COMPILE_OK {path.relative_to(ROOT)}')

    for path, needles in TEXT_ASSERTIONS.items():
        content = path.read_text(encoding='utf-8')
        for needle in needles:
            if needle not in content:
                raise SystemExit(f'MISSING_TEXT {needle} in {path.relative_to(ROOT)}')
        print(f'TEXT_ASSERT_OK {path.relative_to(ROOT)} {len(needles)} markers')

    print('SMOKE_OK control_plane_overlay')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
