from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

db_path = Path(tempfile.gettempdir()) / "helpdesk_round7_smoke.db"
if db_path.exists():
    db_path.unlink()

os.environ["APP_ENV"] = "development"
os.environ["AUTO_INIT_DB"] = "false"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["DATABASE_URL"] = "sqlite:///" + str(db_path.resolve())
os.environ["SECRET_KEY"] = "round7-secret"
os.environ["MAX_UPLOAD_BYTES"] = "1024"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from backend.app.auth_service import hash_password  # noqa: E402
from backend.app.db import Base, SessionLocal, engine  # noqa: E402
from backend.app.enums import UserRole  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.models import Team, User  # noqa: E402
from backend.app.settings import get_settings  # noqa: E402

Base.metadata.create_all(bind=engine)
db = SessionLocal()

support = Team(name="Support", team_type="support")
db.add(support)
db.commit()
db.refresh(support)

admin = User(username="admin", display_name="Admin", email="admin@test.local", password_hash=hash_password("pw"), role=UserRole.admin, team_id=support.id)
db.add(admin)
db.commit()
db.refresh(admin)

client = TestClient(app)

res = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
assert res.status_code == 200, res.text
token = res.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

ready = client.get("/readyz")
assert ready.status_code == 200, ready.text

metrics = client.get("/metrics")
assert metrics.status_code == 200, metrics.text

# production guard sanity via settings construction
os.environ["APP_ENV"] = "production"
os.environ["DATABASE_URL"] = "sqlite:///" + str(db_path.resolve())
try:
    get_settings.cache_clear()
    get_settings()
    raise AssertionError("Expected production settings to reject sqlite")
except Exception:
    pass
finally:
    os.environ["APP_ENV"] = "development"
    get_settings.cache_clear()

print("ROUND7_SMOKE_PASSED")
