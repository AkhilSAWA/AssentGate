"""Scoped approvals bound to a tool and the values of its sensitive arguments.

An approval only covers the exact sensitive-argument values it was granted for,
so a changed recipient, amount or document triggers reevaluation. Values are
compared after normalisation: "a@x.com" and ["A@X.com"] are the same
recipient, and list order doesn't matter. Only email addresses are compared
case-insensitively; identifiers such as document ids keep their case. Approvals
expire after a TTL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field

from assentgate.schemas import ToolCall, ToolSpec

DEFAULT_TTL = timedelta(minutes=30)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_scalar(value: Any) -> str:
    text = str(value).strip()
    return text.lower() if "@" in text else text


def normalise(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(sorted({_normalise_scalar(v) for v in value if v is not None}))
    return (_normalise_scalar(value),)


class Approval(BaseModel):
    tool: str
    bound_arguments: dict[str, Any]
    single_use: bool = True
    granted_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = None

    def expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def covers(self, call: ToolCall, spec: ToolSpec) -> bool:
        if call.tool != self.tool:
            return False
        return all(
            normalise(call.arguments.get(f)) == normalise(self.bound_arguments.get(f))
            for f in spec.sensitive_fields
        )


class ApprovalStore:
    def __init__(
        self, default_ttl: timedelta | None = DEFAULT_TTL, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self.default_ttl = default_ttl
        self.clock = clock
        self._approvals: list[Approval] = []

    def grant(
        self, call: ToolCall, spec: ToolSpec, single_use: bool = True, ttl: timedelta | None = None
    ) -> Approval:
        now = self.clock()
        ttl = ttl if ttl is not None else self.default_ttl
        approval = Approval(
            tool=call.tool,
            bound_arguments={f: call.arguments.get(f) for f in spec.sensitive_fields},
            single_use=single_use,
            granted_at=now,
            expires_at=now + ttl if ttl is not None else None,
        )
        self._approvals.append(approval)
        return approval

    def find(self, call: ToolCall, spec: ToolSpec) -> Approval | None:
        now = self.clock()
        self._approvals = [a for a in self._approvals if not a.expired(now)]
        return next((a for a in self._approvals if a.covers(call, spec)), None)

    def consume(self, approval: Approval) -> None:
        if approval.single_use and approval in self._approvals:
            self._approvals.remove(approval)

    def __len__(self) -> int:
        return len(self._approvals)
