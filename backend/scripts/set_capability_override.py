from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import db_context  # noqa: E402
from app.models import User, UserCapabilityOverride  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Set per-user capability override")
    parser.add_argument("--username", required=True)
    parser.add_argument("--capability", required=True)
    parser.add_argument("--allow", choices=["true", "false"], default="true")
    args = parser.parse_args()

    with db_context() as db:
        user = db.query(User).filter(User.username == args.username).first()
        if not user:
            raise SystemExit(f"user not found: {args.username}")
        override = db.query(UserCapabilityOverride).filter(
            UserCapabilityOverride.user_id == user.id,
            UserCapabilityOverride.capability == args.capability,
        ).first()
        if not override:
            override = UserCapabilityOverride(user_id=user.id, capability=args.capability, allowed=args.allow == "true")
            db.add(override)
        else:
            override.allowed = args.allow == "true"
        db.flush()
        print(f"username={args.username} capability={args.capability} allowed={override.allowed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
