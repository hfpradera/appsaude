from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AiConversation
from app.services.ai_chat import conversation_messages


def maybe_update_summary(db: Session, conversation: AiConversation) -> None:
    limit = get_settings().ai_conversation_summary_message_limit
    messages = conversation_messages(db, conversation.id)
    if len(messages) < limit:
        return
    facts = []
    for message in messages[-limit:]:
        if message.role == "user":
            facts.append(message.content[:120])
    if facts:
        conversation.summary = " | ".join(facts[-8:])
        db.add(conversation)
