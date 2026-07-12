from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("apply_frontend_truth.py")
text = path.read_text(encoding="utf-8")

import_needle = '''        "  healthPresentation,\\n"
        "  runtimePresentation,\\n"'''
import_replacement = '''        "  healthPresentation,\\n"
        "  knowledgeStatusPresentation,\\n"
        "  runtimePresentation,\\n"'''
if text.count(import_needle) != 1:
    raise RuntimeError("knowledge presentation import insertion point changed")
text = text.replace(import_needle, import_replacement, 1)

insert_needle = '''    text = replace_once(
        text,
        "<span>{stateLabel(activeConversation)}</span>",
        "<span>{sourceConversationPresentation(activeConversation).label}</span>",
        "thread source status",
    )

    forbidden = ["toneForHealth", "toneForChannel", "toneForConversation", "stateLabel(", "已结束"]'''
insert_replacement = '''    text = replace_once(
        text,
        "<span>{stateLabel(activeConversation)}</span>",
        "<span>{sourceConversationPresentation(activeConversation).label}</span>",
        "thread source status",
    )
    text = replace_once(
        text,
        "<Badge tone={toneForHealth(item.status)}>{knowledgeStatusLabel(item.status)}</Badge>",
        "<Badge tone={knowledgeStatusPresentation(item.status).tone}>{knowledgeStatusPresentation(item.status).label}</Badge>",
        "knowledge item lifecycle tone",
    )
    text = replace_once(
        text,
        "<Badge tone={toneForHealth(draft.status)}>{knowledgeStatusLabel(draft.status)}</Badge>",
        "<Badge tone={knowledgeStatusPresentation(draft.status).tone}>{knowledgeStatusPresentation(draft.status).label}</Badge>",
        "knowledge editor lifecycle tone",
    )

    forbidden = ["toneForHealth", "toneForChannel", "toneForConversation", "stateLabel(", "已结束"]'''
if text.count(insert_needle) != 1:
    raise RuntimeError("knowledge lifecycle replacement insertion point changed")
text = text.replace(insert_needle, insert_replacement, 1)

path.write_text(text, encoding="utf-8")
print("frontend truth codemod patched for knowledge lifecycle")
