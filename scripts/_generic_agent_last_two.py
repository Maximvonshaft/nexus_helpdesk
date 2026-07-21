from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text.rstrip() + "\n", encoding="utf-8")


speedaf_path = "backend/tests/test_speedaf_tool_governance.py"
speedaf = read(speedaf_path)
speedaf = speedaf.replace(
    "speedaf.order.cancel.request.request",
    "speedaf.order.cancel.request",
)
speedaf = speedaf.replace(
    "speedaf.order.updateAddress.request.request",
    "speedaf.order.updateAddress.request",
)
write(speedaf_path, speedaf)

compensation_path = "backend/tests/test_webchat_voice_room_compensation.py"
compensation = read(compensation_path)
compensation = compensation.replace(
    "from app.db import Base, engine\n",
    "from app.db import Base, SessionLocal, engine\n"
    "from app.models_agent_routing import ConversationControl\n"
    "from app.webchat_models import WebchatConversation\n",
    1,
)
compensation = compensation.replace(
    "    conversation_id, visitor_token = _create_webchat_conversation(client)\n\n"
    "    response = client.post(\n",
    "    conversation_id, visitor_token = _create_webchat_conversation(client)\n"
    "    db = SessionLocal()\n"
    "    try:\n"
    "        conversation = db.query(WebchatConversation).filter(\n"
    "            WebchatConversation.public_id == conversation_id\n"
    "        ).one()\n"
    "        control = db.query(ConversationControl).filter(\n"
    "            ConversationControl.conversation_id == conversation.id\n"
    "        ).one()\n"
    "        control.country_code = \"ME\"\n"
    "        db.commit()\n"
    "    finally:\n"
    "        db.close()\n\n"
    "    response = client.post(\n",
    1,
)
write(compensation_path, compensation)
