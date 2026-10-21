"""Tool specs for AgentDojo's workspace suite: email, calendar and cloud drive.

Names and arguments match AgentDojo's tools (checked at commit 089ed468), and
sensitive fields match SENSITIVE_FIELDS in scripts/build_agentdojo_dataset.py,
so gate inputs line up with the dataset. The in-memory handlers are sandbox
stand-ins for demos and tests; the real evaluation runs inside AgentDojo.
"""

from __future__ import annotations

from assentgate.schemas import ToolSpec


def _read(name: str, description: str, required=(), optional=()) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        required_args=list(required),
        optional_args=list(optional),
    )


def _write(name: str, description: str, required=(), optional=(), sensitive=()) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        required_args=list(required),
        optional_args=list(optional),
        sensitive_fields=list(sensitive),
        side_effecting=True,
    )


WORKSPACE_TOOLS = [
    # Email
    _read("get_unread_emails", "List unread emails."),
    _read("get_received_emails", "List received emails."),
    _read("get_sent_emails", "List sent emails."),
    _read("get_draft_emails", "List draft emails."),
    _read("search_emails", "Search emails.", ["query"], ["sender"]),
    _read("search_contacts_by_name", "Search contacts by name.", ["query"]),
    _read("search_contacts_by_email", "Search contacts by email.", ["query"]),
    _write(
        "send_email",
        "Send an email.",
        ["recipients", "subject", "body"],
        ["attachments", "cc", "bcc"],
        ["recipients", "cc", "bcc", "attachments"],
    ),
    _write("delete_email", "Delete an email.", ["email_id"], sensitive=["email_id"]),
    # Calendar
    _read("get_current_day", "Get today's date."),
    _read("get_day_calendar_events", "List events on a day.", ["day"]),
    _read("search_calendar_events", "Search calendar events.", ["query"], ["date"]),
    _write(
        "create_calendar_event",
        "Create a calendar event.",
        ["title", "start_time", "end_time"],
        ["description", "participants", "location"],
        ["participants"],
    ),
    _write("cancel_calendar_event", "Cancel an event.", ["event_id"], sensitive=["event_id"]),
    _write(
        "reschedule_calendar_event",
        "Reschedule an event.",
        ["event_id", "new_start_time"],
        ["new_end_time"],
        ["event_id"],
    ),
    _write(
        "add_calendar_event_participants",
        "Add participants to an event.",
        ["event_id", "participants"],
        sensitive=["event_id", "participants"],
    ),
    # Cloud drive
    _read("list_files", "List files."),
    _read("search_files", "Search file contents.", ["query"]),
    _read("search_files_by_filename", "Search files by name.", ["filename"]),
    _read("get_file_by_id", "Read a file.", ["file_id"]),
    _write("create_file", "Create a file.", ["filename", "content"]),
    _write("append_to_file", "Append to a file.", ["file_id", "content"], sensitive=["file_id"]),
    _write("delete_file", "Delete a file.", ["file_id"], sensitive=["file_id"]),
    _write(
        "share_file",
        "Share a file with someone.",
        ["file_id", "email", "permission"],
        sensitive=["file_id", "email", "permission"],
    ),
]


class FakeWorkspace:
    """In-memory stand-in implementing the subset of tools the tests and demo use."""

    def __init__(self) -> None:
        self.emails = {
            "1": {
                "id_": "1",
                "sender": "noreply@airline.example",
                "subject": "Your flight",
                "body": "Flight AB123 departs 2026-11-02 09:00.",
            }
        }
        self.files = {"13": {"id_": "13", "filename": "budget.xlsx", "content": "..."}}
        self.sent: list[dict] = []
        self.events: list[dict] = []
        self.shares: list[dict] = []

    def search_emails(self, query: str, sender: str | None = None) -> list[dict]:
        return [
            e
            for e in self.emails.values()
            if query.lower() in (e["subject"] + e["body"]).lower()
            and (sender is None or e["sender"] == sender)
        ]

    def get_unread_emails(self) -> list[dict]:
        return list(self.emails.values())

    def send_email(self, recipients, subject, body, attachments=None, cc=None, bcc=None) -> dict:
        email = {"recipients": recipients, "subject": subject, "body": body}
        self.sent.append(email)
        return email

    def delete_email(self, email_id: str) -> str:
        self.emails.pop(email_id)
        return f"Email {email_id} deleted."

    def create_calendar_event(
        self, title, start_time, end_time, description="", participants=None, location=None
    ) -> dict:
        event = {"id_": str(len(self.events)), "title": title, "start_time": start_time}
        self.events.append(event)
        return event

    def get_file_by_id(self, file_id: str) -> dict:
        return self.files[file_id]

    def share_file(self, file_id: str, email: str, permission: str) -> dict:
        share = {"file_id": file_id, "email": email, "permission": permission}
        self.shares.append(share)
        return share

    def delete_file(self, file_id: str) -> str:
        self.files.pop(file_id)
        return f"File {file_id} deleted."

    def handlers(self) -> dict:
        return {
            spec.name: getattr(self, spec.name)
            for spec in WORKSPACE_TOOLS
            if hasattr(self, spec.name)
        }
