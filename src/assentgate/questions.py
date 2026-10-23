"""Template-based confirmation questions, one per unresolved field."""

from __future__ import annotations

from assentgate.policy import RECIPIENT_FIELDS
from assentgate.schemas import ToolCall

_TEMPLATES = {
    "file_id": "May '{tool}' act on file '{value}'?",
    "email_id": "May '{tool}' act on email '{value}'?",
    "event_id": "May '{tool}' act on calendar event '{value}'?",
    "attachments": "May '{tool}' attach {value}?",
    "permission": "May '{tool}' grant '{value}' permission?",
    "visibility": "May the agent set visibility to '{value}'?",
}
_RECIPIENT_TEMPLATE = "May '{tool}' send to recipient '{value}'?"
_DEFAULT_TEMPLATE = "May '{tool}' proceed with {field} = '{value}'?"


def _display(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def confirmation_questions(call: ToolCall, fields: list[str]) -> list[str]:
    if not fields:  # the action itself, not a value, is in doubt
        return [f"May the agent run '{call.tool}'? Your request doesn't appear to ask for this action."]
    questions = []
    for field in fields:
        value = _display(call.arguments.get(field))
        if field in RECIPIENT_FIELDS:
            template = _RECIPIENT_TEMPLATE
        else:
            template = _TEMPLATES.get(field, _DEFAULT_TEMPLATE)
        questions.append(template.format(tool=call.tool, field=field, value=value))
    return questions
