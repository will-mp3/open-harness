from __future__ import annotations

from open_harness.schema.request import ModelMessage
from open_harness.schema.session import Session


def to_model_messages(session: Session) -> list[ModelMessage]:
  messages: list[ModelMessage] = []

  for message in session.messages:
    messages.append(
      {
        "role": message.info.role,
        "content": message.joined_text(),
      }
    )

  return messages
