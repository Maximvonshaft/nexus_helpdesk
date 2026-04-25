from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import User
from app.auth_service import hash_password
from app.enums import UserRole
from app.settings import get_settings


def main() -> None:
    settings = get_settings()
    if settings.app_env == 'production':
        raise SystemExit('roundb_ensure_local_admin.py is development-only and must not run in production')

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == 'admin').first()
        if not user:
            user = User(
                username='admin',
                display_name='Admin User',
                email='admin@local.test',
                password_hash=hash_password('demo123'),
                role=UserRole.admin,
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        print({
            'id': user.id,
            'username': user.username,
            'role': str(user.role),
            'is_active': user.is_active,
            'note': 'development-only helper for local Round B smoke validation',
        })
    finally:
        db.close()


if __name__ == '__main__':
    main()
